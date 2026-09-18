"""
browser_search.py — v1.0 浏览器搜索 + 智能网页抓取
========================================================
作为 web_search / HTML 抓取失败后的兜底策略，用真实浏览器 (Playwright) 操作搜索引擎并
抓取页面，突破反爬 + 拿到 JS 渲染后的结果。

两个核心函数:
  - browser_search(query, engine, top_k)
      真实浏览器搜索 → 截图 + 结构化 top-K 结果 (title/url/snippet)

  - browser_read(url)
      真实浏览器打开页面 → 截图 + 提取主正文 (readability 启发式)

依赖: 复用 skills/browser-automation/browser_server.py (Playwright + FastAPI @ :19000)
      服务没起会自动 ensure_server() 启动, 起过的直接复用 context + page。

设计原则:
  - 轻量: 一次请求 = 一次 HTTP 调用到 browser_server, 不直接起 Playwright
  - 持久会话: session 名 "search-bot" / "reader-bot", cookie/storage 跨调用复用
  - 截图保存: 输出到 workspace/screenshots/, 供前端 / agent 复查
  - 对 LLM 友好: 返回 Markdown + JSON 混合, 方便模型继续决策
"""
from __future__ import annotations
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urlparse

try:
    import httpx
except ImportError:
    httpx = None

_BROWSER_PORT = int(os.environ.get("BROWSER_AGENT_PORT", 19000))
_BROWSER_URL = f"http://127.0.0.1:{_BROWSER_PORT}"

# [2026-05-25 Node 替代 Python Playwright]
# 原 browser_server.py (Python + Playwright + 自带 chromium-1223) 撞 npm 镜像被墙,
# 改用 browser_server.js (Node + puppeteer-core 22 + 系统 google-chrome).
# 优势:
#  1. 单容器无新依赖 (复用容器内已装 node + google-chrome)
#  2. 不下 chromium binary (省 200MB + 国内访问障碍)
#  3. 单例 + 用完即关 (idle 5min 自动 close ctx, 见 browser_server.js _cleanupSession)
#  4. /cleanup endpoint 主动关 session
def _resolve_browser_server_path() -> tuple:
    """返回 (启动命令 list, 描述). 优先找 Node 版, 回退 Python 老版."""
    # 1) Node 版优先 (在 /opt/browser_node/ 装了 puppeteer-core + express)
    node_js = Path("/opt/browser_node/browser_server.js")
    if node_js.exists():
        return (["node", str(node_js)], f"node:{node_js}")
    # 1b) Node 版也可能在 skills/ 下 (host mount 同步过去)
    node_alt = Path(__file__).resolve().parent.parent / "skills" / "browser-automation" / "browser_server.js"
    if node_alt.exists():
        return (["node", str(node_alt)], f"node:{node_alt}")
    # 2) 老 Python 版 fallback (chromium 装好的环境下还能用)
    env_p = os.environ.get("BROWSER_SERVER_PATH", "")
    if env_p and Path(env_p).exists():
        return ([sys.executable, env_p], f"python:{env_p}")
    home = os.environ.get("LITECODE_HOME", "/opt/litecode")
    p = Path(home) / "skills" / "browser-automation" / "browser_server.py"
    if p.exists():
        return ([sys.executable, str(p)], f"python:{p}")
    p2 = Path(__file__).resolve().parent.parent / "skills" / "browser-automation" / "browser_server.py"
    if p2.exists():
        return ([sys.executable, str(p2)], f"python:{p2}")
    return ([], "<not-found>")

_BROWSER_LAUNCH_CMD, _BROWSER_DESC = _resolve_browser_server_path()

# 截图保存目录
_WORKSPACE = Path(os.environ.get("WORKSPACE", "/tmp/litecode_workspace"))
_SHOT_DIR = _WORKSPACE / "screenshots"


