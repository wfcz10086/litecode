"""
tool_dispatch.py — 工具执行分发器 (v1.0)
═════════════════════════════════════════
从 litecode_server.py 提取 ~1300 行工具实现代码。
"""

import asyncio
import difflib as _difflib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx

from lib.config import (
    WORKSPACE, BASE, SKILLS_DIR, SESSIONS_DISK, TEMPLATE_DIR,
    CONTEXT_WINDOW, MODEL_ID, BACKEND_URL, API_KEY, MAX_TOKENS,
    HAS_EXECUTOR, HAS_MEMORY, HAS_MEMORY_INDEX, HAS_DEEP_SEARCH,
    execute_shell_async, log, SEARCH_CFG,
    mem_get as _mem_get, memory_index_get,
)
from lib.skills import _SKILL_INDEX, _build_skill_index

# ── T-52d-1: helper 域从本文件抽出 → core/tools/*, 在此 re-import 保留原属性面 ──
# 其他模块 (gateway/sessions.py 等) 用 `from tool_dispatch import _bg_procs, ...`,
# 靠这些 re-export 继续能拿到同一个对象.
from tools.bg import (  # noqa: F401
    _bg_procs, _bg_lock,
    _bg_state_path, _persist_bg_state, _restore_bg_state,
    _register_bg, _unregister_bg,
    list_bg_for_session, kill_bg_pid, cleanup_session_bg,
    _check_pid_alive, _exec_bg,
)
from tools.diff_helpers import _make_diff, _patch_context  # noqa: F401
from tools.md_merge import _merge_md_sections, _replace_md_section  # noqa: F401
from tools.vision import _do_vision_ocr, _VISION_EXTS, _VISION_MIME  # noqa: F401
from tools.web import _do_web_fetch  # noqa: F401
# T-52d-2: 简单 tool handler 迁到 core/tools/handlers/, 走 REGISTRY 分派
from tools.handlers import REGISTRY as _HANDLERS  # noqa: F401

# ── 可注入的 server 依赖 ──────────────────────────────────────
_ds = None                 # DeepSearchTool 实例
_add_artifact_fn = None    # artifact 追踪函数
_SEARCH_CFG = {}           # 搜索配置
HAS_SEARCH = False

# [v1.0] 浏览器搜索 (可选, 需要 browser_server + playwright)
try:
    import browser_search as _bs
    HAS_BROWSER_SEARCH = True
except ImportError:
    _bs = None
    HAS_BROWSER_SEARCH = False

# [v1.0.3] 代码错误语义搜索 (GitHub issues + StackOverflow)
try:
    import error_search as _es
    HAS_ERROR_SEARCH = True
except ImportError:
    _es = None
    HAS_ERROR_SEARCH = False

# P0-2: Read 去重缓存
_read_cache: dict = {}

# P12-c: 工具调用埋点
TOOLS_TELEMETRY_DIR = WORKSPACE / "telemetry"
TOOLS_TELEMETRY_PATH = TOOLS_TELEMETRY_DIR / "tools.jsonl"


def init(*, ds=None, add_artifact_fn=None, search_cfg=None):
    """由 litecode_server.py 启动时调用"""
    global _ds, _add_artifact_fn, _SEARCH_CFG, HAS_SEARCH
    _ds = ds
    _add_artifact_fn = add_artifact_fn
    _SEARCH_CFG = search_cfg or SEARCH_CFG
    HAS_SEARCH = ds is not None
    # [v1.0] 启动时恢复 BG 进程 registry（server 重启不丢）
    _restore_bg_state()
    # [v1.0] 向 session 模块注册清理钩子：session 结束时自动 kill 其 bg 进程
    try:
        from lib.session import set_cleanup_hook as _set_session_cleanup_hook
        _set_session_cleanup_hook(lambda _sid: cleanup_session_bg(_sid, force=False))
    except Exception as _e:
        log.warning(f"[tool_dispatch] failed to register session cleanup hook: {_e}")


# [v1.0] read_cache LRU 上限，防止长跑涨内存
_READ_CACHE_MAX = 200

# [P0-c] read_file 安全预检查常量
READ_FILE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

_READ_FILE_BINARY_EXTS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".mkv",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar", ".tgz",
    ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a",
    ".pyc", ".pyo", ".class", ".jar", ".wasm",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".db", ".sqlite", ".sqlite3",
    ".pdf", ".docx", ".xlsx", ".pptx",
})

