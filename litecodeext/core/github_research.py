"""github_research.py — GitHub 搜索 / 仓库研读 (不 clone 就能看别人怎么实现)。

两个对外入口 (同步函数, 由 core/tools/handlers/code_research.py 放线程池跑):
  github_search(kind, query, ...)  kind = repositories | code | issues | commits | topics
  github_repo(action, repo, ...)   action = info | readme | tree | file | releases | commits | search_in_repo

后端与限额:
  api.github.com   无 token: core 60 次/小时, search 10 次/分钟, code search 不可用 (要求登录)
                   有 token (config.json github.token 或 env GITHUB_TOKEN): core 5000/小时, search 30/分钟, 可搜代码
  raw.githubusercontent.com  读 README / 文件, 不占 API 配额
  grep.app         无 token 时的代码搜索首选 (Vercel 风控时自动跳过 10 分钟)
  sourcegraph.com  无 token 代码搜索兜底 (公开 GraphQL, 匿名可用)

限流: 读 X-RateLimit-Remaining/Reset, 403/429 时记下重置时间, 重置前同一桶直接返回说明, 不重试空转。
缓存: 同参数 10 分钟内直接返回 (只缓存成功结果)。超时 15s。
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
GREPAPP = "https://grep.app/api/search"
SOURCEGRAPH = "https://sourcegraph.com/.api/graphql"
UA = "LiteCode/1.0 (code-research)"
TIMEOUT = 15.0
CACHE_TTL = 600
MAX_TEXT = 20000          # readme / file 返回上限 (头 + 尾)
MAX_RAW_BYTES = 3_000_000

_TRANSPORT = None          # 测试注入 httpx.MockTransport
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

_LOCK = threading.Lock()
_CACHE: dict = {}          # key -> (ts, text)
_BLOCKED: dict = {}        # bucket -> (reset_ts, message)
_GREPAPP_DOWN_UNTIL = [0.0]
_SG_DOWN_UNTIL = [0.0]
SG_TIMEOUT = 14.0
_CFG_CACHE = {"mtime": None, "token": ""}


# ═════════════════════════════════════════════ 基础设施
def get_token() -> str:
    """env GITHUB_TOKEN / GH_TOKEN 优先, 其次 config.json 的 github.token (按 mtime 热读)。"""
    t = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if t:
        return t
    try:
        mt = _CONFIG_PATH.stat().st_mtime
        if mt != _CFG_CACHE["mtime"]:
            cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            _CFG_CACHE["token"] = str(((cfg.get("github") or {}).get("token")) or "").strip()
            _CFG_CACHE["mtime"] = mt
        return _CFG_CACHE["token"]
    except Exception:
        return ""


def _client(timeout: float = TIMEOUT):
    return httpx.Client(timeout=timeout, follow_redirects=True, transport=_TRANSPORT,
                        headers={"User-Agent": UA})


def cache_get(key):
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        if hit:
            _CACHE.pop(key, None)
    return None


def cache_put(key, text: str) -> str:
    if text and not text.startswith("ERROR"):
        with _LOCK:
            if len(_CACHE) > 300:
                for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:100]:
                    _CACHE.pop(k, None)
            _CACHE[key] = (time.time(), text)
    return text


def cached(fn):
    def _w(*a, **kw):
        key = (fn.__name__, json.dumps([a, kw], sort_keys=True, ensure_ascii=False, default=str),
               bool(get_token()))
        hit = cache_get(key)
        if hit is not None:
            return hit + "\n\n(缓存结果, 10 分钟内相同查询)"
        return cache_put(key, fn(*a, **kw))
    _w.__name__ = fn.__name__
    _w.__wrapped__ = fn
    return _w


def clear_state():
    """测试用: 清缓存 / 限流记录 / grep.app 冷却。"""
    with _LOCK:
        _CACHE.clear()
        _BLOCKED.clear()
    _GREPAPP_DOWN_UNTIL[0] = 0.0
    _SG_DOWN_UNTIL[0] = 0.0
    _CFG_CACHE.update(mtime=None, token="")


def smart_truncate(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return (text[:head] + f"\n\n…[中间省略 {len(text) - limit} 字符, 共 {len(text)} 字符; "
            f"需要细看请缩小范围或 git clone --depth 1]…\n\n" + text[-tail:])


def _clip(s, n: int) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:n] + ("…" if len(s) > n else "")


def _fmt_num(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1000:.1f}k" if n >= 10000 else str(n)


def _bucket(path: str) -> str:
    if path.startswith("/search/code"):
        return "code_search"
    return "search" if path.startswith("/search/") else "core"


def _fmt_reset(ts: float) -> str:
    wait = max(0, int(ts - time.time()))
    return f"{time.strftime('%H:%M:%S', time.localtime(ts))} (约 {wait} 秒后)"


def _limit_hint(bucket: str) -> str:
    if get_token():
        return {"core": "5000 次/小时", "search": "30 次/分钟", "code_search": "10 次/分钟"}[bucket]
    return {"core": "无 token 60 次/小时", "search": "无 token 10 次/分钟",
            "code_search": "需 token"}[bucket]


def gh_get(path: str, params: Optional[dict] = None, accept: str = "application/vnd.github+json"):
    """GitHub REST GET → (json, None) 或 (None, 'ERROR: …')。限流不重试。"""
    if httpx is None:
        return None, "ERROR: httpx 不可用"
    bucket = _bucket(path)
    with _LOCK:
        blk = _BLOCKED.get(bucket)
    if blk and time.time() < blk[0]:
        return None, blk[1] + " (本地已记录限流, 未发请求)"
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    tok = get_token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        with _client() as c:
            r = c.get(API + path, params=params or {}, headers=headers)
    except Exception as e:
        if httpx is not None and isinstance(e, httpx.TimeoutException):
            return None, f"ERROR: GitHub API 超时 ({TIMEOUT:.0f}s)"
        return None, f"ERROR: GitHub API 请求失败: {e}"
    remaining = r.headers.get("X-RateLimit-Remaining")
    if r.status_code in (403, 429):
        body = r.text[:300]
        if r.status_code == 429 or remaining == "0" or "rate limit" in body.lower():
            reset = r.headers.get("X-RateLimit-Reset")
            retry = r.headers.get("Retry-After")
            try:
                reset_ts = float(reset) if reset else time.time() + float(retry or 60)
            except ValueError:
                reset_ts = time.time() + 60
            msg = (f"ERROR: GitHub API 限流 ({bucket} 桶, {_limit_hint(bucket)}), 将在 {_fmt_reset(reset_ts)} 重置。"
                   "不要立即重试; 可先用 web_search(category='it') 或 sourcegraph/grep.app 代码搜索"
                   + ("" if tok else "; 配置 github.token 可提到 5000 次/小时") + "。")
            with _LOCK:
                _BLOCKED[bucket] = (reset_ts, msg)
            return None, msg
        return None, f"ERROR: GitHub API 403 拒绝访问: {_clip(body, 200)}"
    if r.status_code == 401:
        return None, "ERROR: GitHub token 无效或已过期 (401), 请检查 config.json github.token / GITHUB_TOKEN"
    if r.status_code == 404:
        return None, f"ERROR: GitHub 404 未找到 ({path}) — 检查 owner/name、ref 或路径"
    if r.status_code == 422:
        return None, f"ERROR: GitHub 查询无效 (422): {_clip(r.text, 240)}"
    if r.status_code >= 400:
        return None, f"ERROR: GitHub API HTTP {r.status_code}: {_clip(r.text, 200)}"
    try:
        return r.json(), None
    except Exception:
        return None, "ERROR: GitHub API 返回非 JSON"


def raw_get(url: str):
    """不占 API 配额的原始文件下载 → (status, bytes, truncated)。"""
    with _client() as c:
        with c.stream("GET", url) as r:
            if r.status_code != 200:
                return r.status_code, b"", False
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_RAW_BYTES:
                    return 200, bytes(buf[:MAX_RAW_BYTES]), True
            return 200, bytes(buf), False


_BINARY_EXT = re.compile(r"\.(png|jpe?g|gif|webp|ico|bmp|pdf|zip|gz|tgz|bz2|xz|7z|rar|jar|whl|exe|dll|so|"
                         r"dylib|bin|o|a|class|pyc|woff2?|ttf|otf|eot|mp[34]|mov|avi|wav|ogg|psd|sqlite|db)$", re.I)


def is_binary(path: str, data: bytes) -> bool:
    return bool(_BINARY_EXT.search(path or "")) or b"\x00" in data[:8000]


# ═════════════════════════════════════════════ 参数解析
_REPO_URL = re.compile(r"(?:https?://)?(?:www\.)?github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?"
                       r"(?:/(?:tree|blob)/([^/\s]+)(?:/(\S*))?)?/?$")


def parse_repo(repo: str):
    """'owner/name' / GitHub URL / git@ 地址 → (owner, name, ref_from_url, path_from_url) 或 None。"""
    s = (repo or "").strip().rstrip("/")
    if s.startswith("git@github.com:"):
        s = "github.com/" + s[len("git@github.com:"):]
    m = _REPO_URL.match(s)
    if m:
        return m.group(1), m.group(2), m.group(3), m.group(4)
    m = re.fullmatch(r"([\w.-]+)/([\w.-]+?)(?:\.git)?", s)
    if m:
        return m.group(1), m.group(2), None, None
    return None


def build_q(query: str, language: str = "", qualifiers=None) -> str:
    q = (query or "").strip()
    if language:
        q += f" language:{language.strip()}"
    if isinstance(qualifiers, dict):
        q += "".join(f" {k}:{v}" for k, v in qualifiers.items() if v not in (None, ""))
    elif isinstance(qualifiers, (list, tuple)):
        q += "".join(f" {x}" for x in qualifiers if x)
    elif qualifiers:
        q += f" {str(qualifiers).strip()}"
    return q.strip()


# ═════════════════════════════════════════════ 格式化
def fmt_repo(i: int, it: dict) -> str:
    lic = ((it.get("license") or {}).get("spdx_id") or "无许可证").replace("NOASSERTION", "其他许可证")
    pushed = (it.get("pushed_at") or "")[:10]
    flags = "".join(f" [{f}]" for f, on in (("已归档", it.get("archived")), ("fork", it.get("fork"))) if on)
    topics = ", ".join((it.get("topics") or [])[:8])
    return (f"[{i}] {it.get('full_name')}  ★{_fmt_num(it.get('stargazers_count'))} "
            f"fork {_fmt_num(it.get('forks_count'))} · {it.get('language') or '?'} · {lic} · 最近推送 {pushed}{flags}\n"
            f"    {_clip(it.get('description'), 200) or '(无描述)'}\n"
            + (f"    topics: {topics}\n" if topics else "")
            + f"    {it.get('html_url')}")


def fmt_issue(i: int, it: dict) -> str:
    kind = "PR" if "pull_request" in it else "Issue"
    repo = (it.get("repository_url") or "").replace(API + "/repos/", "")
    labels = ", ".join(l.get("name", "") for l in (it.get("labels") or [])[:6] if isinstance(l, dict))
    body = _clip(it.get("body"), 300)
    return (f"[{i}] {kind} {repo}#{it.get('number')} [{it.get('state')}] {_clip(it.get('title'), 160)}\n"
            f"    评论 {it.get('comments', 0)} · 更新 {(it.get('updated_at') or '')[:10]}"
            + (f" · labels: {labels}" if labels else "") + f"\n    {it.get('html_url')}"
            + (f"\n    {body}" if body else ""))


def fmt_commit(i: int, it: dict) -> str:
    cm = it.get("commit") or {}
    au = cm.get("author") or {}
    repo = (it.get("repository") or {}).get("full_name", "")
    return (f"[{i}] {(it.get('sha') or '')[:8]} {repo} · {au.get('name', '?')} · {(au.get('date') or '')[:10]}\n"
            f"    {_clip((cm.get('message') or '').splitlines()[0] if cm.get('message') else '', 160)}\n"
            f"    {it.get('html_url')}")


def fmt_topic(i: int, it: dict) -> str:
    tags = "".join(f" [{t}]" for t, on in (("featured", it.get("featured")), ("curated", it.get("curated"))) if on)
    return (f"[{i}] {it.get('name')} ({it.get('display_name') or it.get('name')}){tags}\n"
            f"    {_clip(it.get('short_description') or it.get('description'), 200)}\n"
            f"    https://github.com/topics/{it.get('name')}")


def _strip_snippet(s: str, n: int = 400) -> str:
    s = re.sub(r"</(tr|div|p)>", "\n", s or "", flags=re.I)
    s = _html.unescape(re.sub(r"<[^>]+>", "", s))
    lines = [ln.rstrip() for ln in s.splitlines() if ln.strip()]
    out = "\n".join(lines)
    return out[:n] + ("…" if len(out) > n else "")


# ═════════════════════════════════════════════ 代码搜索后端
_GREP_LANG = {"python": "Python", "py": "Python", "javascript": "JavaScript", "js": "JavaScript",
              "typescript": "TypeScript", "ts": "TypeScript", "go": "Go", "golang": "Go", "rust": "Rust",
              "java": "Java", "c": "C", "c++": "C++", "cpp": "C++", "c#": "C#", "csharp": "C#",
              "shell": "Shell", "bash": "Shell", "ruby": "Ruby", "php": "PHP", "kotlin": "Kotlin",
              "swift": "Swift", "vue": "Vue", "html": "HTML", "css": "CSS", "lua": "Lua", "dart": "Dart"}


def _gh_code(q: str, limit: int):
    d, err = gh_get("/search/code", {"q": q, "per_page": limit},
                    accept="application/vnd.github.text-match+json")
    if err:
        return None, err
    out = []
    for it in (d.get("items") or [])[:limit]:
        frag = "\n".join(_clip(tm.get("fragment"), 300) for tm in (it.get("text_matches") or [])[:2])
        out.append({"repo": (it.get("repository") or {}).get("full_name", ""), "path": it.get("path", ""),
                    "url": it.get("html_url", ""), "snippet": frag})
    return {"total": d.get("total_count", len(out)), "hits": out}, None


def _grepapp(query: str, language: str, repo: str, limit: int):
    if time.time() < _GREPAPP_DOWN_UNTIL[0]:
        return None, "grep.app 冷却中 (之前被风控拦截)"
    params = {"q": query}
    if language:
        params["filter[lang][0]"] = _GREP_LANG.get(language.lower(), language[:1].upper() + language[1:])
    if repo:
        params["filter[repo][0]"] = repo
    try:
        with _client() as c:
            r = c.get(GREPAPP, params=params, headers={"Accept": "application/json"})
    except Exception as e:
        return None, f"grep.app 请求失败: {e}"
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or "json" not in ctype:
        if r.status_code in (403, 429) or "html" in ctype:
            _GREPAPP_DOWN_UNTIL[0] = time.time() + CACHE_TTL
        return None, f"grep.app 不可用 (HTTP {r.status_code}{', 风控挑战页' if 'html' in ctype else ''})"
    try:
        d = r.json()
    except Exception:
        return None, "grep.app 返回非 JSON"
    hits = (d.get("hits") or {})
    out = []
    for h in (hits.get("hits") or [])[:limit]:
        rp = h.get("repo")
        rp = rp.get("raw", "") if isinstance(rp, dict) else (rp or "")
        path = h.get("path")
        path = path.get("raw", "") if isinstance(path, dict) else (path or "")
        br = h.get("branch")
        br = (br.get("raw") if isinstance(br, dict) else br) or "HEAD"
        snip = (h.get("content") or {}).get("snippet", "")
        out.append({"repo": rp, "path": path, "url": f"https://github.com/{rp}/blob/{br}/{path}",
                    "snippet": _strip_snippet(snip)})
    return {"total": hits.get("total", len(out)), "hits": out}, None


_SG_QUERY = ("query($q:String!){search(query:$q,version:V3){results{matchCount results{__typename "
             "... on FileMatch{repository{name} file{path} lineMatches{preview lineNumber}}}}}}")


def _sourcegraph(query: str, language: str, repo: str, limit: int):
    if time.time() < _SG_DOWN_UNTIL[0]:
        return None, "sourcegraph 冷却中 (刚才超时/出错, 5 分钟内不再等)"
    q = query
    if repo:
        q = f"repo:^github\\.com/{re.escape(repo)}$ {q}"
    if language:
        q += f" lang:{language}"
    q += f" count:{limit} timeout:10s"   # 匿名访问时快时慢 (实测 2s~30s+), 让服务端先返回已有结果
    try:
        with _client(timeout=SG_TIMEOUT) as c:
            r = c.post(SOURCEGRAPH, json={"query": _SG_QUERY, "variables": {"q": q}},
                       headers={"Content-Type": "application/json"})
    except Exception as e:
        _SG_DOWN_UNTIL[0] = time.time() + 300
        return None, f"sourcegraph 请求失败: {type(e).__name__} {e}"
    if r.status_code != 200:
        if r.status_code in (429,) or r.status_code >= 500:
            _SG_DOWN_UNTIL[0] = time.time() + 300
        return None, f"sourcegraph HTTP {r.status_code}"
    try:
        res = (((r.json().get("data") or {}).get("search") or {}).get("results") or {})
    except Exception:
        return None, "sourcegraph 返回非 JSON"
    out = []
    for fm in (res.get("results") or []):
        if fm.get("__typename") != "FileMatch":
            continue
        name = ((fm.get("repository") or {}).get("name") or "")
        rp = name.split("github.com/", 1)[1] if name.startswith("github.com/") else name
        path = (fm.get("file") or {}).get("path", "")
        lms = fm.get("lineMatches") or []
        line = (lms[0].get("lineNumber", 0) + 1) if lms else 0
        base = f"https://github.com/{rp}/blob/HEAD/{path}" if name.startswith("github.com/") \
            else f"https://sourcegraph.com/{name}/-/blob/{path}"
        out.append({"repo": rp, "path": path, "url": base + (f"#L{line}" if line else ""),
                    "snippet": "\n".join(f"L{lm.get('lineNumber', 0) + 1}: {_clip(lm.get('preview'), 200)}"
                                         for lm in lms[:3])})
        if len(out) >= limit:
            break
    return {"total": res.get("matchCount", len(out)), "hits": out}, None


def code_search(query: str, language: str = "", repo: str = "", limit: int = 8, qualifiers=None):
    """→ (backend, data, notes)。有 token 走 GitHub; 否则 grep.app → sourcegraph。"""
    notes = []
    if get_token():
        q = build_q(query, language, qualifiers) + (f" repo:{repo}" if repo else "")
        data, err = _gh_code(q, limit)
        if data is not None:
            return "GitHub code search (token)", data, notes
        notes.append(err)
    else:
        notes.append("未配置 GitHub token, GitHub 代码搜索不可用")
    extra = build_q("", "", qualifiers)
    q2 = f"{query} {extra}".strip()
    data, err = _grepapp(q2, language, repo, limit)
    if data is not None and data["hits"]:
        return "grep.app", data, notes
    notes.append(err or "grep.app 0 条")
    data, err = _sourcegraph(q2, language, repo, limit)
    if data is not None:
        if not data["hits"]:
            notes.append("sourcegraph 0 条")
        return "sourcegraph.com", data, notes
    notes.append(err)
    return None, None, notes


def _fmt_code(backend: str, data: dict, query: str, notes: list) -> str:
    lines = [f"## 代码搜索 (后端: {backend}) — {data['total']} 处匹配, 显示 {len(data['hits'])}: {_clip(query, 80)}"]
    if notes:
        lines.append("说明: " + "; ".join(n for n in notes if n))
    for i, h in enumerate(data["hits"], 1):
        lines.append(f"[{i}] {h['repo']} / {h['path']}\n    {h['url']}"
                     + ("\n    " + h["snippet"].replace("\n", "\n    ") if h["snippet"] else ""))
    if not data["hits"]:
        lines.append("(0 条; 换更具体的片段, 或去掉 language 过滤)")
    lines.append("下一步: github_repo(action='file', repo=..., path=...) 读完整文件。")
    return "\n".join(lines)


# ═════════════════════════════════════════════ 入口: github_search
_SORTS = {
    "repositories": {"stars": "stars", "updated": "updated", "forks": "forks"},
    "issues": {"stars": "reactions", "updated": "updated", "comments": "comments", "reactions": "reactions",
               "created": "created"},
    "commits": {"updated": "committer-date", "stars": None},
    "topics": {},
}
KINDS = ("repositories", "code", "issues", "commits", "topics")


@cached
def github_search(kind: str = "repositories", query: str = "", language: str = "", sort: str = "",
                  limit: int = 8, qualifiers=None) -> str:
    kind = (kind or "repositories").strip().lower()
    kind = {"repo": "repositories", "repos": "repositories", "repository": "repositories",
            "issue": "issues", "pr": "issues", "prs": "issues", "pulls": "issues",
            "commit": "commits", "topic": "topics"}.get(kind, kind)
    if kind not in KINDS:
        return f"ERROR: kind 只能是 {' / '.join(KINDS)}"
    if not (query or "").strip() and not qualifiers:
        return "ERROR: github_search 需要 query"
    try:
        limit = max(1, min(int(limit or 8), 20))
    except (TypeError, ValueError):
        limit = 8
    if kind == "code":
        backend, data, notes = code_search(query, language, "", limit, qualifiers)
        if backend is None:
            return ("ERROR: 代码搜索全部后端失败: " + "; ".join(n for n in notes if n)
                    + "。可改用 github_search(kind='repositories') 找项目后 github_repo tree/file 阅读, "
                      "或 web_search(category='it'); 配置 github.token 后可用 GitHub 代码搜索。")
        return _fmt_code(backend, data, build_q(query, language, qualifiers), notes)

    q = build_q(query, language, qualifiers)
    params = {"q": q, "per_page": limit}
    s = _SORTS[kind].get((sort or "").strip().lower())
    if s:
        params["sort"] = s
        params["order"] = "desc"
    d, err = gh_get(f"/search/{kind}", params)
    if err:
        return err
    items = (d.get("items") or [])[:limit]
    total = d.get("total_count", len(items))
    if not items:
        return f"GitHub {kind}: 0 条 (q: {_clip(q, 100)}) — 换关键词或去掉 language/限定条件"
    fmt = {"repositories": fmt_repo, "issues": fmt_issue, "commits": fmt_commit, "topics": fmt_topic}[kind]
    head = f"## GitHub {kind} (后端: api.github.com) — 共 {total}, 显示 {len(items)}: {_clip(q, 100)}"
    tail = ""
    if kind == "repositories":
        tail = "\n下一步: github_repo(action='readme'|'tree', repo='owner/name') 看候选项目; 深入研究可 git clone --depth 1。"
    return head + "\n" + "\n".join(fmt(i, it) for i, it in enumerate(items, 1)) + tail


# ═════════════════════════════════════════════ 入口: github_repo
ACTIONS = ("info", "readme", "tree", "file", "releases", "commits", "search_in_repo")


def _readme(o: str, n: str, ref: str) -> str:
    for name in ("README.md", "README.rst", "readme.md", "README"):
        st, data, trunc = raw_get(f"{RAW}/{o}/{n}/{quote(ref)}/{name}")
        if st == 200 and data:
            return _text_out(f"{o}/{n} {name} (ref={ref}, raw.githubusercontent.com)", name, data, trunc)
    d, err = gh_get(f"/repos/{o}/{n}/readme", {"ref": ref} if ref != "HEAD" else None)
    if err:
        return err if "404" not in err else f"ERROR: {o}/{n} 没有 README (或仓库不存在)"
    dl = d.get("download_url")
    if dl:
        st, data, trunc = raw_get(dl)
        if st == 200:
            return _text_out(f"{o}/{n} {d.get('path')} (ref={ref})", d.get("path", ""), data, trunc)
    return f"ERROR: 读取 {o}/{n} README 失败"


def _text_out(title: str, path: str, data: bytes, trunc: bool) -> str:
    if is_binary(path, data):
        return f"ERROR: {path} 是二进制文件 ({len(data)} 字节), 不返回内容"
    text = data.decode("utf-8", errors="replace")
    note = " (原文件超过 3MB, 仅取前 3MB)" if trunc else ""
    return f"## {title} — {len(text)} 字符{note}\n\n" + smart_truncate(text)


def _tree(o, n, ref, path, limit):
    d, err = gh_get(f"/repos/{o}/{n}/git/trees/{quote(ref, safe='')}", {"recursive": "1"})
    if err:
        return err
    prefix = (path or "").strip("/")
    items = [t for t in (d.get("tree") or [])
             if not prefix or t.get("path", "") == prefix or t.get("path", "").startswith(prefix + "/")]
    n_dir = sum(1 for t in items if t.get("type") == "tree")
    shown = items[:limit]
    lines = [f"## {o}/{n} 目录树 (ref={ref}{', 前缀 ' + prefix if prefix else ''}) — {len(items)} 项 "
             f"(目录 {n_dir}, 文件 {len(items) - n_dir}), 显示 {len(shown)}"
             + (" — 仓库过大, GitHub 只返回了部分条目" if d.get("truncated") else "")]
    for t in shown:
        if t.get("type") == "tree":
            lines.append(t.get("path", "") + "/")
        else:
            sz = t.get("size") or 0
            lines.append(f"{t.get('path', '')}  {sz / 1024:.1f}KB" if sz >= 1024 else f"{t.get('path', '')}  {sz}B")
    if len(items) > len(shown):
        lines.append(f"…还有 {len(items) - len(shown)} 项; 用 path 前缀缩小范围或调大 limit")
    return "\n".join(lines)


def _search_in_repo(o, n, query, limit, ref):
    repo = f"{o}/{n}"
    backend, data, notes = code_search(query, "", repo, limit)
    if not (backend and data["hits"]):
        # 仓库改过名 (tiangolo/fastapi → fastapi/fastapi) 时索引只认新名字
        d, _ = gh_get(f"/repos/{o}/{n}")
        full = (d or {}).get("full_name") or repo
        if full.lower() != repo.lower():
            notes.append(f"{repo} 已更名为 {full}")
            repo = full
            o, n = full.split("/", 1)
            backend, data, more = code_search(query, "", repo, limit)
            notes += [x for x in more if x not in notes]
    if backend and data["hits"]:
        return _fmt_code(backend, data, f"{query} (repo {repo})", notes)
    # 兜底: 文件名匹配
    d, err = gh_get(f"/repos/{o}/{n}/git/trees/{quote(ref, safe='')}", {"recursive": "1"})
    if err:
        return "ERROR: 仓库内代码搜索无结果 (" + "; ".join(x for x in notes if x) + f"), 目录树也取不到: {err}"
    toks = [t.lower() for t in re.findall(r"[\w.-]{2,}", query)]
    hits = [t.get("path", "") for t in (d.get("tree") or [])
            if t.get("type") == "blob" and any(tok in t.get("path", "").lower() for tok in toks)][:limit * 3]
    head = (f"## {repo} 仓库内搜索: 代码内容索引无结果 ({'; '.join(x for x in notes if x) or '后端 0 条'}), "
            f"以下为文件名匹配 {len(hits)} 个")
    tip = ("\n下一步: github_repo(action='file', path=...) 读文件; 想全文 grep 可 "
           f"git clone --depth 1 https://github.com/{repo} 到工作区后用 search_code。")
    return head + "\n" + ("\n".join(hits) if hits else "(无)") + tip


@cached
def github_repo(action: str = "info", repo: str = "", path: str = "", ref: str = "",
                limit: int = 0, query: str = "") -> str:
    action = (action or "info").strip().lower()
    if action not in ACTIONS:
        return f"ERROR: action 只能是 {' / '.join(ACTIONS)}"
    pr = parse_repo(repo)
    if not pr:
        return "ERROR: repo 需要 'owner/name' 或 GitHub 链接"
    o, n, url_ref, url_path = pr
    ref = (ref or url_ref or "HEAD").strip()
    path = (path or url_path or "").strip().lstrip("/")
    try:
        limit = int(limit or 0)
    except (TypeError, ValueError):
        limit = 0

    if action == "info":
        d, err = gh_get(f"/repos/{o}/{n}")
        if err:
            return err
        lic = ((d.get("license") or {}).get("spdx_id") or "无")
        return "\n".join([
            f"## {d.get('full_name')}" + (" [已归档]" if d.get("archived") else "") + (" [fork]" if d.get("fork") else ""),
            _clip(d.get("description"), 300) or "(无描述)",
            f"★{d.get('stargazers_count')} · fork {d.get('forks_count')} · watchers {d.get('subscribers_count', '?')} · "
            f"open issues {d.get('open_issues_count')}",
            f"语言 {d.get('language') or '?'} · 许可证 {lic} · 默认分支 {d.get('default_branch')} · "
            f"创建 {(d.get('created_at') or '')[:10]} · 最近推送 {(d.get('pushed_at') or '')[:10]} · 大小 {d.get('size', 0)}KB",
            "topics: " + (", ".join(d.get("topics") or []) or "-"),
            f"主页: {d.get('homepage') or '-'}  ·  {d.get('html_url')}",
            f"深入研究: git clone --depth 1 {d.get('clone_url') or f'https://github.com/{o}/{n}.git'}",
        ])
    if action == "readme":
        return _readme(o, n, ref)
    if action == "file":
        if not path:
            return "ERROR: action=file 需要 path"
        st, data, trunc = raw_get(f"{RAW}/{o}/{n}/{quote(ref)}/{quote(path)}")
        if st == 404:
            return f"ERROR: {o}/{n}@{ref} 下没有文件 {path} (目录请用 action=tree, path 作前缀)"
        if st != 200:
            return f"ERROR: 读取文件失败 HTTP {st}"
        return _text_out(f"{o}/{n}/{path} (ref={ref}, raw.githubusercontent.com)", path, data, trunc)
    if action == "tree":
        return _tree(o, n, ref, path, max(1, min(limit or 200, 1000)))
    if action == "releases":
        lim = max(1, min(limit or 5, 20))
        d, err = gh_get(f"/repos/{o}/{n}/releases", {"per_page": lim})
        if err:
            return err
        if not d:
            return f"{o}/{n}: 没有 GitHub Release (可用 action=commits 或看 tags)"
        out = [f"## {o}/{n} 最近 {len(d)} 个 Release"]
        for r in d[:lim]:
            out.append(f"### {r.get('tag_name')} {_clip(r.get('name'), 80)} · {(r.get('published_at') or '')[:10]}"
                       + (" [预发布]" if r.get("prerelease") else "") + f"\n{r.get('html_url')}\n"
                       + smart_truncate((r.get("body") or "").strip(), 1500))
        return "\n\n".join(out)
    if action == "commits":
        lim = max(1, min(limit or 10, 30))
        params = {"per_page": lim}
        if ref != "HEAD":
            params["sha"] = ref
        if path:
            params["path"] = path
        d, err = gh_get(f"/repos/{o}/{n}/commits", params)
        if err:
            return err
        out = [f"## {o}/{n} 最近 {len(d)} 次提交" + (f" (path={path})" if path else "")]
        for c in d[:lim]:
            cm = c.get("commit") or {}
            au = cm.get("author") or {}
            out.append(f"{(c.get('sha') or '')[:8]} {(au.get('date') or '')[:10]} {au.get('name', '?')}: "
                       f"{_clip((cm.get('message') or '').splitlines()[0] if cm.get('message') else '', 140)}")
        return "\n".join(out)
    # search_in_repo
    if not (query or "").strip():
        return "ERROR: action=search_in_repo 需要 query"
    return _search_in_repo(o, n, query.strip(), max(1, min(limit or 10, 20)), ref)