# ═══════════════════════════════════════════════════════
# Search engine 配置 (selector 按经验给, 失败时走通用回退)
# ═══════════════════════════════════════════════════════
_ENGINES = {
    # [2026-05-22] google: 海外用户首选, 容器 IP 在境外才稳;
    # selector 用 div.g + h3, 是 google 稳定 5+ 年的结构.
    # url 加 hl=en 防被推断节点转日文; pws=0 关个性化; num=10 一页 10 条
    "google": {
        "url": "https://www.google.com/search?q={q}&hl=en&pws=0&num=10",
        "wait": "div#search,div[role=main]",
        "result_sel": "div#search div.g, div#rso div.g, div[data-snc] div.g",
        "fields": {
            "title":   "h3",
            "url":     "a@href",
            "snippet": "div[data-sncf='1'], .VwiC3b, [data-snhf='0']",
        },
    },
    "bing": {
        "url": "https://www.bing.com/search?q={q}",
        "wait": "#b_results",
        "result_sel": "#b_results > li.b_algo",
        "fields": {
            "title":   "h2 a",
            "url":     "h2 a@href",
            "snippet": ".b_caption p",
        },
    },
    "duckduckgo": {
        "url": "https://duckduckgo.com/?q={q}",
        "wait": "article[data-testid='result']",
        "result_sel": "article[data-testid='result']",
        "fields": {
            "title":   "h2 a",
            "url":     "h2 a@href",
            "snippet": "[data-result='snippet']",
        },
    },
    "baidu": {
        "url": "https://www.baidu.com/s?wd={q}",
        "wait": "#content_left",
        "result_sel": ".result.c-container",
        "fields": {
            "title":   "h3 a",
            "url":     "h3 a@href",
            "snippet": ".content-right_2s-H4,.c-abstract",
        },
    },
    "sogou": {
        "url": "https://www.sogou.com/web?query={q}",
        "wait": "#main",
        "result_sel": ".vrwrap, .rb",
        "fields": {
            "title":   "h3 a",
            "url":     "h3 a@href",
            "snippet": ".fz-mid, .space-txt",
        },
    },
}


# ═══════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════
_BROWSER_LAST_ERR: str = ""

def _ensure_browser_server(timeout: float = 10.0) -> bool:
    """确保 browser_server 在 :19000 跑。已跑 → True; 起得来 → True; 起不来 → False。
    [FIX] 记录最近一次失败原因到 _BROWSER_LAST_ERR, 供 browser_search / browser_read 透传给 agent。
    """
    global _BROWSER_LAST_ERR
    if httpx is None:
        _BROWSER_LAST_ERR = "httpx 未安装 (pip install httpx)"
        return False
    try:
        r = httpx.get(f"{_BROWSER_URL}/health", timeout=2.0)
        if r.status_code == 200:
            _BROWSER_LAST_ERR = ""
            return True
    except Exception as _he:
        _BROWSER_LAST_ERR = f"health 检查失败: {_he}"
    # 不在跑 → 启动 (Node 版或 Python 老版, 由 _resolve_browser_server_path 决定)
    if not _BROWSER_LAUNCH_CMD:
        _BROWSER_LAST_ERR = f"browser_server 未找到 (检索: node ./browser_server.js / python browser_server.py 均不存在)"
        return False
    log_path = Path("/tmp/browser_server.log")
    log = log_path.open("a")
    try:
        # [2026-05-25] Node 版需要在 /opt/browser_node 目录跑 (node_modules 在那)
        _cwd = "/opt/browser_node" if _BROWSER_DESC.startswith("node:") else None
        subprocess.Popen(
            _BROWSER_LAUNCH_CMD,
            stdout=log, stderr=log,
            cwd=_cwd,
            env={**os.environ, "BROWSER_AGENT_PORT": str(_BROWSER_PORT)},
            start_new_session=True,   # 完全 daemon 化, 不随父进程退出
        )
    except Exception as _spe:
        _BROWSER_LAST_ERR = f"subprocess.Popen 失败 ({_BROWSER_DESC}): {_spe}"
        return False
    # 等健康检查
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(f"{_BROWSER_URL}/health", timeout=1.5).status_code == 200:
                _BROWSER_LAST_ERR = ""
                return True
        except Exception:
            pass
        time.sleep(0.5)
    # 超时: 尝试捞日志尾巴
    _tail = ""
    try:
        if log_path.exists():
            _tail = log_path.read_text(errors="replace")[-600:]
    except Exception:
        pass
    _BROWSER_LAST_ERR = (
        f"{timeout}s 内 http://127.0.0.1:{_BROWSER_PORT}/health 未就绪"
        + (f"\n[log tail]\n{_tail}" if _tail else "")
    )
    return False


