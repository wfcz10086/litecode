"""web_search.py — 网页/搜索/浏览器类 tool.

从 core/tool_dispatch.py 抽出的 8 个 tool + 1 个 async helper.
通过 dispatch(name, args, sid=None, ctx=None) 统一入口调用.

ctx: 传 shared runtime (browser_search / deep_search / ds instance / etc.).
目前先接受 dict, 后续 plugin 化时切成 dataclass.
"""
from __future__ import annotations
import ipaddress, json, os, re, socket, time, asyncio, logging
from typing import Optional
from urllib.parse import urlparse

import httpx

log = logging.getLogger("litecode_server")


def _is_ssrf_target(url: str) -> Optional[str]:
    """返回 None = 放行; 非 None = 拒绝原因. 只允 http/https, 拒内网/回环/元数据."""
    try:
        u = urlparse(url)
    except Exception as e:
        return f"invalid URL: {e}"
    if u.scheme not in ("http", "https"):
        return f"scheme {u.scheme!r} not allowed (http/https only)"
    host = (u.hostname or "").strip()
    if not host:
        return "empty host"
    # 解析 IP; 若 host 已是 IP 直接用
    try:
        ip = ipaddress.ip_address(host)
        ips = [ip]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
            ips = list({ipaddress.ip_address(ai[4][0]) for ai in infos})
        except Exception as e:
            return f"DNS resolve failed: {e}"
    for ip in ips:
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return f"target IP {ip} in disallowed range (private/loopback/link-local/metadata)"
    return None

# ── 从 tool_dispatch 复用模块级依赖 (避免重复初始化 / 双份状态) ──
# tool_dispatch 不 import web_search, 所以这里直接反向 import 安全.
#
# 懒加载: plugins.registry.scan() 可能在 litecode_server.py 设置
# `sys.path += core/` 之前跑 (比如 CLI / 测试孤立扫描). 直接 top-level
# `from core import tool_dispatch` 会撞 `ModuleNotFoundError: tools`,
# 因为 tool_dispatch 又 eagerly `from tools.bg import ...`.
# 用 proxy 把 attribute 访问延迟到实际 tool 调用时, 那时 sys.path 已就位.
class _TdProxy:
    _mod = None
    def __getattr__(self, name):
        if _TdProxy._mod is None:
            from core import tool_dispatch as m
            _TdProxy._mod = m
        return getattr(_TdProxy._mod, name)

_td = _TdProxy()


def _get_workspace():
    from lib.config import WORKSPACE as _w
    return _w


async def _do_web_fetch(url: str, max_chars: int = 6000) -> str:
    _reason = _is_ssrf_target(url)
    if _reason:
        return f"ERROR: URL blocked ({_reason})"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
            headers=headers
        ) as client:
            resp = await client.get(url)
            # [v1.2] 多编码兜底: 中国网站 (weather.com.cn / 163.com 等) 常常
            # 声明 charset 不准确. 优先按声明解, 失败再尝试 utf-8 / gbk / gb2312.
            raw_bytes = resp.content
            html = ""
            if raw_bytes:
                # 1) 从 HTML meta 标签抓编码 (更可靠)
                meta_enc = None
                try:
                    head = raw_bytes[:2048].decode("ascii", errors="ignore")
                    m = re.search(r'charset=["\']?([A-Za-z0-9_\-]+)', head, re.I)
                    if m:
                        meta_enc = m.group(1).lower()
                except Exception:
                    pass
                # 2) 按优先级尝试解码
                for enc in filter(None, [meta_enc,
                                         resp.encoding,
                                         "utf-8", "gbk", "gb18030", "gb2312"]):
                    try:
                        html = raw_bytes.decode(enc, errors="replace")
                        if html.strip():
                            break
                    except (LookupError, UnicodeDecodeError):
                        continue
            # [v1.2] 空响应显式报错, 避免上层把 "" 当结果塞给模型
            if not html or not html.strip():
                return (f"ERROR: {url} returned empty body "
                        f"(status={resp.status_code}, bytes={len(raw_bytes)}). "
                        f"Site may block non-CN IP or require JS. "
                        f"Try browser_read(url) instead.")
            if resp.status_code >= 400:
                return f"ERROR: {url} HTTP {resp.status_code}"
    except Exception as e:
        return f"ERROR fetching {url}: {e}"
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>",   " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if not text:
        return (f"ERROR: {url} fetched ok but content was all markup/whitespace. "
                f"Try browser_read(url) for JS-rendered pages.")
    return text[:max_chars]


