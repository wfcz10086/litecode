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

# ── 支持语言 ──────────────────────────────────────────────────
_CODE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
              ".rb", ".php", ".c", ".cpp", ".h", ".vue", ".svelte", ".sh"}
_WRITING_EXTS = {".md", ".txt", ".rst", ".adoc"}
_CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml"}
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
              "dist", "build", ".next", "target", "vendor", ".mypy_cache",
              ".pytest_cache", ".openclaw_checkpoints"}
_SKIP_FILES = {"PROJECT_MAP.md", "PROJECT_MAP_BRIEF.md", "CALL_GRAPH.json",
               ".manifest_hashes.json", "MEMORY.md", "SYMBOL_INDEX.json"}

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
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class RouteInfo:
    method: str
    path: str
    handler: str
    params: str = ""
    doc: str = ""

@dataclass
class FuncInfo:
    name: str
    line_no: int
    params: str
    returns: str = ""
    doc: str = ""
    is_async: bool = False
    decorators: List[str] = field(default_factory=list)

@dataclass
class ClassInfo:
    name: str
    line_no: int
    bases: str = ""
    methods: List[str] = field(default_factory=list)
    doc: str = ""

@dataclass
class FileInfo:
    path: str           # 相对于 project_root
    lines: int
    hash: str
    language: str
    routes: List[RouteInfo] = field(default_factory=list)
    funcs: List[FuncInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    constants: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    # 写作模式
    word_count: int = 0
    headings: List[Tuple[int, str]] = field(default_factory=list)  # (level, title)


# ─────────────────────────────────────────────────────────────
# Python AST 解析器（最精确）
# ─────────────────────────────────────────────────────────────

class PythonASTParser:
    """使用 ast 模块精确提取 Python 符号"""

    # FastAPI/Flask/Django 路由装饰器模式
    _ROUTE_DECORATORS = re.compile(
        r"(app|router|bp|blueprint|api)\.(get|post|put|delete|patch|head|options|route|websocket)\s*\(\s*['\"]([^'\"]+)['\"]",
        re.I
    )

    def parse(self, source: str, filepath: str) -> FileInfo:
        lines = source.splitlines()
        line_count = len(lines)
        info = FileInfo(
            path=filepath, lines=line_count,
            hash=_md5(source), language="python"
        )
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return info

        info.constants = self._extract_constants(tree)
        info.imports = self._extract_imports(tree)
        info.routes = self._extract_routes(tree, source)
        info.classes = self._extract_classes(tree)
        info.funcs = self._extract_funcs(tree)
        return info

    def _extract_constants(self, tree: ast.AST) -> List[str]:
        consts = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        consts.append(target.id)
            elif isinstance(node, (ast.AnnAssign,)) and isinstance(node.target, ast.Name):
                if node.target.id.isupper():
                    consts.append(node.target.id)
        return consts[:20]

    def _extract_imports(self, tree: ast.AST) -> List[str]:
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
        # 去重，保留前10
        seen = set()
        result = []
        for imp in imports:
            if imp not in seen:
                seen.add(imp)
                result.append(imp)
            if len(result) >= 10:
                break
        return result

    def _extract_routes(self, tree: ast.AST, source: str) -> List[RouteInfo]:
        routes = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                route = self._parse_route_decorator(dec, node)
                if route:
                    routes.append(route)
        return routes

    def _parse_route_decorator(self, dec: ast.expr, func: ast.FunctionDef) -> Optional[RouteInfo]:
        # @app.get("/path") or @router.post("/path")
        if not isinstance(dec, ast.Call):
            return None
        func_node = dec.func
        if not isinstance(func_node, ast.Attribute):
            return None
        method = func_node.attr.upper()
        if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD",
                           "OPTIONS", "ROUTE", "WEBSOCKET"}:
            return None
        if not dec.args:
            return None
        path_node = dec.args[0]
        if not isinstance(path_node, ast.Constant):
            return None
        path = str(path_node.value)
        # 提取参数
        params = ", ".join(
            f"{arg.arg}: {ast.unparse(arg.annotation) if arg.annotation else '?'}"
            for arg in func.args.args
            if arg.arg not in ("self", "cls", "request", "req", "db", "background_tasks")
        )
        doc = ast.get_docstring(func) or ""
        return RouteInfo(
            method=method, path=path,
            handler=func.name,
            params=params[:80],
            doc=doc.split("\n")[0][:60] if doc else ""
        )

    def _extract_classes(self, tree: ast.AST) -> List[ClassInfo]:
        classes = []
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = ", ".join(
                ast.unparse(b) for b in node.bases
            )[:60]
            methods = []
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(item.name)
            doc = ast.get_docstring(node) or ""
            classes.append(ClassInfo(
                name=node.name,
                line_no=node.lineno,
                bases=bases,
                methods=methods[:12],
                doc=doc.split("\n")[0][:80] if doc else ""
            ))
        return classes

    def _extract_funcs(self, tree: ast.AST) -> List[FuncInfo]:
        funcs = []
        # 只提取顶层函数（不含类方法）
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = self._fmt_args(node.args)
            returns = ""
            if node.returns:
                try:
                    returns = ast.unparse(node.returns)[:40]
                except Exception:
                    pass
            doc = ast.get_docstring(node) or ""
            decs = []
            for d in node.decorator_list:
                try:
                    decs.append(ast.unparse(d)[:40])
                except Exception:
                    pass
            funcs.append(FuncInfo(
                name=node.name,
                line_no=node.lineno,
                params=params[:100],
                returns=returns,
                doc=doc.split("\n")[0][:80] if doc else "",
                is_async=isinstance(node, ast.AsyncFunctionDef),
                decorators=decs[:3]
            ))
        return funcs

    def _fmt_args(self, args: ast.arguments) -> str:
        parts = []
        # positional
        for arg in args.args:
            if arg.arg in ("self", "cls"):
                continue
            ann = f": {ast.unparse(arg.annotation)}" if arg.annotation else ""
            try:
                parts.append(f"{arg.arg}{ann}")
            except Exception:
                parts.append(arg.arg)
        # *args
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        # **kwargs
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")
        return ", ".join(parts)


