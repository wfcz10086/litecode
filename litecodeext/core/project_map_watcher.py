#!/usr/bin/env python3
"""
project_map_watcher.py — 自动热更新 PROJECT_MAP.md
=====================================================
完全 LLM 无关。纯 AST/正则静态分析，轮询文件变更。

设计目标:
  1. LLM-independent — 不依赖模型调用 update_map
  2. 热更新 — 文件写入后 3s 内 PROJECT_MAP.md 自动更新
  3. 原子写入 — tmp → rename，不污染已有内容
  4. 增量更新 — 只重扫已变更的文件
  5. 多项目支持 — 一个 Watcher 实例管理多个 project_root
  6. 长文模式 — 小说/文档 → 章节字数统计而非 AST

集成方式:
  from project_map_watcher import ProjectMapWatcher, get_global_watcher
  watcher = get_global_watcher()
  watcher.register(project_root="/tmp/my_project")
  # watcher 在 server 启动时作为 asyncio task 后台运行

PROJECT_MAP.md 格式:
  ## 目录结构     — 自动生成目录树
  ## {filename}   — 每个源文件一个 section
     ### Routes   — FastAPI/Flask/Gin 路由
     ### Classes  — 类列表（含 base、docstring）
     ### Functions— 函数列表（含签名、行号）
     ### Constants— 模块级常量
  ## 章节大纲     — 小说/文档模式
  ## 测试覆盖     — 测试文件映射
  ## 变更日志     — 最近50条变更
"""

import ast
import asyncio
import hashlib
import json
import os
import re
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── 拆分后的子模块 (re-export, 保持 project_map_watcher.X 可访问) ──
# 数据结构/解析器/工具函数 → project_map_parsers.py; 渲染器 → project_map_render.py
try:
    from .project_map_parsers import (  # noqa: F401
        _CODE_EXTS, _WRITING_EXTS, _CONFIG_EXTS, _SKIP_DIRS, _SKIP_FILES,
        RouteInfo, FuncInfo, ClassInfo, FileInfo,
        PythonASTParser, RegexParser, WritingParser, ConfigParser,
        _md5, _now, _count_lines, _build_tree_str,
    )
    from .project_map_render import MapRenderer  # noqa: F401
except ImportError:  # core/ 扁平导入 或 脚本直跑
    from project_map_parsers import (  # noqa: F401
        _CODE_EXTS, _WRITING_EXTS, _CONFIG_EXTS, _SKIP_DIRS, _SKIP_FILES,
        RouteInfo, FuncInfo, ClassInfo, FileInfo,
        PythonASTParser, RegexParser, WritingParser, ConfigParser,
        _md5, _now, _count_lines, _build_tree_str,
    )
    from project_map_render import MapRenderer  # noqa: F401

# ── 轮询间隔 ──────────────────────────────────────────────────
POLL_INTERVAL = 3.0        # 秒
DEBOUNCE_DELAY = 1.5       # 文件写完后等待 1.5s 再扫描（防止写到一半）
MAX_DEBOUNCE_DELAY = 30.0  # 持续编辑也最迟 30s 扫一次
MAX_FILE_SIZE = 500 * 1024 # 跳过 >500KB 的文件

# ── 全局实例 ──────────────────────────────────────────────────
_global_watcher: Optional["ProjectMapWatcher"] = None
_watcher_lock = threading.Lock()


def get_global_watcher() -> "ProjectMapWatcher":
    global _global_watcher
    with _watcher_lock:
        if _global_watcher is None:
            _global_watcher = ProjectMapWatcher()
    return _global_watcher


# ─────────────────────────────────────────────────────────────
# ignore 支持
# ─────────────────────────────────────────────────────────────

def _load_ignore_patterns(root: Path) -> List[str]:
    patterns: List[str] = []
    for fname in (".gitignore", ".projectmapignore"):
        fpath = root / fname
        if not fpath.exists():
            continue
        try:
            for line in fpath.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
        except Exception:
            pass
    return patterns


