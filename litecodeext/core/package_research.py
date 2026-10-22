"""package_research.py — 包/库调研: PyPI / npm / crates.io / Go 一次查。

  pypi    精确名 → pypi.org/pypi/<name>/json; 关键词发现 → 本机 SearXNG `site:pypi.org` 抽包名再补详情,
          还不够 → GitHub 仓库搜索 (language:python, 注明"未必发布到 PyPI")。PyPI 网页搜索有 JS 风控, 不用。
  npm     registry.npmjs.org/-/v1/search (自带周下载量)
  crates  crates.io/api/v1/crates (总下载 / 近 90 天下载)
  go      SearXNG pkg.go.dev 引擎; 不可用时 GitHub 仓库搜索 language:go
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None

import github_research as gr

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:18888").rstrip("/")
ECOSYSTEMS = ("pypi", "npm", "crates", "go")
_PYPI_PROJECT = re.compile(r"https?://pypi\.org/project/([A-Za-z0-9._-]+)/?")


def _get_json(url: str, params=None, headers=None):
    try:
        with gr._client() as c:
            r = c.get(url, params=params or {}, headers=headers or {})
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        return r.json(), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def searxng(query: str, engines: str = "", categories: str = "", limit: int = 10):
    params = {"q": query, "format": "json"}
    if engines:
        params["engines"] = engines
    if categories:
        params["categories"] = categories
    d, err = _get_json(f"{SEARXNG_URL}/search", params)
    if err or not isinstance(d, dict):
        return None, err or "SearXNG 返回异常"
    return (d.get("results") or [])[:limit * 3], None


def _date(s) -> str:
    return str(s or "")[:10]


# ── PyPI ──────────────────────────────────────────────
def pypi_info(name: str):
    d, err = _get_json(f"https://pypi.org/pypi/{quote(name)}/json")
    if err or not isinstance(d, dict):
        return None
    info = d.get("info") or {}
    ver = info.get("version") or ""
    files = d.get("urls") or (d.get("releases") or {}).get(ver) or []
    released = _date(files[0].get("upload_time_iso_8601") or files[0].get("upload_time")) if files else ""
    urls = info.get("project_urls") or {}
    repo = next((v for k, v in urls.items() if re.search(r"source|repo|github|code", k, re.I)), "") \
        or info.get("home_page") or next(iter(urls.values()), "")
    lic = info.get("license_expression") or info.get("license") or ""
    return {"name": info.get("name") or name, "version": ver, "date": released,
            "desc": info.get("summary") or "", "repo": repo, "license": gr._clip(lic, 40),
            "extra": f"requires_python {info.get('requires_python')}" if info.get("requires_python") else "",
            "url": f"https://pypi.org/project/{info.get('name') or name}/"}


def search_pypi(query: str, limit: int):
    notes, names = [], []
    q = query.strip()
    if re.fullmatch(r"[A-Za-z0-9._-]+", q):
        names.append(q)
    elif len(q.split()) <= 3:
        names.append(re.sub(r"\s+", "-", q))
    res, err = searxng(f"site:pypi.org {q}", limit=limit)
    if res is None:
        notes.append(f"SearXNG 不可用 ({err}), 只做了精确名查询")
    else:
        for r in res:
            m = _PYPI_PROJECT.match(r.get("url") or "")
            if m and m.group(1).lower() not in {x.lower() for x in names}:
                names.append(m.group(1))
    names = names[:limit + 3]
    with ThreadPoolExecutor(max_workers=6) as ex:
        infos = [x for x in ex.map(pypi_info, names) if x]
    # 搜索引擎的 site:pypi.org 结果噪声大: 非精确名的候选, 名字或简介至少要含一个查询词
    toks = [t.lower() for t in re.findall(r"[\w-]{3,}", q)]
    exact = names[0].lower() if names and names[0].lower() in (q.lower(), re.sub(r"\s+", "-", q.lower())) else ""
    infos = [x for x in infos if x["name"].lower() == exact
             or any(t in (x["name"] + " " + x["desc"]).lower() for t in toks)][:limit]
    if len(infos) < 2:
        d, gerr = gr.gh_get("/search/repositories",
                            {"q": f"{q} language:python", "sort": "stars", "order": "desc", "per_page": limit})
        if d:
            notes.append("PyPI 命中太少, 补充 GitHub 仓库 (language:python, 未必发布到 PyPI)")
            for it in (d.get("items") or [])[:limit - len(infos)]:
                infos.append({"name": it.get("full_name"), "version": "-", "date": _date(it.get("pushed_at")),
                              "desc": it.get("description") or "",
                              "extra": f"★{it.get('stargazers_count')}", "repo": it.get("html_url"),
                              "license": ((it.get("license") or {}).get("spdx_id") or ""), "url": it.get("html_url")})
        elif gerr:
            notes.append(gerr)
    return infos, notes


# ── npm ───────────────────────────────────────────────
def search_npm(query: str, limit: int):
    d, err = _get_json("https://registry.npmjs.org/-/v1/search", {"text": query, "size": limit})
    if err:
        return [], [f"npm registry 失败: {err}"]
    out = []
    for o in (d.get("objects") or [])[:limit]:
        p = o.get("package") or {}
        links = p.get("links") or {}
        repo = re.sub(r"^git\+|\.git(#.*)?$", "", links.get("repository") or "") or links.get("homepage") or ""
        repo = re.sub(r"^(?:ssh://)?git@github\.com[:/]", "https://github.com/", repo)
        wk = (o.get("downloads") or {}).get("weekly")
        out.append({"name": p.get("name"), "version": p.get("version"), "date": _date(p.get("date")),
                    "desc": p.get("description") or "", "repo": repo, "license": p.get("license") or "",
                    "extra": f"周下载 {gr._fmt_num(wk)}" if wk is not None else "",
                    "url": links.get("npm") or f"https://www.npmjs.com/package/{p.get('name')}"})
    return out, []


# ── crates.io ─────────────────────────────────────────
def search_crates(query: str, limit: int):
    d, err = _get_json("https://crates.io/api/v1/crates", {"q": query, "per_page": limit})
    if err:
        return [], [f"crates.io 失败: {err}"]
    out = []
    for c in (d.get("crates") or [])[:limit]:
        out.append({"name": c.get("name"), "version": c.get("max_stable_version") or c.get("default_version")
                    or c.get("max_version"), "date": _date(c.get("updated_at")), "desc": c.get("description") or "",
                    "repo": c.get("repository") or c.get("homepage") or "", "license": "",
                    "extra": f"总下载 {gr._fmt_num(c.get('downloads'))} · 近90天 {gr._fmt_num(c.get('recent_downloads'))}",
                    "url": f"https://crates.io/crates/{c.get('name')}"})
    return out, []


# ── Go ────────────────────────────────────────────────
def search_go(query: str, limit: int):
    res, err = searxng(query, engines="pkg.go.dev", limit=limit)
    if res:
        out = [{"name": r.get("package_name") or r.get("title"), "version": r.get("version") or "",
                "date": _date(r.get("publishedDate")), "desc": r.get("content") or "",
                "repo": "", "license": r.get("license_name") or "", "extra": "", "url": r.get("url")}
               for r in res[:limit]]
        return out, []
    d, gerr = gr.gh_get("/search/repositories",
                        {"q": f"{query} language:go", "sort": "stars", "order": "desc", "per_page": limit})
    notes = [f"pkg.go.dev (SearXNG) 无结果/不可用 ({err or '0 条'}), 改用 GitHub 仓库搜索"]
    if not d:
        return [], notes + [gerr or ""]
    return [{"name": it.get("full_name"), "version": "-", "date": _date(it.get("pushed_at")),
             "desc": it.get("description") or "", "repo": it.get("html_url"), "extra": f"★{it.get('stargazers_count')}",
             "license": ((it.get("license") or {}).get("spdx_id") or ""), "url": it.get("html_url")}
            for it in (d.get("items") or [])[:limit]], notes


_SEARCHERS = {"pypi": search_pypi, "npm": search_npm, "crates": search_crates, "go": search_go}
_ALIASES = {"python": "pypi", "pip": "pypi", "py": "pypi", "node": "npm", "js": "npm", "javascript": "npm",
            "typescript": "npm", "ts": "npm", "rust": "crates", "cargo": "crates", "crate": "crates",
            "golang": "go"}


def _fmt(eco: str, items: list, notes: list) -> str:
    title = {"pypi": "PyPI", "npm": "npm", "crates": "crates.io", "go": "Go (pkg.go.dev)"}[eco]
    lines = [f"### {title} — {len(items)} 个"]
    lines += [f"注: {n}" for n in notes if n]
    for i, it in enumerate(items, 1):
        ver = str(it.get("version") or "").lstrip("vV")
        meta = " · ".join(x for x in (f"v{ver}" if ver and ver != "-" else "",
                                      it.get("date") and f"发布/更新 {it['date']}", it.get("extra"),
                                      it.get("license")) if x)
        lines.append(f"[{i}] {it.get('name')}  {meta}\n    {gr._clip(it.get('desc'), 180) or '(无描述)'}\n"
                     f"    {it.get('url')}" + (f"  源码: {it['repo']}" if it.get("repo") and it.get("repo") != it.get("url") else ""))
    if not items:
        lines.append("(无结果)")
    return "\n".join(lines)


@gr.cached
def package_search(query: str, ecosystem: str = "all", limit: int = 5) -> str:
    q = (query or "").strip()
    if not q:
        return "ERROR: package_search 需要 query (包名或功能关键词)"
    try:
        limit = max(1, min(int(limit or 5), 10))
    except (TypeError, ValueError):
        limit = 5
    eco = _ALIASES.get((ecosystem or "all").strip().lower(), (ecosystem or "all").strip().lower())
    if eco == "all":
        ecos = ["pypi", "npm", "crates"]
    elif eco in ECOSYSTEMS:
        ecos = [eco]
    else:
        return f"ERROR: ecosystem 只能是 all / {' / '.join(ECOSYSTEMS)}"
    with ThreadPoolExecutor(max_workers=len(ecos)) as ex:
        results = list(ex.map(lambda e: (e, _SEARCHERS[e](q, limit)), ecos))
    parts = [f"## 包搜索: {gr._clip(q, 80)}"]
    for e, (items, notes) in results:
        parts.append(_fmt(e, items, notes))
    parts.append("下一步: 挑 2-3 个候选用 github_repo(action='info'|'readme', repo=...) 看活跃度/许可证/用法。")
    return "\n\n".join(parts)