def _post_act(action: str, params: dict, session: str = "default",
              wait_ms: int = 800, want_shot: bool = True,
              timeout: float = 30.0) -> dict:
    """同步调用 browser_server 的 /act 端点。返回 {ok, msg, data, b64, url}."""
    if httpx is None:
        return {"ok": False, "msg": "httpx not installed", "data": None, "b64": None, "url": ""}
    try:
        r = httpx.post(
            f"{_BROWSER_URL}/act",
            json={
                "action": action, "params": params, "session": session,
                "wait": wait_ms, "shot": want_shot,
            },
            timeout=timeout,
        )
        return r.json()
    except Exception as e:
        return {"ok": False, "msg": f"browser_server call failed: {e}",
                "data": None, "b64": None, "url": ""}


def _save_b64_png(b64: str, prefix: str = "shot") -> Optional[str]:
    """把 base64 截图存到 workspace/screenshots/, 返回绝对路径。"""
    if not b64:
        return None
    try:
        _SHOT_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{prefix}-{int(time.time()*1000)}.png"
        p = _SHOT_DIR / fname
        p.write_bytes(base64.b64decode(b64))
        return str(p)
    except Exception:
        return None


def _readable_script() -> str:
    """纯 JS readability: 找最大正文区域, 返回 title + text。无外部依赖。"""
    return r"""
() => {
  // 1. 标题
  const title = (document.querySelector('h1')?.innerText || document.title || '').trim();
  // 2. 候选正文容器: 按文字密度 * 面积 打分
  const candidates = Array.from(document.querySelectorAll(
    'article, main, [role=main], .article, .post, .content, #content, #main, ' +
    '.article-content, .post-content, .entry-content'
  ));
  let best = null, bestScore = 0;
  for (const el of candidates) {
    const text = (el.innerText || '').trim();
    const score = text.length;
    if (score > bestScore) { best = el; bestScore = score; }
  }
  // 3. 没有语义标签 → 回退到 body 里最长文字的 div
  if (!best || bestScore < 400) {
    const divs = Array.from(document.querySelectorAll('body div'));
    for (const el of divs) {
      const text = (el.innerText || '').trim();
      if (text.length > bestScore) { best = el; bestScore = text.length; }
    }
  }
  const body = (best?.innerText || document.body?.innerText || '').trim();
  // 4. 去掉连续空行
  const cleaned = body.replace(/\n{3,}/g, '\n\n').slice(0, 15000);
  return { title: title.slice(0, 300), text: cleaned, chars: cleaned.length };
}
"""