# ─────────────────────────────────────────────────────────────
# 多语言正则解析器
# ─────────────────────────────────────────────────────────────

class RegexParser:
    """JS/TS/Go/Rust/Java/Shell 等的正则解析"""

    _PATTERNS: Dict[str, List[Tuple[str, str]]] = {
        ".js":  [
            (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)", "func"),
            (r"^(?:export\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", "class"),
            (r"^const\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>", "func"),
            (r"^(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*['\"`]([^'\"` ]+)['\"`]", "route"),
        ],
        ".go":  [
            (r"^func\s+(?:\(\w+\s+\*?(\w+)\)\s+)?(\w+)\s*\(([^)]*)\)", "method"),
            (r"^type\s+(\w+)\s+struct", "struct"),
            (r"^func\s+(\w+)\s*\(([^)]*)\)(?:\s*\(([^)]*)\)|\s+(\w+))?", "func"),
        ],
        ".rs":  [
            (r"^(?:pub(?:\(\w+\))?\s+)?(?:async\s+)?fn\s+(\w+)\s*(?:<[^>]*>)?\(([^)]*)\)(?:\s*->\s*(.+?))?(?:\s*\{|\s*where)", "func"),
            (r"^(?:pub\s+)?struct\s+(\w+)", "struct"),
            (r"^(?:pub\s+)?enum\s+(\w+)", "enum"),
            (r"^(?:pub\s+)?trait\s+(\w+)", "trait"),
        ],
        ".java":[
            (r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(([^)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{", "func"),
            (r"(?:public|private|protected|\s)*(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?", "class"),
        ],
        ".rb":  [
            (r"^\s*def\s+(\w+)(?:\s*\(([^)]*)\))?", "func"),
            (r"^class\s+(\w+)(?:\s*<\s*(\w+))?", "class"),
        ],
        ".sh":  [
            (r"^(?:function\s+)?(\w+)\s*\(\s*\)\s*\{", "func"),
        ],
    }
    # TypeScript 复用 JS 模式并追加
    _PATTERNS[".ts"] = _PATTERNS[".js"] + [
        (r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?", "class"),
        (r"^(?:export\s+)?interface\s+(\w+)", "interface"),
    ]
    _PATTERNS[".tsx"] = _PATTERNS[".ts"]
    _PATTERNS[".jsx"] = _PATTERNS[".js"]

    def parse(self, source: str, filepath: str, ext: str) -> FileInfo:
        lines = source.splitlines()
        info = FileInfo(
            path=filepath, lines=len(lines),
            hash=_md5(source), language=ext.lstrip(".")
        )
        patterns = self._PATTERNS.get(ext, [])
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("#"):
                # 提取 import
                if any(stripped.startswith(kw) for kw in ("import ", "from ", "require(")):
                    mod = re.match(r"(?:import|from)\s+['\"]?(\S+?)['\"]?[\s;]", stripped)
                    if mod:
                        info.imports.append(mod.group(1).split("/")[0][:30])
                continue
            for pattern, kind in patterns:
                m = re.search(pattern, stripped)
                if not m:
                    continue
                g = m.groups()
                if kind == "func":
                    name = g[0] if g else "?"
                    params = g[1] if len(g) > 1 else ""
                    info.funcs.append(FuncInfo(name=name, line_no=lineno,
                                               params=(params or "")[:80]))
                elif kind == "class" or kind == "struct":
                    name = g[0] if g else "?"
                    base = g[1] if len(g) > 1 and g[1] else ""
                    info.classes.append(ClassInfo(name=name, line_no=lineno,
                                                   bases=base or ""))
                elif kind == "route":
                    method, path = g[0].upper(), g[1]
                    info.routes.append(RouteInfo(method=method, path=path, handler="?"))
                elif kind == "method":
                    receiver = g[0] if g[0] else ""
                    name = g[1] if len(g) > 1 else (g[0] or "?")
                    params = g[2] if len(g) > 2 else ""
                    info.funcs.append(FuncInfo(name=name, line_no=lineno,
                                               params=(params or "")[:80]))
                break  # 每行匹配一次

        # 去重 imports
        seen = set()
        deduped = []
        for imp in info.imports:
            if imp and imp not in seen:
                seen.add(imp)
                deduped.append(imp)
        info.imports = deduped[:10]
        return info


# ─────────────────────────────────────────────────────────────
# 写作文档解析器（小说/长文）
# ─────────────────────────────────────────────────────────────

class WritingParser:
    """Markdown/txt 文档 → 章节统计，字数统计"""

    def parse(self, source: str, filepath: str) -> FileInfo:
        lines = source.splitlines()
        info = FileInfo(
            path=filepath, lines=len(lines),
            hash=_md5(source), language="writing"
        )
        # 字数（中文按字符，英文按单词）
        chinese_chars = len(re.findall(r"[\u4e00-\u9fa5]", source))
        english_words = len(re.findall(r"\b[a-zA-Z]+\b", source))
        info.word_count = chinese_chars + english_words

        # 章节标题
        for lineno, line in enumerate(lines, 1):
            m = re.match(r"^(#{1,4})\s+(.+)$", line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()[:80]
                info.headings.append((level, title))
        return info


# ─────────────────────────────────────────────────────────────
# 配置文件解析器
# ─────────────────────────────────────────────────────────────

class ConfigParser:
    """JSON/YAML/TOML 配置文件 → 顶层 key 列表"""

    def parse(self, source: str, filepath: str) -> FileInfo:
        ext = Path(filepath).suffix.lower()
        info = FileInfo(
            path=filepath, lines=source.count("\n"),
            hash=_md5(source), language="config"
        )
        keys: List[str] = []
        try:
            if ext == ".json":
                import json as _json
                data = _json.loads(source)
                if isinstance(data, dict):
                    keys = list(data.keys())[:20]
            elif ext in (".yaml", ".yml"):
                try:
                    import yaml as _yaml
                    data = _yaml.safe_load(source)
                    if isinstance(data, dict):
                        keys = list(data.keys())[:20]
                except ImportError:
                    pass
            elif ext == ".toml":
                try:
                    import tomllib as _toml
                    data = _toml.loads(source)
                    keys = list(data.keys())[:20]
                except ImportError:
                    try:
                        import tomli as _toml
                        data = _toml.loads(source)
                        keys = list(data.keys())[:20]
                    except ImportError:
                        pass
        except Exception:
            pass
        info.constants = keys
        return info


# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def _md5(text: str) -> str:
    return hashlib.md5(text.encode(errors="replace")).hexdigest()[:12]

def _now() -> str:
    return time.strftime("%m-%d %H:%M")

def _count_lines(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0

def _build_tree_str(root: Path, max_depth: int = 3) -> str:
    """生成目录树字符串"""
    lines = [str(root)]

    def _walk(p: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except PermissionError:
            return
        visible = [e for e in entries
                   if e.name not in _SKIP_DIRS
                   and not e.name.startswith(".")
                   and e.name not in _SKIP_FILES]
        for i, item in enumerate(visible):
            is_last = (i == len(visible) - 1)
            connector = "`-- " if is_last else "|-- "
            suffix = "/" if item.is_dir() else ""
            if item.is_file() and item.suffix in _CODE_EXTS | _WRITING_EXTS | _CONFIG_EXTS:
                lc = _count_lines(item)
                suffix = f"  ({lc}L)" if lc else ""
            lines.append(f"{prefix}{connector}{item.name}{suffix}")
            if item.is_dir():
                ext = "    " if is_last else "|   "
                _walk(item, prefix + ext, depth + 1)

    _walk(root, "", 1)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# PROJECT_MAP.md 渲染器
# ─────────────────────────────────────────────────────────────

class MapRenderer:
    """把 FileInfo 列表渲染成 PROJECT_MAP.md 内容"""

    def render_full(self, project_root: Path,
                    file_infos: Dict[str, FileInfo],
                    existing_map: str = "") -> str:
        """完整渲染或增量更新"""
        proj_name = project_root.name
        lines = [f"# {proj_name} — Project Map",
                 f"> 自动生成 · 最后更新: {_now()} · LLM无关\n"]

        # 目录结构
        lines.append("## 目录结构\n```")
        lines.append(_build_tree_str(project_root))
        lines.append("```\n")

        # 模块依赖（汇总所有文件的 imports）
        lines.append("## 模块依赖\n```")
        for relpath, fi in sorted(file_infos.items()):
            if fi.imports and fi.language != "writing":
                lines.append(f"{relpath}  ←  {', '.join(fi.imports[:8])}")
        lines.append("```\n")

        # 核心流程占位（保留手动填写内容）
        if "## 核心流程" in existing_map:
            # 提取已有核心流程段
            start = existing_map.find("## 核心流程")
            end = existing_map.find("\n---\n", start + 5)
            if end > start:
                lines.append(existing_map[start:end])
        else:
            lines.append("## 核心流程\n```\n(首次运行后手动填写，或由 LLM 补充)\n```\n")

        lines.append("---")

        # 按文件分 section
        code_files = {k: v for k, v in file_infos.items() if v.language != "writing"}
        writing_files = {k: v for k, v in file_infos.items() if v.language == "writing"}

        for relpath, fi in sorted(code_files.items()):
            lines.extend(self._render_code_section(relpath, fi, existing_map))

        # 写作模式：章节大纲
        if writing_files:
            lines.append("\n---\n## 章节大纲\n")
            lines.append("| 序号 | 文件 | 标题 | 字数 | 状态 |")
            lines.append("|------|------|------|------|------|")
            i = 1
            for relpath, fi in sorted(writing_files.items()):
                status = "✅done" if fi.word_count > 1000 else "✏️draft"
                lines.append(f"| {i} | {relpath} | {fi.headings[0][1] if fi.headings else '-'} | {fi.word_count} | {status} |")
                i += 1
            lines.append("")

            # 小说角色（从标题中猜）
            all_h2 = [h[1] for fi in writing_files.values() for h in fi.headings if h[0] == 2]
            if all_h2:
                lines.append("### 子章节列表\n")
                for t in all_h2[:30]:
                    lines.append(f"- {t}")
                lines.append("")

        # 测试覆盖
        lines.append("\n---\n## 测试覆盖\n")
        lines.append("| 源文件 | 测试文件 | 状态 |")
        lines.append("|--------|--------|------|")
        for relpath, fi in sorted(code_files.items()):
            fn = Path(relpath).name
            mn = fn.rsplit(".", 1)[0]
            ext = Path(relpath).suffix
            test_name = f"test_{mn}{ext}"
            has_test = any(test_name in k or f"{mn}_test{ext}" in k
                           for k in file_infos.keys())
            icon = "✅" if has_test else "⏳"
            lines.append(f"| {fn} | {test_name} | {icon} |")

        # 变更日志（保留已有 + 追加头部）
        lines.append("\n---\n## 变更日志\n")
        lines.append("| 时间 | 文件 | 类型 | 说明 |")
        lines.append("|------|------|------|------|")
        if "## 变更日志" in existing_map:
            # 提取已有日志行
            idx = existing_map.find("## 变更日志")
            old_log = existing_map[idx:].split("\n\n", 2)
            if len(old_log) > 1:
                for log_line in old_log[1].splitlines()[2:22]:  # 保留最近20条
                    if log_line.startswith("|") and "---" not in log_line:
                        lines.append(log_line)

        return "\n".join(lines) + "\n"

    def render_section(self, relpath: str, fi: FileInfo) -> str:
        """渲染单个文件的 section（用于增量更新）"""
        sec_lines = self._render_code_section(relpath, fi, "")
        return "\n".join(sec_lines)

    def _render_code_section(self, relpath: str, fi: FileInfo,
                              existing_map: str) -> List[str]:
        fn = Path(relpath).name
        lines = [f"\n---\n## {fn}", f"**{relpath}** · {fi.lines}L · `{fi.language}`\n"]

        # Routes
        if fi.routes:
            lines.append("### Routes\n")
            lines.append("| Method | Path | Handler | Params | Doc |")
            lines.append("|--------|------|---------|--------|-----|")
            for r in fi.routes[:20]:
                lines.append(f"| `{r.method}` | `{r.path}` | `{r.handler}` | `{r.params[:50]}` | {r.doc} |")
            lines.append("")

        # Classes
        if fi.classes:
            lines.append("### Classes\n")
            lines.append("| L# | Class | Base | Methods | Doc |")
            lines.append("|----|-------|------|---------|-----|")
            for c in fi.classes[:15]:
                methods_str = ", ".join(f"`{m}`" for m in c.methods[:6])
                if len(c.methods) > 6:
                    methods_str += f" +{len(c.methods)-6}"
                lines.append(f"| {c.line_no} | `{c.name}` | `{c.bases or '-'}` | {methods_str} | {c.doc[:50]} |")
            lines.append("")

        # Functions
        if fi.funcs:
            lines.append("### Functions\n")
            lines.append("| L# | Function | Params | Return | Doc |")
            lines.append("|----|----------|--------|--------|-----|")
            for f in fi.funcs[:30]:
                async_tag = "⚡" if f.is_async else ""
                dec_tag = f" `@{f.decorators[0].split('(')[0]}`" if f.decorators else ""
                lines.append(
                    f"| {f.line_no} | `{async_tag}{f.name}`{dec_tag} "
                    f"| `{f.params[:60]}` | `{f.returns or '-'}` | {f.doc[:50]} |"
                )
            lines.append("")

        # Constants
        if fi.constants:
            lines.append(f"**Constants**: `{'`, `'.join(fi.constants[:15])}`\n")

        # Imports
        if fi.imports:
            lines.append(f"**Imports**: {', '.join(fi.imports[:10])}\n")

        return lines


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