async def tool_web_fetch(args, sid, ctx):
    return await _do_web_fetch(args["url"], args.get("max_chars", 4000)), None


async def tool_web_search(args, sid, ctx):
    # 从 tool_dispatch 拿模块级依赖 (HAS_SEARCH / HAS_BROWSER_SEARCH 是可变全局)
    _bs = _td._bs
    _ds = _td._ds
    HAS_BROWSER_SEARCH = _td.HAS_BROWSER_SEARCH
    HAS_SEARCH = _td.HAS_SEARCH
    _SEARCH_CFG = _td._SEARCH_CFG
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


async def tool_browser_search(args, sid, ctx):
    _bs = _td._bs
    HAS_BROWSER_SEARCH = _td.HAS_BROWSER_SEARCH
    # [v1.0] 真实浏览器搜索 + 截图
    if not HAS_BROWSER_SEARCH or not _bs:
        return "ERROR: browser_search module 不可用 (检查 skills/browser-automation 是否正常安装)", None
    query  = args.get("query", "")
    if not query:
        return "ERROR: browser_search 需要 query 参数", None
    engine = args.get("engine", "bing")
    top_k  = min(int(args.get("top_k", 5)), 15)
    shot   = bool(args.get("screenshot", True))
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(None, _bs.browser_search, query, engine, top_k, shot),
            timeout=45,
        )
        return r.get("message") or "ERROR: browser_search 无返回", None
    except asyncio.TimeoutError:
        return "ERROR: browser_search 超过 45s 超时", None
    except Exception as e:
        return f"ERROR: browser_search 失败: {e}", None


async def tool_github_search_issues(args, sid, ctx):
    _es = _td._es
    HAS_ERROR_SEARCH = _td.HAS_ERROR_SEARCH
    if not HAS_ERROR_SEARCH or not _es:
        return "ERROR: error_search module 不可用", None
    q = args.get("query", "")
    if not q:
        return "ERROR: github_search_issues 需要 query", None
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _es.github_search_issues, q,
                args.get("repo"), args.get("state", "all"),
                min(int(args.get("top_k", 5)), 10),
            ),
            timeout=15,
        )
        return r, None
    except asyncio.TimeoutError:
        return "ERROR: github_search_issues 超时 (15s)", None
    except Exception as e:
        return f"ERROR: {e}", None


async def tool_stackoverflow_search(args, sid, ctx):
    _es = _td._es
    HAS_ERROR_SEARCH = _td.HAS_ERROR_SEARCH
    if not HAS_ERROR_SEARCH or not _es:
        return "ERROR: error_search module 不可用", None
    q = args.get("query", "")
    if not q:
        return "ERROR: stackoverflow_search 需要 query", None
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _es.stackoverflow_search, q,
                args.get("tag"), int(args.get("top_k", 5)),
            ),
            timeout=15,
        )
        return r, None
    except asyncio.TimeoutError:
        return "ERROR: stackoverflow_search 超时 (15s)", None
    except Exception as e:
        return f"ERROR: {e}", None


async def tool_search_code_error(args, sid, ctx):
    _es = _td._es
    HAS_ERROR_SEARCH = _td.HAS_ERROR_SEARCH
    if not HAS_ERROR_SEARCH or not _es:
        return "ERROR: error_search module 不可用", None
    err = args.get("error_text", "")
    if not err:
        return "ERROR: search_code_error 需要 error_text", None
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _es.search_code_error, err,
                args.get("repo"), int(args.get("top_k", 3)),
            ),
            timeout=20,
        )
        return r, None
    except asyncio.TimeoutError:
        return "ERROR: search_code_error 超时 (20s)", None
    except Exception as e:
        return f"ERROR: {e}", None


async def tool_browser_login(args, sid, ctx):
    _bs = _td._bs
    HAS_BROWSER_SEARCH = _td.HAS_BROWSER_SEARCH
    # [P34-d-1] 真浏览器自动登录: goto → extract_form → 智能匹配 → fill → click
    if not HAS_BROWSER_SEARCH or not _bs:
        return "ERROR: browser_login 不可用 (browser_search module 缺)", None
    url = args.get("url", "")
    if not url:
        return "ERROR: browser_login 需要 url 参数", None
    try:
        from lib.browser_login import attempt_login as _alogin
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _alogin, _get_workspace(), url,
                args.get("username", ""), args.get("password", ""),
                args.get("domain"), int(args.get("timeout", 30)),
                args.get("user_hint"), args.get("submit_selector"),
            ),
            timeout=60,
        )
        msg_parts = [
            ("✅" if r.get("ok") else "❌") + " " + (r.get("msg") or ""),
            f"URL: {r.get('url', '')}",
            f"填充字段: {r.get('fields_filled', 0)}",
            f"selectors: {r.get('selectors', {})}",
        ]
        if r.get("screenshot"):
            msg_parts.append(f"截图: {r['screenshot']}")
        return "\n".join(msg_parts), None
    except asyncio.TimeoutError:
        return "ERROR: browser_login 超过 60s 超时", None
    except Exception as e:
        return f"ERROR: browser_login 失败: {e}", None