# ═══════════════════════════════════════════════════════
# [v1.4] 视觉兜底: CSS 选择器失败时, 用多模态 LLM 读搜索结果截图
# ═══════════════════════════════════════════════════════
def _vision_extract_from_screenshot(b64_png: Optional[str], *,
                                     engine: str, query: str,
                                     top_k: int = 5) -> Optional[dict]:
    """把截图丢给支持视觉的主模型, 让它直接从图里抽结果.
    返回 {"results": [...], "message": "Markdown 摘要"} 或 None (失败).
    只在有 b64_png + 能找到多模态 backend + 能调通时才返回.
    """
    if not b64_png or httpx is None:
        return None
    # 找主模型或第一个 supports_vision 模型的 backend (复用 wechat_bridge 思路)
    try:
        from pathlib import Path as _P
        import json as _json
        cfg_path = _P(__file__).parent / "config.json"
        if not cfg_path.exists():
            return None
        cfg = _json.loads(cfg_path.read_text())
    except Exception:
        return None

    # 候选: 活跃主 model → models[] 里 supports_vision 的第一个
    candidates = []
    main = cfg.get("model", {}) or {}
    if main.get("supports_vision"):
        candidates.append(main)
    for m in cfg.get("models", []) or []:
        if m.get("supports_vision") and m not in candidates:
            candidates.append(m)
    if not candidates:
        return None

    prompt = (
        f"这是搜索引擎 ({engine}) 搜 '{query}' 的结果页截图.\n"
        f"请从图中抽出前 {top_k} 条搜索结果, 按顺序输出纯 JSON 数组, "
        f"每项格式: {{\"title\": \"...\", \"url\": \"...\", \"snippet\": \"...\"}}. "
        f"只输出 JSON, 不要解释. 如果图里没有可用结果 (出验证码/空页) "
        f"输出 []."
    )
    payload_tmpl = {
        "max_tokens": 1024,
        "temperature": 0.1,
        "stream": False,
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64_png}"}},
            {"type": "text", "text": prompt},
        ]}],
    }

    import json as _json
    for mdl in candidates[:2]:   # 最多试 2 个模型
        try:
            url = mdl.get("backend_url", "").rstrip("/")
            if not url:
                continue
            key = mdl.get("api_key", "EMPTY")
            payload = dict(payload_tmpl)
            payload["model"] = mdl["id"]
            r = httpx.post(
                f"{url}/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json=payload, timeout=45.0,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            content = (data.get("choices", [{}])[0]
                           .get("message", {}).get("content") or "")
            # 剥 <think>...</think>
            content = re.sub(r"<think>.*?</think>", "", content,
                             flags=re.S | re.I).strip()
            # 去 code fence
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content,
                             flags=re.M).strip()
            # 找 JSON 数组
            m = re.search(r"\[[\s\S]*\]", content)
            if not m:
                continue
            try:
                parsed = _json.loads(m.group(0))
            except Exception:
                continue
            if not isinstance(parsed, list):
                continue
            results = []
            for it in parsed[:top_k]:
                if not isinstance(it, dict):
                    continue
                t = str(it.get("title", "")).strip()[:200]
                u = str(it.get("url", "")).strip()
                s = str(it.get("snippet", "")).strip()[:400]
                if t and u and u.startswith(("http://", "https://")):
                    results.append({"title": t, "url": u, "snippet": s})
            if not results:
                continue
            lines = [f"## 浏览器搜索结果 ({engine}, {len(results)} 条, 视觉兜底)"]
            for i, rr in enumerate(results, 1):
                lines.append(
                    f"[{i}] **{rr['title']}**\n    URL: {rr['url']}"
                    + (f"\n    {rr['snippet']}" if rr['snippet'] else "")
                )
            return {"results": results, "message": "\n\n".join(lines)}
        except Exception:
            continue
    return None


