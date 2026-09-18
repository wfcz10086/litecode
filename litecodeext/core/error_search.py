"""
error_search.py — v1.0.3 代码错误语义搜索
==========================================
遇到代码报错时, 优先用结构化 API 搜已知问题, 比 web_search 快/准:

  1. github_search_issues(query, repo=None, state='all')
     → GitHub Issues Search API (公开, 无 KEY 60 req/hr)
     → 精确匹配 "error message" 到相关仓库的 issue/PR

  2. stackoverflow_search(query, tag=None)
     → Stack Exchange public API (无 KEY 300 req/day)
     → 按投票排序, 返回 top-5 问答

  3. search_code_error(error_text)  高层入口
     → 自动从 traceback 提取 signature (异常类 + 最后一行错误消息)
     → 先 github, 再 stackoverflow, 合并 top-K 结果

全部返回 Markdown 格式, 供 LLM 做根因判断。
"""
from __future__ import annotations
import re
import time
from typing import Optional
from urllib.parse import quote_plus

try:
    import httpx
except ImportError:
    httpx = None

_GH_API  = "https://api.github.com/search/issues"
_SO_API  = "https://api.stackexchange.com/2.3/search/advanced"
_UA      = "LiteCode/1.0 (error-search)"


# ═══════════════════════════════════════════════════════
# GitHub Issues 搜索
# ═══════════════════════════════════════════════════════
def github_search_issues(query: str, repo: Optional[str] = None,
                         state: str = "all", top_k: int = 5,
                         timeout: float = 8.0) -> str:
    """
    搜 GitHub issue/PR. 无 auth 时限 60 req/hr (够用)。

    Args:
      query: 搜索词 (报错片段 / 函数名 / 库名+行为)
      repo:  可选 owner/name, 限制只搜这个仓库
      state: all / open / closed
      top_k: 返回数 (默认 5)
    """
    if httpx is None:
        return "ERROR: httpx 不可用"
    q = query.strip()
    if not q:
        return "ERROR: github_search_issues 需要 query"
    if repo:
        q = f"{q} repo:{repo}"
    if state in ("open", "closed"):
        q = f"{q} state:{state}"
    try:
        r = httpx.get(
            _GH_API,
            params={"q": q, "sort": "reactions", "order": "desc", "per_page": top_k},
            timeout=timeout,
            headers={"User-Agent": _UA, "Accept": "application/vnd.github+json"},
        )
        if r.status_code == 403:
            return ("ERROR: GitHub API rate limit 超了 (60 req/hr). "
                    "稍后再试, 或用 web_search 搜类似的问题。")
        if r.status_code != 200:
            return f"ERROR: GitHub API HTTP {r.status_code}: {r.text[:150]}"
        d = r.json()
        items = d.get("items") or []
        total = d.get("total_count", 0)
        if not items:
            return f"GitHub: 0 条相关 issue (query: {q[:60]})"
        lines = [f"## GitHub Issues (top {len(items)}/{total} for: {q[:80]})"]
        for i, it in enumerate(items[:top_k], 1):
            title   = (it.get("title") or "").strip()[:160]
            state_  = it.get("state", "?")
            is_pr   = "pull_request" in it
            repo_   = it.get("repository_url", "").replace("https://api.github.com/repos/", "")
            reacts  = (it.get("reactions") or {}).get("total_count", 0)
            comments = it.get("comments", 0)
            body    = (it.get("body") or "").strip().replace("\r", "")
            # 截短正文, 避免撑爆 context
            body_preview = re.sub(r"\n{3,}", "\n\n", body)[:400]
            icon = "🔀" if is_pr else "🐛"
            lines.append(
                f"[{i}] {icon} **{title}**  [{state_}, 👍{reacts}, 💬{comments}]\n"
                f"    {repo_}  →  {it.get('html_url')}\n"
                + (f"    {body_preview}" if body_preview else "")
            )
        return "\n\n".join(lines)
    except httpx.TimeoutException:
        return f"ERROR: GitHub API 超过 {timeout}s 超时"
    except Exception as e:
        return f"ERROR: github_search_issues 失败: {e}"