async def tool_browser_read(args, sid, ctx):
    _bs = _td._bs
    HAS_BROWSER_SEARCH = _td.HAS_BROWSER_SEARCH
    # [v1.0] 真实浏览器打开 URL → 智能提取正文 + 截图
    if not HAS_BROWSER_SEARCH or not _bs:
        return "ERROR: browser_read module 不可用", None
    url = args.get("url", "")
    if not url:
        return "ERROR: browser_read 需要 url 参数", None
    wait_for  = args.get("wait_for")
    max_chars = int(args.get("max_chars", 8000))
    shot      = bool(args.get("screenshot", True))
    try:
        loop = asyncio.get_event_loop()
        r = await asyncio.wait_for(
            loop.run_in_executor(
                None, _bs.browser_read, url, wait_for, max_chars, shot
            ),
            timeout=45,
        )
        return r.get("message") or "ERROR: browser_read 无返回", None
    except asyncio.TimeoutError:
        return "ERROR: browser_read 超过 45s 超时", None
    except Exception as e:
        return f"ERROR: browser_read 失败: {e}", None


_TOOLS = {
    "web_fetch": tool_web_fetch,
    "web_search": tool_web_search,
    "browser_search": tool_browser_search,
    "github_search_issues": tool_github_search_issues,
    "stackoverflow_search": tool_stackoverflow_search,
    "search_code_error": tool_search_code_error,
    "browser_login": tool_browser_login,
    "browser_read": tool_browser_read,
}


def names() -> set[str]:
    return set(_TOOLS.keys())


async def dispatch(name: str, args: dict, sid: str = None, ctx: dict = None):
    fn = _TOOLS.get(name)
    if not fn:
        return None  # 不处理, 让上层继续 fallback
    return await fn(args, sid, ctx or {})


# ── [S1-K] 机架契约: 暴露 TOOLS 让 plugins.registry 扫描到 ─────────────────
# run(args, ctx) 是新契约签名 (2 参); 旧 tool_xxx(args, sid, ctx) 兼容包一层.
def _wrap(fn):
    async def _run(args, ctx):
        return await fn(args or {}, (ctx or {}).get("sid"), ctx or {})
    _run.__name__ = f"run_{fn.__name__}"
    return _run

_OBJ = {"type": "object", "properties": {"query": {"type": "string"}, "url": {"type": "string"}}}

TOOLS = [
    {"name": "web_fetch",            "description": "抓取指定 URL 的正文内容",     "schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, "run": _wrap(tool_web_fetch),            "capabilities": {"cost": "cheap", "safe": True}},
    {"name": "web_search",           "description": "搜索引擎搜索关键字",           "schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, "run": _wrap(tool_web_search),         "capabilities": {"cost": "cheap", "safe": True}},
    {"name": "browser_search",       "description": "浏览器渲染搜索 (处理 JS)",     "schema": _OBJ, "run": _wrap(tool_browser_search),      "capabilities": {"cost": "medium", "safe": True}},
    {"name": "github_search_issues", "description": "搜索 GitHub Issue",           "schema": _OBJ, "run": _wrap(tool_github_search_issues), "capabilities": {"cost": "cheap", "safe": True}},
    {"name": "stackoverflow_search", "description": "搜索 StackOverflow",          "schema": _OBJ, "run": _wrap(tool_stackoverflow_search), "capabilities": {"cost": "cheap", "safe": True}},
    {"name": "search_code_error",    "description": "按报错信息搜索解决方案",         "schema": _OBJ, "run": _wrap(tool_search_code_error),   "capabilities": {"cost": "cheap", "safe": True}},
    {"name": "browser_login",        "description": "打开浏览器登录",                "schema": _OBJ, "run": _wrap(tool_browser_login),       "capabilities": {"cost": "medium", "safe": True, "auth_required": True}},
    {"name": "browser_read",         "description": "读取当前浏览器页面正文",         "schema": _OBJ, "run": _wrap(tool_browser_read),        "capabilities": {"cost": "cheap", "safe": True}},
]
VERSION = "0.1.0"