# ═══════════════════════════════════════════════════════
# 核心: 浏览器搜索
# ═══════════════════════════════════════════════════════
def browser_search(query: str, engine: str = "bing", top_k: int = 5,
                   save_screenshot: bool = True) -> dict:
    """
    用真实浏览器搜索。
    Args:
      query:  搜索词
      engine: bing / duckduckgo / baidu / sogou
      top_k:  返回结果数 (默认 5)
      save_screenshot: 是否保存截图到 workspace/screenshots/

    Returns:
      {
        "ok": True/False,
        "engine": "bing",
        "query": "...",
        "results": [{"title":..., "url":..., "snippet":...}, ...],
        "screenshot": "/path/to/shot.png",  # 或 None
        "message": "Markdown formatted summary",
      }
    """
    if not _ensure_browser_server():
        return {"ok": False, "message": f"ERROR: browser_server 启动失败: {_BROWSER_LAST_ERR}"}

    cfg = _ENGINES.get(engine.lower(), _ENGINES["bing"])
    url = cfg["url"].replace("{q}", quote_plus(query))
    session = "search-bot"

    # 1. 打开搜索 URL (不截图, 省带宽)
    goto = _post_act("goto", {"url": url}, session=session, wait_ms=0, want_shot=False)
    if not goto.get("ok"):
        return {"ok": False, "message": f"ERROR: goto 失败: {goto.get('msg','')}"}

    # 2. 智能等待结果容器
    _post_act("smart_wait", {"sel": cfg["wait"], "timeout": 8000},
              session=session, wait_ms=0, want_shot=False)

    # 3. 抓取 top-K 结果
    scrape = _post_act("scrape", {
        "item_sel": cfg["result_sel"], "fields": cfg["fields"], "limit": top_k,
    }, session=session, wait_ms=0, want_shot=False)

    results = []
    # [v1.2] Bing 的 /ck/a?!&u=aXXX 是广告/追踪跳转, 但 u= 参数里藏着真实 URL
    # (格式: 前缀 "a1" + base64-urlsafe 的原始 URL). 能解出来就保留, 解不出才丢.
    import base64 as _b64, re as _re
    def _unwrap_bing_ck(url: str) -> str:
        try:
            m = _re.search(r"[?&]u=([A-Za-z0-9\-_%]+)", url)
            if not m:
                return ""
            blob = m.group(1)
            # urlsafe base64 里不会出现 %, 但有时被 encodeURIComponent 过
            from urllib.parse import unquote as _unq
            blob = _unq(blob)
            # Bing 格式: 前两位是前缀 ("a1"), 后面才是 base64
            if len(blob) > 2:
                blob = blob[2:]
            # 补齐 padding
            blob += "=" * (-len(blob) % 4)
            decoded = _b64.urlsafe_b64decode(blob).decode("utf-8", errors="ignore")
            if decoded.startswith(("http://", "https://")):
                return decoded
        except Exception:
            return ""
        return ""

    def _is_useless_url(u: str) -> bool:
        if not u:
            return True
        ul = u.lower()
        # Bing 广告跳转: 交给 _unwrap_bing_ck 试一下, 解不出才丢
        if "bing.com/aclk" in ul:
            return True
        # 其它引擎跳转/广告
        if "google.com/aclk" in ul or "baidu.com/link?" in ul:
            return True
        # 搜索引擎自引 (导航/登录/关于等)
        if any(p in ul for p in ("bing.com/search", "bing.com/images",
                                  "bing.com/videos", "bing.com/maps",
                                  "bing.com/account", "bing.com/rewards",
                                  "microsoft.com/en-us/bing", "microsoft.com/privacy")):
            return True
        return False

    if scrape.get("ok") and isinstance(scrape.get("data"), list):
        for it in scrape["data"][:top_k * 3]:   # 多抓, 过滤后再截断
            title = (it.get("title") or "").strip()
            url_ = (it.get("url") or "").strip()
            snippet = (it.get("snippet") or "").strip()
            # 先尝试解 Bing 的 /ck/a 跳转
            if "bing.com/ck/a" in url_.lower():
                real = _unwrap_bing_ck(url_)
                if real:
                    url_ = real
                else:
                    continue   # 解不出就放弃
            if not title or not url_ or _is_useless_url(url_):
                continue
            results.append({"title": title[:200], "url": url_, "snippet": snippet[:400]})
            if len(results) >= top_k:
                break

    # 4. 截图 (单独一次请求, 避免失败污染结果)
    shot_path = None
    shot_b64 = None
    if save_screenshot or not results:   # 结构化失败时强制截图, 留作视觉兜底
        shot_resp = _post_act("shot", {}, session=session, wait_ms=0, want_shot=True)
        if shot_resp.get("ok") and shot_resp.get("b64"):
            shot_b64 = shot_resp["b64"]
            shot_path = _save_b64_png(shot_b64, prefix=f"search-{engine}")

    # 5. 组装 Markdown message
    if not results:
        # [v1.4] 结构化抓取失败 → 视觉兜底: 把截图丢给多模态 LLM, 让它
        # 直接"看"搜索结果页读 top-K. 选择器失效、JS 渲染延迟、反爬都能救.
        vision_fallback = _vision_extract_from_screenshot(
            shot_b64, engine=engine, query=query, top_k=top_k
        )
        if vision_fallback:
            return {
                "ok": True, "engine": engine, "query": query,
                "results": vision_fallback.get("results", []),
                "screenshot": shot_path,
                "message": vision_fallback.get("message", ""),
                "source": "vision_fallback",
            }
        return {
            "ok": False,
            "engine": engine, "query": query, "results": [], "screenshot": shot_path,
            "message": (
                f"ERROR: browser_search({engine}) 抓到 0 条结果 "
                f"(选择器失效 + 视觉兜底也失败). 截图: {shot_path or '(无)'}"
            ),
        }
    lines = [f"## 浏览器搜索结果 ({engine}, {len(results)} 条)"]
    if shot_path:
        lines.append(f"📷 截图: `{shot_path}`")
    for i, r in enumerate(results, 1):
        lines.append(
            f"[{i}] **{r['title']}**\n    URL: {r['url']}"
            + (f"\n    {r['snippet'][:300]}" if r['snippet'] else "")
        )
    return {
        "ok": True, "engine": engine, "query": query, "results": results,
        "screenshot": shot_path, "message": "\n\n".join(lines),
    }