# ═══════════════════════════════════════════════════════
# StackOverflow 搜索
# ═══════════════════════════════════════════════════════
def stackoverflow_search(query: str, tag: Optional[str] = None,
                         top_k: int = 5, timeout: float = 8.0) -> str:
    """Stack Exchange public API. 按投票排序, 返回 top_k 问答。"""
    if httpx is None:
        return "ERROR: httpx 不可用"
    if not query.strip():
        return "ERROR: stackoverflow_search 需要 query"
    params = {
        "order": "desc", "sort": "votes", "q": query,
        "site": "stackoverflow", "pagesize": top_k,
        "filter": "!-*jbN-o8P3E5",  # 带 answer_count / score / link 的字段集
    }
    if tag:
        params["tagged"] = tag
    try:
        r = httpx.get(_SO_API, params=params, timeout=timeout,
                      headers={"User-Agent": _UA})
        if r.status_code == 400:
            return f"ERROR: StackOverflow API 参数错误: {r.text[:150]}"
        if r.status_code != 200:
            return f"ERROR: StackOverflow API HTTP {r.status_code}"
        d = r.json()
        items = d.get("items") or []
        total = d.get("total", len(items))
        if not items:
            return f"StackOverflow: 0 条相关问答 (query: {query[:60]})"
        lines = [f"## StackOverflow (top {len(items)} for: {query[:80]})"]
        for i, it in enumerate(items[:top_k], 1):
            title   = (it.get("title") or "").strip()[:160]
            score   = it.get("score", 0)
            answers = it.get("answer_count", 0)
            has_ans = "✅" if it.get("is_answered") else "❓"
            link    = it.get("link", "")
            tags    = ", ".join((it.get("tags") or [])[:4])
            lines.append(
                f"[{i}] {has_ans} **{title}**  [👍{score}, 💬{answers}]\n"
                f"    tags: {tags}\n"
                f"    {link}"
            )
        return "\n\n".join(lines)
    except httpx.TimeoutException:
        return f"ERROR: StackOverflow API 超过 {timeout}s 超时"
    except Exception as e:
        return f"ERROR: stackoverflow_search 失败: {e}"


# ═══════════════════════════════════════════════════════
# 高层: 从报错自动抽 signature 并搜
# ═══════════════════════════════════════════════════════
_TRACE_SIG_PATTERNS = [
    # Python: 最后一行 "ExceptionType: message"
    (re.compile(r"([A-Z][A-Za-z]*(?:Error|Exception|Warning)):\s*(.+?)(?:\n|$)"), "py"),
    # Node/JS: "TypeError: Cannot read properties of undefined"
    (re.compile(r"((?:Type|Reference|Syntax|Range)Error):\s*(.+?)(?:\n|$)"), "js"),
    # Go: "panic: runtime error: ..."
    (re.compile(r"panic:\s*(.+?)(?:\n|$)"), "go"),
    # Rust: "thread '.*' panicked at '...'"
    (re.compile(r"panicked at '(.+?)'"), "rust"),
]


def extract_error_signature(error_text: str) -> tuple[str, str]:
    """
    从报错文本抽 (查询字符串, 语言标签). 优先最后一次出现的 signature.
    """
    if not error_text:
        return "", ""
    text = error_text[-3000:]  # 通常最后的才是真正的根因
    best = None
    for pat, lang in _TRACE_SIG_PATTERNS:
        for m in pat.finditer(text):
            best = (m.group(0).strip(), lang)  # 取最后一个 match
    if not best:
        # 兜底: 取 "Error:" 或 "error:" 所在行
        for line in reversed(text.splitlines()):
            if "error:" in line.lower() or "Error:" in line:
                return line.strip()[:200], ""
        return "", ""
    sig, lang = best
    # 去掉行号 / 路径噪声, 保留异常类 + 消息
    sig = re.sub(r"\s+", " ", sig).strip()[:200]
    return sig, lang


def search_code_error(error_text: str, repo: Optional[str] = None,
                      top_k: int = 3) -> str:
    """
    高层接口: 从 traceback 抽 signature, 并行查 github + stackoverflow,
    合并 top 结果返回 Markdown。

    Args:
      error_text: 报错文本 (stderr / traceback)
      repo:       可选, 只搜特定 github 仓库
      top_k:      各源返回数 (默认 3)
    """
    sig, lang = extract_error_signature(error_text)
    if not sig:
        return ("ERROR: 未能从错误文本抽出有效 signature. "
                "请直接用 github_search_issues 或 stackoverflow_search 提供关键词。")
    lang_tag = {"py": "python", "js": "javascript", "go": "go", "rust": "rust"}.get(lang)
    lines = [f"# 错误根因搜索: `{sig[:120]}`"]
    gh = github_search_issues(sig, repo=repo, top_k=top_k)
    so = stackoverflow_search(sig, tag=lang_tag, top_k=top_k)
    lines.append(gh)
    lines.append(so)
    return "\n\n---\n\n".join(lines)