def _matches_ignore(rel_path: str, patterns: List[str]) -> bool:
    import fnmatch
    parts = rel_path.replace("\\", "/").split("/")
    for p in patterns:
        if p.endswith("/"):
            dir_pat = p.rstrip("/")
            if any(fnmatch.fnmatch(part, dir_pat) for part in parts[:-1]):
                return True
            if parts and fnmatch.fnmatch(parts[0], dir_pat):
                return True
            continue
        if fnmatch.fnmatch(rel_path, p):
            return True
        if fnmatch.fnmatch(rel_path, "*/" + p):
            return True
        if any(fnmatch.fnmatch(part, p) for part in parts):
            return True
    return False


# ─────────────────────────────────────────────────────────────
# 主 Watcher 类
# ─────────────────────────────────────────────────────────────

class ProjectMapWatcher:
    """
    后台异步 Watcher，监控项目文件变更并热更新 PROJECT_MAP.md

    使用方法:
        watcher = ProjectMapWatcher()
        watcher.register("/tmp/my_project")
        # 在 asyncio 事件循环中:
        asyncio.create_task(watcher.run())
    """

    def __init__(self, poll_interval: float = POLL_INTERVAL):
        self.poll_interval = poll_interval
        # {project_root_str: {relpath: file_hash}}
        self._watched: Dict[str, Dict[str, str]] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._py_parser = PythonASTParser()
        self._regex_parser = RegexParser()
        self._writing_parser = WritingParser()
        self._config_parser = ConfigParser()
        self._renderer = MapRenderer()
        # 手动注册的 pending 更新（server 写文件后立即触发）
        self._pending: Dict[str, Tuple[float, float]] = {}  # {root: (first_ts, last_ts)}
        self._ignore_cache: Dict[str, List[str]] = {}  # {root_str: patterns}

    def register(self, project_root: str):
        """注册一个项目目录进行监控"""
        root = str(Path(project_root).resolve())
        if root not in self._watched:
            self._watched[root] = {}
        now = time.time()
        self._pending[root] = (now, now)  # 立即触发首次全量扫描
        self._ignore_cache[root] = _load_ignore_patterns(Path(root))

    def unregister(self, project_root: str):
        root = str(Path(project_root).resolve())
        self._watched.pop(root, None)
        self._pending.pop(root, None)
        self._ignore_cache.pop(root, None)

    def notify_file_changed(self, filepath: str):
        """
        当 server 的 write_file/patch_file 写入文件时调用此方法，
        触发对应项目的 debounced 更新
        """
        p = Path(filepath).resolve()
        now = time.time()
        for root_str in list(self._watched.keys()):
            root = Path(root_str)
            try:
                p.relative_to(root)
                existing = self._pending.get(root_str)
                if existing is None:
                    self._pending[root_str] = (now, now)
                else:
                    self._pending[root_str] = (existing[0], now)
            except ValueError:
                continue

    async def run(self):
        """主循环，作为 asyncio.Task 运行"""
        self._running = True
        import logging
        log = logging.getLogger("openclaw.watcher")
        log.info("[MapWatcher] started")
        while self._running:
            try:
                await self._poll_all()
            except Exception as e:
                log.warning(f"[MapWatcher] error: {e}")
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False

    async def _poll_all(self):
        """轮询所有注册的项目"""
        now = time.time()
        for root_str in list(self._watched.keys()):
            root = Path(root_str)
            if not root.exists():
                continue
            pending = self._pending.get(root_str)
            if pending is not None:
                first_ts, last_ts = pending
                idle_enough = (now - last_ts) >= DEBOUNCE_DELAY
                too_long = (now - first_ts) >= MAX_DEBOUNCE_DELAY
                if idle_enough or too_long:
                    self._pending.pop(root_str, None)
                    await self._scan_project(root)
            else:
                # 常规轮询：检查是否有文件变更
                changed = await self._detect_changes(root)
                if changed:
                    await self._scan_project(root)

    async def _detect_changes(self, root: Path) -> bool:
        """检测文件是否变更（基于 mtime hash）"""
        known = self._watched.get(str(root), {})
        loop = asyncio.get_event_loop()
        changed = await loop.run_in_executor(None, self._check_mtimes, root, known)
        return changed

    def _check_mtimes(self, root: Path, known: Dict[str, str]) -> bool:
        """同步版本，在 executor 中运行"""
        ignore_patterns = self._ignore_cache.get(str(root), [])
        for fpath in root.rglob("*"):
            if not fpath.is_file():
                continue
            if any(part in _SKIP_DIRS for part in fpath.parts):
                continue
            if fpath.name in _SKIP_FILES:
                continue
            if fpath.suffix not in _CODE_EXTS | _WRITING_EXTS | _CONFIG_EXTS:
                continue
            if fpath.stat().st_size > MAX_FILE_SIZE:
                continue
            rel = str(fpath.relative_to(root))
            if ignore_patterns and _matches_ignore(rel, ignore_patterns):
                continue
            try:
                mtime_hash = str(fpath.stat().st_mtime)
                if known.get(rel) != mtime_hash:
                    return True
            except OSError:
                continue
        return False

    async def _scan_project(self, root: Path):
        """扫描整个项目，更新 PROJECT_MAP.md"""
        import logging
        log = logging.getLogger("openclaw.watcher")

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self._scan_sync, root)
            if result:
                file_infos, new_hashes = result
                self._watched[str(root)] = new_hashes
                await self._write_map(root, file_infos)
                log.info(f"[MapWatcher] updated PROJECT_MAP.md: {root.name} "
                         f"({len(file_infos)} files)")
        except Exception as e:
            log.warning(f"[MapWatcher] scan failed for {root}: {e}")

    def _scan_sync(self, root: Path) -> Optional[Tuple[Dict[str, FileInfo], Dict[str, str]]]:
        """同步扫描，在 executor 中运行"""
        file_infos: Dict[str, FileInfo] = {}
        new_hashes: Dict[str, str] = {}
        known = self._watched.get(str(root), {})
        ignore_patterns = self._ignore_cache.get(str(root), [])

        for fpath in sorted(root.rglob("*")):
            if not fpath.is_file():
                continue
            if any(part in _SKIP_DIRS for part in fpath.parts):
                continue
            if fpath.name in _SKIP_FILES:
                continue
            if fpath.suffix not in _CODE_EXTS | _WRITING_EXTS | _CONFIG_EXTS:
                continue
            if fpath.stat().st_size > MAX_FILE_SIZE:
                continue

            rel = str(fpath.relative_to(root))
            if ignore_patterns and _matches_ignore(rel, ignore_patterns):
                continue

            try:
                mtime_hash = str(fpath.stat().st_mtime)
                new_hashes[rel] = mtime_hash

                source = fpath.read_text(encoding="utf-8", errors="replace")
                ext = fpath.suffix.lower()

                if ext == ".py":
                    fi = self._py_parser.parse(source, rel)
                elif ext in _WRITING_EXTS:
                    fi = self._writing_parser.parse(source, rel)
                elif ext in _CONFIG_EXTS:
                    fi = self._config_parser.parse(source, rel)
                elif ext in self._regex_parser._PATTERNS:
                    fi = self._regex_parser.parse(source, rel, ext)
                else:
                    fi = FileInfo(path=rel, lines=source.count("\n"),
                                  hash=_md5(source), language=ext.lstrip("."))

                file_infos[rel] = fi

            except Exception:
                continue

        return file_infos, new_hashes

    async def _write_map(self, root: Path, file_infos: Dict[str, FileInfo]):
        """原子写入 PROJECT_MAP.md"""
        map_path = root / "PROJECT_MAP.md"
        existing = ""
        if map_path.exists():
            try:
                existing = map_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

        content = self._renderer.render_full(root, file_infos, existing)

        # 原子写入
        tmp = map_path.with_suffix(".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(map_path)
        except Exception as e:
            import logging
            logging.getLogger("openclaw.watcher").warning(
                f"[MapWatcher] write failed: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

        # Write SYMBOL_INDEX.json
        symbol_index = {"_meta": {"updated": _now(), "n_files": len(file_infos)}, "symbols": {}}
        for relpath, info in file_infos.items():
            for fn in info.funcs:
                symbol_index["symbols"].setdefault(fn.name, []).append({
                    "file": relpath, "line": fn.line_no, "type": "func",
                    "params": fn.params, "is_async": fn.is_async,
                })
            for cls in info.classes:
                symbol_index["symbols"].setdefault(cls.name, []).append({
                    "file": relpath, "line": cls.line_no, "type": "class",
                    "bases": cls.bases, "methods": cls.methods[:10],
                })
        sym_path = root / "SYMBOL_INDEX.json"
        sym_tmp = sym_path.with_suffix(".tmp")
        try:
            sym_tmp.write_text(json.dumps(symbol_index, ensure_ascii=False, indent=1))
            sym_tmp.replace(sym_path)
        except Exception as e:
            import logging
            logging.getLogger("openclaw.watcher").warning(
                f"[MapWatcher] symbol index write failed: {e}")
            try:
                sym_tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def force_update(self, project_root: str):
        """强制立即更新（同步版，用于 server 启动时）"""
        root = Path(project_root).resolve()
        self.register(str(root))
        # 设置 first_ts 足够早，下次 poll 立即触发
        past = time.time() - MAX_DEBOUNCE_DELAY - 1.0
        self._pending[str(root)] = (past, past)

    async def get_summary(self, project_root: str) -> dict:
        """返回项目摘要（给 server /stats 等接口用）"""
        root = Path(project_root).resolve()
        map_path = root / "PROJECT_MAP.md"
        if not map_path.exists():
            return {"status": "not_generated", "root": str(root)}
        mtime = map_path.stat().st_mtime
        size = map_path.stat().st_size
        known = self._watched.get(str(root), {})
        return {
            "status": "ok",
            "root": str(root),
            "map_path": str(map_path),
            "map_size": size,
            "map_updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
            "tracked_files": len(known),
            "pending": self._pending.get(str(root)) is not None,
        }


# ─────────────────────────────────────────────────────────────
# server 集成补丁（在 litecode_server.py 中调用）
# ─────────────────────────────────────────────────────────────

def patch_server_integration(server_module):
    """
    把 MapWatcher 集成进现有 server，热更新文件写入通知。

    在 litecode_server.py 的 execute_tool() 的 write_file/patch_file 分支末尾调用:
        from project_map_watcher import get_global_watcher
        get_global_watcher().notify_file_changed(str(p))

    在 server 启动时:
        asyncio.create_task(get_global_watcher().run())
    """
    pass  # 文档用，实际集成见下方 hook 函数


def write_file_hook(filepath: str):
    """server 的 write_file/patch_file 工具执行后调用此 hook"""
    try:
        get_global_watcher().notify_file_changed(filepath)
    except Exception:
        pass


def register_project_hook(project_root: str):
    """server 检测到新 project_root 时调用"""
    try:
        get_global_watcher().register(project_root)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# CLI 独立运行
# ─────────────────────────────────────────────────────────────

async def _cli_main():
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 project_map_watcher.py <project_root> [--watch]")
        print("  --watch  持续监控（否则一次性生成）")
        sys.exit(1)

    root = Path(sys.argv[1]).resolve()
    watch_mode = "--watch" in sys.argv

    print(f"[MapWatcher] 扫描: {root}")
    watcher = ProjectMapWatcher(poll_interval=2.0)
    watcher.register(str(root))

    if watch_mode:
        print(f"[MapWatcher] 持续监控中 (Ctrl+C 退出)...")
        try:
            await watcher.run()
        except KeyboardInterrupt:
            print("\n[MapWatcher] 停止")
    else:
        # 一次性生成
        result = watcher._scan_sync(root)
        if result:
            file_infos, hashes = result
            await watcher._write_map(root, file_infos)
            map_path = root / "PROJECT_MAP.md"
            print(f"[MapWatcher] 生成完成: {map_path}")
            print(f"  文件数: {len(file_infos)}")
            py_count = sum(1 for fi in file_infos.values() if fi.language == "python")
            func_count = sum(len(fi.funcs) for fi in file_infos.values())
            route_count = sum(len(fi.routes) for fi in file_infos.values())
            print(f"  Python: {py_count} 个，函数: {func_count} 个，路由: {route_count} 个")
        else:
            print("[MapWatcher] 扫描失败或目录为空")


if __name__ == "__main__":
    asyncio.run(_cli_main())