# ═══════════════════════════════════════════════════════
# 核心: 浏览器读页面 (智能正文提取)
# ═══════════════════════════════════════════════════════
def browser_read(url: str, wait_for: Optional[str] = None,
                 max_chars: int = 8000, save_screenshot: bool = True) -> dict:
    """
    用真实浏览器打开 URL，等 DOM 稳定 → 截图 → 提取主正文。
    绕过反爬、处理 JS 渲染、抓取到真正用户看到的内容。

    Args:
      url:        目标 URL
      wait_for:   可选的 CSS 选择器, 等它出现才算页面好
      max_chars:  正文最大字符数 (默认 8000, 够一篇长文)
      save_screenshot: 保存截图到 workspace/screenshots/

    Returns:
      {
        "ok":    True/False,
        "url":   final URL (跟随重定向后),
        "title": "...",
        "text":  "主正文 (已去噪, 纯文本)",
        "screenshot": "/path/to/shot.png",
        "message": "Markdown summary",
      }
    """
    if not _ensure_browser_server():
        return {"ok": False, "message": f"ERROR: browser_server 启动失败: {_BROWSER_LAST_ERR}"}
    if not url or not urlparse(url).scheme:
        return {"ok": False, "message": f"ERROR: 无效 URL: {url!r}"}

    session = "reader-bot"
    goto = _post_act("goto", {"url": url}, session=session, wait_ms=0, want_shot=False)
    if not goto.get("ok"):
        return {"ok": False, "message": f"ERROR: goto 失败: {goto.get('msg','')}"}

    # 等指定元素或 DOM 稳定
    if wait_for:
        _post_act("smart_wait", {"sel": wait_for, "timeout": 10000},
                  session=session, wait_ms=0, want_shot=False)
    else:
        _post_act("wait_dom_stable", {"stable_ms": 800, "timeout": 8000},
                  session=session, wait_ms=0, want_shot=False)

    # 执行 readability JS
    ev = _post_act("eval", {"script": _readable_script()},
                   session=session, wait_ms=0, want_shot=False)
    title, text = "", ""
    if ev.get("ok") and isinstance(ev.get("data"), dict):
        title = (ev["data"].get("title") or "").strip()[:300]
        text = (ev["data"].get("text") or "").strip()[:max_chars]

    # 回退: 抓不到走 get_text body
    if not text or len(text) < 100:
        body = _post_act("get_text", {"sel": "body"},
                         session=session, wait_ms=0, want_shot=False)
        if body.get("ok") and body.get("data"):
            text = re.sub(r"\n{3,}", "\n\n", str(body["data"])).strip()[:max_chars]

    # 截图
    shot_path = None
    if save_screenshot:
        shot_resp = _post_act("shot", {}, session=session, wait_ms=0, want_shot=True)
        if shot_resp.get("ok") and shot_resp.get("b64"):
            shot_path = _save_b64_png(shot_resp["b64"], prefix="read")

    final_url = goto.get("url") or url
    if not text:
        return {
            "ok": False, "url": final_url, "title": title, "text": "",
            "screenshot": shot_path,
            "message": f"ERROR: browser_read 未能提取到正文. 截图: {shot_path or '(无)'}",
        }

    lines = [f"## {title or final_url}"]
    if shot_path:
        lines.append(f"📷 截图: `{shot_path}`")
    lines.append(f"📍 `{final_url}`")
    lines.append(f"\n{text}")
    return {
        "ok": True, "url": final_url, "title": title, "text": text,
        "screenshot": shot_path, "message": "\n".join(lines),
    }