def _read_cache_put(path_str: str, value: tuple):
    _read_cache[path_str] = value
    # 超过上限时淘汰最早的一半
    if len(_read_cache) > _READ_CACHE_MAX:
        # dict 在 Py3.7+ 保持插入顺序，所以 list(keys)[:N] 就是最早的
        for old_k in list(_read_cache.keys())[:_READ_CACHE_MAX // 2]:
            _read_cache.pop(old_k, None)

# [batch-write-guard-2026-05] 每 session 60s 窗口内创建 ≥ 3 个新源文件 → SYSTEM 警告
# 用于 BOOTSTRAP "大型项目实施规则": 强制 AI 分步而非批量产出
_WRITE_STREAK_WINDOW_SEC = 60
_write_streak: dict = {}  # sid -> [(ts, abs_path), ...]
_BATCH_GUARD_EXEMPT_EXTS = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini"}


def _check_batch_write_guard(sid: str, fp_str: str, is_new: bool) -> str:
    """每次 write_file 调用. 返回追加的 SYSTEM 警告 (空串 = 无警告).
    只针对源代码新文件 (非 doc/config). 同 session 60s 内 ≥3 个就警告.
    """
    if not sid or not is_new:
        return ""
    fp_p = Path(fp_str)
    if fp_p.suffix.lower() in _BATCH_GUARD_EXEMPT_EXTS:
        return ""
    now = time.time()
    lst = _write_streak.setdefault(sid, [])
    # 清窗外
    lst[:] = [x for x in lst if now - x[0] < _WRITE_STREAK_WINDOW_SEC]
    lst.append((now, fp_str))
    if len(lst) >= 3:
        recent = [Path(p).name for _, p in lst[-3:]]
        return (
            f"\n\n[SYSTEM-BATCH-GUARD] ⛔️ 你在 {_WRITE_STREAK_WINDOW_SEC}s 内连续创建了 "
            f"{len(lst)} 个新源文件 ({recent}). 这违反 BOOTSTRAP '大型项目实施规则'. "
            f"立即停止 write_file, 跑 execute_shell 编译/lint 验证 (go build / pytest / tsc), "
            f"通过后再继续下一个文件. 不允许批量产出."
        )
    return ""


# [v1.0] Skill 使用统计 — 为自动迭代 skill 打基础
_skill_usage_lock = threading.Lock()

def _skill_usage_path() -> Path:
    return SKILLS_DIR / "_usage.json"

def _record_skill_usage(skill_name: str):
    """记录某 skill 被加载, 用于后续分析哪些 skill 活跃/冷门"""
    with _skill_usage_lock:
        try:
            p = _skill_usage_path()
            data = {}
            if p.exists():
                try:
                    data = json.loads(p.read_text())
                except Exception:
                    data = {}
            entry = data.setdefault(skill_name, {"loaded": 0, "first_ts": time.time(), "last_ts": 0})
            entry["loaded"] = entry.get("loaded", 0) + 1
            entry["last_ts"] = time.time()
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            log.warning(f"  [skill_usage] record failed: {e}")

def get_skill_usage_summary() -> str:
    """返回 skill 使用统计摘要, 用于自我反思"""
    try:
        p = _skill_usage_path()
        if not p.exists():
            return "(无使用记录)"
        data = json.loads(p.read_text())
        if not data:
            return "(无使用记录)"
        # 按加载次数排序
        sorted_items = sorted(data.items(), key=lambda x: x[1].get("loaded", 0), reverse=True)
        lines = ["## Skill 使用统计"]
        for name, info in sorted_items[:20]:
            loaded = info.get("loaded", 0)
            last_days = int((time.time() - info.get("last_ts", 0)) / 86400)
            lines.append(f"  {name:<40s} loaded={loaded:4d}  last={last_days}d ago")
        if len(data) > 20:
            lines.append(f"  ... ({len(data)-20} more)")
        # 冷门 skill（30 天未被加载，可能需要调整描述或删除）
        cold = [n for n, info in data.items()
                if time.time() - info.get("last_ts", 0) > 30 * 86400]
        if cold:
            lines.append(f"\n⚠️ 30 天未被加载的 skill ({len(cold)}个): {', '.join(cold[:10])}")
        return "\n".join(lines)
    except Exception as e:
        return f"(统计读取失败: {e})"


def _add_artifact(sid, data):
    if _add_artifact_fn and sid:
        _add_artifact_fn(sid, data)


def _infer_timeout(cmd: str) -> Optional[int]:
    c = cmd.lower()
    if any(k in c for k in ["uvicorn", "gunicorn", "flask run", "fastapi run",
                             "nodemon ", "npm start", "yarn start",
                             "streamlit run", "jupyter ", "tornado",
                             "python -m http", "python3 -m http"]):
        return None
    # node 单独判断: node -e '...' / node script.js 是一次性脚本给 120s;
    # 只有 node server.js / app.js / index.js 等服务入口才当长服务
    if "node " in c:
        import re as _re_node
        if _re_node.search(r'node\s+(server|app|index|main|daemon)\b', c):
            return None
        return 120
    if any(k in c for k in ["apt-get install", "apt install", "pip install",
                             "npm install", "yarn install", "docker pull", "docker build"]):
        return 300
    if any(k in c for k in ["curl ", "wget ", "git clone", "git pull"]):
        return 60
    return 30

# ── BG registry / vision / web_fetch / diff / md_merge moved to tools/* ──────
# 各 helper 已在文件顶部 re-import 保留 tool_dispatch.<name> 属性面 (T-52d-1)


# ── P1-5: Checkpoint system -- 文件修改前自动备份 ──────────────
_CKPT_DIR_NAME = ".openclaw_checkpoints"
_CKPT_MAX_PER_FILE = 5  # 每个文件最多保留 5 个历史版本

def _save_checkpoint(filepath: Path, content: str):
    """write_file/patch_file 前自动保存历史版本，支持回滚。"""
    try:
        import shutil
        ckpt_dir = WORKSPACE / _CKPT_DIR_NAME
        ckpt_dir.mkdir(exist_ok=True)
        ts = int(time.time())
        safe_name = filepath.name.replace("/", "_")
        bak_path = ckpt_dir / f"{safe_name}.{ts}.bak"
        bak_path.write_text(content)
        # 清理旧备份，只保留最近 N 个
        existing = sorted(ckpt_dir.glob(f"{safe_name}.*.bak"), reverse=True)
        for old in existing[_CKPT_MAX_PER_FILE:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass  # checkpoint 失败不影响主流程


def _emit_tool_telemetry(tool_name, args, duration_ms, result_len, sid="", error=""):
    """v1.8 P36-c: 走 core/telemetry.py 统一入口；业务字段保留。"""
    try:
        args_summary = repr(args)[:80].replace("\n", " ")
        fields = {
            "tool_name": tool_name,
            "args_keys": sorted(list(args.keys())) if isinstance(args, dict) else [],
            "args_summary": args_summary,
            "sid": (sid or "")[:32],  # 旧字段保留
            "duration_ms": duration_ms,  # 旧字段保留
            "result_len": result_len,
            "error": error[:200] if error else "",
        }
        try:
            from core.telemetry import emit as _emit
            _emit(
                event="tool_error" if error else "tool_call",
                fields=fields,
                jsonl="tools.jsonl",
                sid=sid,
                latency_ms=duration_ms,
            )
        except Exception:
            TOOLS_TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
            entry = {"ts": time.time(), **fields}
            with TOOLS_TELEMETRY_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[telemetry/tools] write failed: {e}")


async def execute_tool(name: str, args: dict, sid: str = None) -> tuple[str, str | None]:
    _t0 = time.time()
    _result_str = ""
    _error = ""
    try:
        result, diff = await _execute_tool_impl(name, args, sid)
        _result_str = result if isinstance(result, str) else str(result)
        return result, diff
    except Exception as e:
        _error = str(e)
        raise
    finally:
        _emit_tool_telemetry(
            name, args,
            int((time.time() - _t0) * 1000),
            len(_result_str),
            sid or "",
            _error,
        )


async def _execute_tool_impl(name: str, args: dict, sid: str = None) -> tuple[str, str | None]:
    """返回 (result_str, diff_str_or_None)。diff 非 None 时客户端渲染彩色 diff。"""
    # ── plugins/tools/* 优先分派 ────────────────────────────────
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _plg_base = str(_Path(__file__).parent.parent)
        if _plg_base not in _sys.path:
            _sys.path.insert(0, _plg_base)
        from plugins.tools import web_search as _plg_web_search
        if name in _plg_web_search.names():
            _plg_ctx = {"ds": _ds, "add_artifact_fn": _add_artifact,
                        "search_cfg": _SEARCH_CFG}
            _plg_ret = await _plg_web_search.dispatch(name, args, sid, _plg_ctx)
            if _plg_ret is not None:
                return _plg_ret  # 返回签名保持原样 (str 或 tuple)
        # [#45] html_render plugin — HTML/URL → PNG 预览截图
        from plugins.tools import html_render as _plg_html_render
        if name in _plg_html_render.names():
            _plg_ret = await _plg_html_render.dispatch(
                name, args, sid,
                {"add_artifact_fn": _add_artifact, "sid": sid},
            )
            if _plg_ret is not None:
                return _plg_ret
    except ImportError:
        pass  # plugin 未就位时降级到内联分支

    # ── T-52d-2: 已迁移到 handlers/ 的 tool 走注册表分派 ─────────
    _handler = _HANDLERS.get(name)
    if _handler is not None:
        return await _handler(sid, args)

    if name == "execute_shell":
        cmd        = args.get("command", "")
        timeout    = args.get("timeout")
        background = args.get("background", False)
        health_url = args.get("health_url")
        keep_alive = args.get("keep_alive", False)  # [v1.0] True=session 结束也保留
        if background:
            return await _exec_bg(cmd, health_url, sid=sid, keep=keep_alive), None
        # [v1.0] 非 background 的硬上限 - 防止误用启动服务挂死主循环
        _FOREGROUND_HARD_LIMIT = 900  # 15 分钟
        _inferred = _infer_timeout(cmd)
        if _inferred is None:
            # [BUG-FIX 2026-05-22] 模型识别为"长服务"命令但用户没加 background=True
            # 旧版 forcing 900s timeout → 用户卡 15 分钟 (用户报 'http.server & 卡死')
            # 改: 直接帮 AI 转 bg pool 跑, 不让任务卡死.
            log.warning(f"  [execute_shell] long-service cmd 无 background=True, 自动转 bg pool: {cmd[:80]}")
            r_msg = await _exec_bg(cmd, health_url, sid=sid, keep=keep_alive)
            return (r_msg + "\n[AUTO-BG] 该命令疑似长服务 (含 http.server / uvicorn / & 等), "
                            "已自动以 background=True 跑. 下次起服务请直接传 background=true."), None
        else:
            t = timeout if timeout is not None else _inferred
            t = min(t, _FOREGROUND_HARD_LIMIT)  # 用户给的 timeout 也受硬上限约束
            _misuse_hint = ""
        if HAS_EXECUTOR:
            result = await execute_shell_async(cmd, timeout=t, workspace=WORKSPACE)
        else:
            # 用 run_in_executor 包裹同步 subprocess，避免阻塞 asyncio event loop
            import subprocess as _sp
            # [v1.0] 复用上面算好的 t（已考虑硬上限），不要重新 _infer_timeout
            def _fallback_run():
                try:
                    r = _sp.run(cmd, shell=True, capture_output=True, text=True,
                                timeout=t, cwd=str(WORKSPACE), start_new_session=True)
                    out, err = r.stdout.strip(), r.stderr.strip()
                    if r.returncode == 0:
                        result = out
                        if err: result = (out + "\n" if out else "") + f"[warnings]:\n{err}"
                    else:
                        result = (out + "\n" + err).strip() or f"(exit {r.returncode})"
                    return result or "(exit 0)"
                except _sp.TimeoutExpired:
                    return f"TIMEOUT after {t}s -- consider background=true"
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _fallback_run)
        # ---- 优化: execute_shell 结果截断，避免 pip install / apt 等长输出浪费 context ----
        MAX_SHELL_RESULT = 2000
        if len(result) > MAX_SHELL_RESULT:
            head = result[:500]
            tail = result[-800:]
            result = f"{head}\n\n... [truncated {len(result)} chars] ...\n\n{tail}"
        # ---- 优化: pytest / test 输出只保留摘要行 ----
        if ("pytest" in cmd or "go test" in cmd or "npm test" in cmd or "npx jest" in cmd):
            _lines = result.split("\n")
            _summary_lines = []
            for _ln in _lines:
                _ln_lower = _ln.lower().strip()
                if any(kw in _ln_lower for kw in [
                    "passed", "failed", "error", "warnings", "=====",
                    "tests/", "test_", ".test.", "fail:", "ok ", "not ok",
                    "short test summary", "fixture", "traceback", "assert",
                    "importerror", "modulenotfound", "syntaxerror",
                ]):
                    _summary_lines.append(_ln)
            if _summary_lines and len(_summary_lines) < len(_lines):
                result = "\n".join(_summary_lines[-30:])
        # [v1.0] 带上误用提示
        return result + _misuse_hint, None


    elif name == "web_search":
        # [S1-A] 已迁 plugins/tools/web_search.py, 保留兜底
        # [v1.1] 三层策略: deep_search → HTML fallback → 真实浏览器.
        # 关键修复: deep_search 返回 "搜索无结果" 字符串 (没抛异常) 时必须视为失败继续降级,
        # 不能把提示文字当结果传给模型 — 参考 openclaw 的升级链路.
        _WEB_SEARCH_TOTAL_TIMEOUT = 15
        query = args["query"]
        region = args.get("region", _SEARCH_CFG.get("default_mode", "domestic"))

        def _is_empty_search(txt: str) -> bool:
            if not txt or len(txt.strip()) < 40:
                return True
            low = txt.strip()
            # [v1.4] 扩展检测: 搜索空 + 人机验证/CAPTCHA/地区限制 页面都算空
            _EMPTY_MARKERS = (
                "搜索无结果", "no results", "无相关结果",
                "ERROR: all search", "搜索结果均与问题无关", "搜索结果无关",
                "请解决以下难题", "人机身份验证", "captcha",
                "verify you are human", "unusual traffic",
                "access denied", "请完成以下验证", "robot check",
                "请完成安全验证", "最后一步 请解决",
                "currently not available in your region",
                "页面在您所在的地区暂不支持",
            )
            low_ci = low.lower()
            for m in _EMPTY_MARKERS:
                if m.lower() in low_ci:
                    return True
            return False

        # [2026-05-25] 优先级 0: 本机 SearXNG (JSON API, 不被反爬, ≤3s)
        # 走通就直接返回, 否则 fallback 到 deep_search → browser_search.
        if HAS_BROWSER_SEARCH and _bs and hasattr(_bs, "searxng_search"):
            try:
                loop = asyncio.get_event_loop()
                sx_result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, _bs.searxng_search, query,
                        8,  # top_k
                    ),
                    timeout=10,
                )
                if isinstance(sx_result, dict) and sx_result.get("ok"):
                    msg = sx_result.get("message") or ""
                    if msg and not _is_empty_search(msg):
                        log.info(f"  [web_search] searxng HIT ({len(sx_result.get('results', []))} 条)")
                        return msg, None
                log.info(f"  [web_search] searxng empty/down, fallback to deep_search")
            except asyncio.TimeoutError:
                log.warning(f"  [web_search] searxng timeout (10s), fallback to deep_search")
            except Exception as _sxe:
                log.warning(f"  [web_search] searxng failed: {_sxe}, fallback to deep_search")

        if HAS_SEARCH and _ds:
            try:
                loop = asyncio.get_event_loop()
                ds_result = await asyncio.wait_for(
                    loop.run_in_executor(None, _ds.run, query, args.get("effort", "mid")),
                    timeout=_WEB_SEARCH_TOTAL_TIMEOUT,
                )
                if ds_result and not _is_empty_search(ds_result):
                    return ds_result, None
                log.info(f"  [web_search] deep_search returned empty/no-result, escalating to browser fallback")
            except asyncio.TimeoutError:
                log.warning(f"  [web_search] deep_search timed out after {_WEB_SEARCH_TOTAL_TIMEOUT}s, trying fallback")
            except Exception as _se:
                log.warning(f"  [web_search] deep_search failed: {_se}, trying fallback")

        # [v1.3] deep_search 空结果 → 上真实浏览器. 改成**并行**多引擎取最快, 原先
        # 串行 bing→sogou 每个 25s, 总耗时可能 50s+. 现在并行, 总预算 15s, 谁先有
        # 结果用谁. 日志显示 duckduckgo 命中率最高, 放在首位.
        if HAS_BROWSER_SEARCH and _bs:
            # duckduckgo 全球最稳, bing 做补充 (unwrap /ck/a 后也能用)
            if region == "international":
                # [2026-05-22] 海外用户加 google: 有代理时 google 优先
                # 注意 google 在境内/无代理 IP 会触发 reCAPTCHA → 返空, 自动 fallback 到下一个
                # 顺序: duckduckgo (最稳) → google (有代理就快) → bing (兜底)
                engines_to_try = ["duckduckgo", "google", "bing"]
            else:
                # 国内: 国内节点 bing.cn + sogou 都可用
                engines_to_try = ["duckduckgo", "bing", "sogou"]
            log.info(f"  [web_search] parallel browser_search: {engines_to_try}")

            loop = asyncio.get_event_loop()
            futs = {
                eng: loop.run_in_executor(None, _bs.browser_search_fallback, query, eng)
                for eng in engines_to_try
            }
            per_engine_timeout = 12
            total_deadline = time.time() + per_engine_timeout + 3
            pending = set(futs.values())
            fut_to_engine = {v: k for k, v in futs.items()}

            try:
                while pending and time.time() < total_deadline:
                    remaining = max(0.5, total_deadline - time.time())
                    done, pending = await asyncio.wait(
                        pending, timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for fut in done:
                        eng_name = fut_to_engine.get(fut, "?")
                        try:
                            res = fut.result()
                        except Exception as _be:
                            log.warning(f"  [web_search] browser({eng_name}) failed: {_be}")
                            continue
                        if res and not _is_empty_search(res):
                            # 取到就跑, 顺手取消还在跑的
                            for p in pending:
                                try: p.cancel()
                                except Exception: pass
                            return res, None
                        else:
                            log.info(f"  [web_search] browser({eng_name}) empty")
                # 全部超时
                for p in pending:
                    try: p.cancel()
                    except Exception: pass
                log.warning(f"  [web_search] all browser engines empty/timeout after {per_engine_timeout+3}s")
            except Exception as _me:
                log.warning(f"  [web_search] browser race loop error: {_me}")

        # 最后兜底: HTML 降级（很可能也失败，但给一次机会）
        from urllib.parse import quote_plus as _qp
        q_encoded = _qp(query)
        sources = _SEARCH_CFG.get(region, _SEARCH_CFG.get("domestic", []))
        _t_start = time.time()
        _FALLBACK_BUDGET = 8
        for src in sources:
            if time.time() - _t_start > _FALLBACK_BUDGET:
                break
            url = src.get("url_template", "").replace("{q}", q_encoded)
            if url:
                _remaining = max(3, _FALLBACK_BUDGET - int(time.time() - _t_start))
                try:
                    result = await asyncio.wait_for(_do_web_fetch(url), timeout=_remaining)
                    if result and not result.startswith("ERROR") and not _is_empty_search(result):
                        return result, None
                except asyncio.TimeoutError:
                    continue

        return (f"ERROR: 所有搜索渠道都无结果 (deep_search + browser + HTML). "
                f"建议换关键词, 或直接调 browser_read(url) 访问具体网站."), None



    # ── [desktop-2026-05] 桌面操控 5 工具 (+ desktop_click 2026-05-22) ──
    elif name in ("desktop_exec", "desktop_screenshot", "desktop_key", "desktop_type", "desktop_click"):
        try:
            from lib import desktop_control as _dc
        except ImportError:
            try:
                import desktop_control as _dc
            except ImportError as _de:
                return f"ERROR: desktop_control 模块不可用: {_de}", None
        try:
            if name == "desktop_exec":
                cmd = args.get("cmd", "").strip()
                if not cmd:
                    return "ERROR: desktop_exec 需要 cmd", None
                bg = bool(args.get("bg", True))
                # [FIX 2026-05] bg=true 改走 LiteCode 的 _exec_bg, 让 chrome 等长进程
                # 进 bg pool — list_bg 能看到, tail_log 能看日志, session 结束自动清理.
                # ⚠ _exec_bg 内部 nohup <cmd>, nohup 不解析 shell 语法,
                #   所以 "DISPLAY=:99 cmd" 会被当成可执行文件名找不到. 用 `env` 包.
                if bg:
                    cmd_with_display = f"env DISPLAY=:99 XAUTHORITY=/root/.Xauthority {cmd}"
                    r_msg = await _exec_bg(cmd_with_display, sid=sid, keep=False)
                    return r_msg, None
                # bg=false: 同步等待 stdout, 走原逻辑
                loop = asyncio.get_event_loop()
                r = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _dc.exec(cmd, bg=False)),
                    timeout=30,
                )
                return (r.get("msg") or "(no msg)"), None
            elif name == "desktop_screenshot":
                path = args.get("path") or None
                loop = asyncio.get_event_loop()
                r = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _dc.screenshot(path)),
                    timeout=15,
                )
                if r.get("ok") and r.get("path"):
                    # [FIX 2026-05] 之前只返回裸路径, AI 把它当 markdown 链接拼成
                    # <a href="/tmp/..."> 浏览器解析成站点相对 URL → 404.
                    # 现在直接返回 markdown 图片语法, src 走 /api/media?path=... 命中 workspace_router.
                    # 浏览器拿到 /api/media?path=/tmp/litecode_workspace/screenshots/xxx.png
                    # → workspace_router._WX_SAFE_DIRS 包含 /tmp/litecode_workspace → FileResponse 200.
                    import urllib.parse as _up
                    _abs_path = r["path"]
                    _media_url = f"/api/media?path={_up.quote(_abs_path)}"
                    _size = r.get("bytes", 0)
                    return (
                        f"截图已保存: `{_abs_path}` ({_size} bytes)\n\n"
                        f"![desktop screenshot]({_media_url})"
                    ), None
                return f"ERROR: {r.get('msg','screenshot failed')}", None
            elif name == "desktop_key":
                keys = args.get("keys", "")
                if not keys:
                    return "ERROR: desktop_key 需要 keys", None
                loop = asyncio.get_event_loop()
                r = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _dc.key(keys)),
                    timeout=10,
                )
                return (r.get("msg") or "(no msg)"), None
            elif name == "desktop_type":
                text = args.get("text", "")
                if not text:
                    return "ERROR: desktop_type 需要 text", None
                loop = asyncio.get_event_loop()
                r = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _dc.type_text(text)),
                    timeout=35,
                )
                return (r.get("msg") or "(no msg)"), None
            elif name == "desktop_click":
                try:
                    x = int(args.get("x", -1)); y = int(args.get("y", -1))
                except Exception:
                    return "ERROR: desktop_click x/y 必须是整数", None
                if x < 0 or y < 0:
                    return "ERROR: desktop_click 缺 x/y", None
                button = args.get("button", "left")
                loop = asyncio.get_event_loop()
                r = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: _dc.click(x, y, button)),
                    timeout=8,
                )
                return (r.get("msg") or f"click ({x},{y})"), None
        except asyncio.TimeoutError:
            return f"ERROR: {name} 超时", None
        except Exception as e:
            return f"ERROR: {name} 失败: {e}", None







    elif name == "project_init":
        import hashlib as _hl
        _proj_path = args.get("path", "").strip()
        _refresh   = args.get("refresh", False)
        _proj_root = Path(_proj_path) if Path(_proj_path).is_absolute() else WORKSPACE / _proj_path
        if not _proj_root.exists():
            return f"ERROR: path not found: {_proj_root}", None

        _brief_path = _proj_root / "PROJECT_MANIFEST_BRIEF.md"
        _full_path  = _proj_root / "PROJECT_MANIFEST.md"
        _graph_path = _proj_root / "CALL_GRAPH.json"
        _hash_path  = _proj_root / ".manifest_hashes.json"

        _old_hashes = {}
        if _hash_path.exists() and not _refresh:
            try:
                _old_hashes = json.loads(_hash_path.read_text())
            except Exception:
                pass

        if _brief_path.exists() and not _refresh:
            age = int((time.time() - _brief_path.stat().st_mtime) / 60)
            if age < 30:
                return (
                    "[project_init] fresh (" + str(age) + "min). Brief manifest:\n"
                    "Full symbol list: read_file PROJECT_MANIFEST.md\n\n"
                    + _brief_path.read_text()
                ), None

        _LANG_PATS = {
            ".py":   [r"^(def|async def|class)\s+(\w+)"],
            ".js":   [r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", r"^(?:export\s+)?class\s+(\w+)"],
            ".ts":   [r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
                      r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)",
                      r"^(?:export\s+)?(?:interface|type|enum)\s+(\w+)"],
            ".go":   [r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", r"^type\s+(\w+)\s+(?:struct|interface)"],
            ".rs":   [r"^(?:pub(?:\(\w+\))?\s+)?(?:async\s+)?fn\s+(\w+)", r"^(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)"],
            ".java": [r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(\w+)\s*\(",
                      r"(?:public|private|protected|\s)*class\s+(\w+)"],
            ".c":    [r"^(?:static\s+)?[\w\s\*]+\s+(\w+)\s*\([^;]"],
            ".cpp":  [r"^(?:static\s+)?[\w\s\*:]+\s+(\w+)\s*\([^;]", r"^class\s+(\w+)"],
            ".rb":   [r"^\s*def\s+(\w+)", r"^class\s+(\w+)"],
            ".php":  [r"^\s*(?:public|private|protected|static|\s)*function\s+(\w+)"],
            ".sh":   [r"^(?:function\s+)?(\w+)\s*\(\s*\)"],
            ".lua":  [r"^(?:local\s+)?function\s+(\w+)"],
        }
        _SKIP = {".git","__pycache__","node_modules",".venv","venv","dist","build",".next","target","vendor"}
        _COMMENT_PFX = ("#","//","/*")

        call_graph = {}
        file_meta  = {}
        new_hashes = {}
        changed_files = 0

        if _graph_path.exists() and _old_hashes and not _refresh:
            try:
                call_graph = json.loads(_graph_path.read_text())
            except Exception:
                call_graph = {}

        # [v1.0] 硬上限: 避免大 monorepo 扫描超时
        _PI_MAX_FILES   = 1000   # 最多扫 1000 个文件
        _PI_MAX_SYMBOLS = 500    # 回调分析最多处理 500 个 symbol
        _PI_TIME_BUDGET = 60     # 超时 60s
        _pi_start = time.time()
        _pi_file_count = 0
        _pi_truncated  = False

        for fpath in sorted(_proj_root.rglob("*")):
            # [v1.0] 超时/文件数上限保护
            if time.time() - _pi_start > _PI_TIME_BUDGET:
                _pi_truncated = True
                log.warning(f"  [project_init] time budget {_PI_TIME_BUDGET}s exhausted")
                break
            if _pi_file_count >= _PI_MAX_FILES:
                _pi_truncated = True
                log.warning(f"  [project_init] file limit {_PI_MAX_FILES} reached")
                break
            if not fpath.is_file():
                continue
            if any(part in _SKIP for part in fpath.parts):
                continue
            if fpath.name in ("PROJECT_MANIFEST.md","PROJECT_MANIFEST_BRIEF.md","CALL_GRAPH.json",".manifest_hashes.json"):
                continue
            ext = fpath.suffix.lower()
            if ext not in _LANG_PATS or fpath.stat().st_size > 500*1024:
                continue
            _pi_file_count += 1

            rel = str(fpath.relative_to(_proj_root))
            try:
                fhash = _hl.md5(fpath.read_bytes()).hexdigest()[:12]
            except Exception:
                continue
            new_hashes[rel] = fhash

            if fhash == _old_hashes.get(rel) and not _refresh:
                file_meta[rel] = {"cached": True}
                continue

            changed_files += 1
            try:
                src_lines = fpath.read_text(errors="replace").splitlines()
            except Exception:
                continue

            symbols = []
            imports = []
            for lineno, line in enumerate(src_lines, 1):
                s = line.strip()
                if s.startswith(("import ", "from ", "require(", "use ")):
                    imports.append(s[:60])
                for pat in _LANG_PATS[ext]:
                    m = re.search(pat, s)
                    if m:
                        grp = 2 if m.lastindex and m.lastindex >= 2 else 1
                        sym = m.group(grp)
                        if sym and len(sym) > 1:
                            symbols.append((lineno, sym))
                            call_graph[sym] = {"file": rel, "line": lineno, "callers": [], "callees": []}
                        break

            resp = ""
            for ln in src_lines[:5]:
                ls = ln.strip()
                if any(ls.startswith(p) for p in _COMMENT_PFX):
                    resp = ls.lstrip("#/'\"* ")[:60].strip()
                    break
            if not resp and imports:
                libs = [i.split()[1].split(".")[0] for i in imports[:3] if len(i.split()) > 1]
                resp = "uses: " + ", ".join(libs) if libs else ""

            file_meta[rel] = {
                "lines": len(src_lines), "symbols": symbols,
                "imports": imports[:5], "resp": resp,
            }

        # [v1.0] 回调分析 O(N²×M): symbols 超 500 跳过，给一个警告
        sym_set = set(call_graph.keys())
        if len(sym_set) <= _PI_MAX_SYMBOLS and time.time() - _pi_start < _PI_TIME_BUDGET:
            for rel, meta in file_meta.items():
                if time.time() - _pi_start > _PI_TIME_BUDGET:
                    _pi_truncated = True
                    break
                if meta.get("cached"):
                    continue
                try:
                    src = (_proj_root / rel).read_text(errors="replace")
                except Exception:
                    continue
                for lineno, sname in meta.get("symbols", []):
                    for other in sym_set:
                        if other == sname:
                            continue
                        if re.search(r"\b" + re.escape(other) + r"[\s\(\.]", src):
                            cg = call_graph.get(sname)
                            if cg and other not in cg["callees"]:
                                cg["callees"].append(other)
                            og = call_graph.get(other)
                            if og and sname not in og["callers"]:
                                og["callers"].append(sname)
        else:
            _pi_truncated = True
            log.warning(f"  [project_init] {len(sym_set)} symbols > {_PI_MAX_SYMBOLS}, skipping call graph (use locate_symbol for precise lookup)")

        _graph_path.write_text(json.dumps(call_graph, ensure_ascii=False, indent=2))
        _hash_path.write_text(json.dumps(new_hashes, ensure_ascii=False, indent=2))

        brief_lines = [
            "# PROJECT_MANIFEST_BRIEF\nRoot: " + str(_proj_root) + "\n",
            "Files: " + str(len(new_hashes)) + "  Symbols: " + str(len(call_graph)) +
            "  Changed: " + str(changed_files) + "\n\n"
        ]
        for rel in sorted(new_hashes.keys()):
            meta = file_meta.get(rel)
            if not meta or meta.get("cached"):
                brief_lines.append("  " + rel + "  [cached]\n")
                continue
            syms = [s[1] for s in meta["symbols"][:5]]
            sym_str = ", ".join(syms) + ("..." if len(meta["symbols"]) > 5 else "")
            resp_str = ("  # " + meta["resp"]) if meta.get("resp") else ""
            brief_lines.append(
                "  " + rel.ljust(42) + " " + str(meta["lines"]).rjust(4) + "L" + resp_str + "\n"
                "    [" + sym_str + "]\n"
            )

        brief_text = "".join(brief_lines)
        _brief_path.write_text(brief_text)

        full_lines = ["# PROJECT_MANIFEST (FULL)\nRoot: " + str(_proj_root) + "\n\n"]
        for rel in sorted(new_hashes.keys()):
            meta = file_meta.get(rel)
            if not meta or meta.get("cached"):
                continue
            full_lines.append("## " + rel + "  (" + str(meta["lines"]) + " lines)\n")
            for ln, sym in meta["symbols"]:
                callers = call_graph.get(sym, {}).get("callers", [])[:3]
                callees = call_graph.get(sym, {}).get("callees", [])[:3]
                c_str = "  <- " + ",".join(callers) if callers else ""
                d_str = "  -> " + ",".join(callees) if callees else ""
                full_lines.append("  L" + str(ln).ljust(5) + " " + sym + c_str + d_str + "\n")
            full_lines.append("\n")
        _full_path.write_text("".join(full_lines))

        result = (
            "[project_init] " + str(len(new_hashes)) + " files, " + str(len(call_graph)) + " symbols ("
            + str(changed_files) + " rescanned, " + str(len(new_hashes)-changed_files) + " cached)\n"
            "Brief manifest | Full: read_file PROJECT_MANIFEST.md\n\n"
            + brief_text
        )
        if len(result) > 4000:
            result = result[:3800] + "\n...[brief truncated, use read_file PROJECT_MANIFEST_BRIEF.md]"
        # [v1.0] 项目扫描被截断时提示
        if _pi_truncated:
            result += (
                f"\n\n⚠️ [PARTIAL] 扫描被限流（{_pi_file_count} files 扫描, {len(call_graph)} symbols, "
                f"耗时 {int(time.time()-_pi_start)}s）。"
                f"项目可能过大。建议: 用 search_code/locate_symbol 精准查询特定 symbol。"
            )
        return result, None



    else:
        return f"ERROR: unknown tool '{name}'", None