# ═══════════════════════════════════════════════════════
# [2026-05-25] SearXNG 本机 sidecar — JSON API, 不走 Playwright, 永不被反爬
# 部署: docker-compose 起 searxng/searxng + host network, 默认 127.0.0.1:18888
# 配置端口由 env SEARXNG_URL 覆盖 (默认 http://127.0.0.1:18888)
# ═══════════════════════════════════════════════════════
_SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:18888").rstrip("/")
_SEARXNG_TIMEOUT = float(os.environ.get("SEARXNG_TIMEOUT", "8"))


def searxng_search(query: str, top_k: int = 8,
                   categories: str = "general",
                   language: str = "auto") -> dict:
    """走本机 SearXNG JSON API 搜索 — 聚合 70+ 搜索源, 不被反爬.
    Returns: {ok, query, results: [{title,url,snippet,engine}], message, source}
    """
    if httpx is None:
        return {"ok": False, "message": "ERROR: httpx 未安装", "results": []}
    try:
        params = {
            "q": query,
            "format": "json",
            "categories": categories,
            "language": language,
            "safesearch": "0",
        }
        r = httpx.get(f"{_SEARXNG_URL}/search", params=params, timeout=_SEARXNG_TIMEOUT)
        if r.status_code != 200:
            return {
                "ok": False, "query": query, "results": [],
                "message": f"ERROR: SearXNG HTTP {r.status_code} @ {_SEARXNG_URL}",
            }
        data = r.json()
    except Exception as e:
        return {
            "ok": False, "query": query, "results": [],
            "message": f"ERROR: SearXNG 调用失败: {e}",
        }
    raw = data.get("results") or []
    if not raw:
        return {
            "ok": False, "query": query, "results": [],
            "message": "SearXNG: 0 条结果 (查询无命中或上游源都拉空)",
        }
    results = []
    seen_urls = set()
    for it in raw:
        u = (it.get("url") or "").strip()
        if not u or u in seen_urls:
            continue
        seen_urls.add(u)
        results.append({
            "title":   (it.get("title") or "").strip()[:200],
            "url":     u,
            "snippet": (it.get("content") or "").strip()[:400],
            "engine":  (it.get("engine") or "").strip(),
        })
        if len(results) >= top_k:
            break
    if not results:
        return {
            "ok": False, "query": query, "results": [],
            "message": "SearXNG: 结果全被去重/空 URL 过滤掉",
        }
    lines = [f"## SearXNG 搜索结果 ({len(results)} 条, 聚合本机)"]
    for i, rr in enumerate(results, 1):
        eng_tag = f" [{rr['engine']}]" if rr['engine'] else ""
        lines.append(
            f"[{i}]{eng_tag} **{rr['title']}**\n    URL: {rr['url']}"
            + (f"\n    {rr['snippet']}" if rr['snippet'] else "")
        )
    return {
        "ok": True, "query": query, "results": results,
        "message": "\n\n".join(lines),
        "source": "searxng",
    }


def searxng_health() -> bool:
    """SearXNG 是否可达 (短超时, 用于 web_search 入口判断)."""
    if httpx is None:
        return False
    try:
        r = httpx.get(f"{_SEARXNG_URL}/", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


# ═══════════════════════════════════════════════════════
# 兜底接入 web_search: deep_search 失败 → 浏览器搜索
# ═══════════════════════════════════════════════════════
def browser_search_fallback(query: str, engine: str = "bing") -> Optional[str]:
    """web_search 所有源都失败时的兜底。返回 Markdown 字符串或 None."""
    try:
        r = browser_search(query, engine=engine, top_k=5, save_screenshot=False)
        if r.get("ok"):
            return r.get("message")
    except Exception:
        pass
    return None
