"""lib/skill_market.py — 在线技能索引 (技能市场): 聚合公开技能源, 搜索 / 详情 / 下载暂存。

来源 (均为公开、免登录的接口, 2026-10 实测可用):
  anthropic — github.com/anthropics/skills: GitHub API 取仓库文件树 (1 次调用), 每个 SKILL.md
              从 raw.githubusercontent.com 读 frontmatter (不占 API 配额); 整份索引缓存 24h, 本地打分过滤。
  github    — 任意 GitHub 仓库, 用户填 owner/repo[/path], 同上。
  clawhub   — ClawHub (OpenClaw 技能注册表) clawhub.ai/api/v1: search / skills / skills/{slug}/file / download (zip)。
  skillhub  — 腾讯 SkillHub api.skillhub.cn/api/v1: search / skills/{slug}/file / download (zip), 按 namespace 区分同名。
条目 id: "<source>:<定位>" — github:owner/repo@branch:dir / clawhub:owner/slug / skillhub:namespace/slug。
网络: 每个请求 10s 超时 (国内访问 GitHub 慢, 给出清晰错误而不是卡住); 代理用 config.json
`skills.market_proxy`, 没配时按环境变量 HTTPS_PROXY 走 (httpx 默认 trust_env)。
GitHub token (可选, 防 60 次/小时限流): 复用 core/github_research.get_token (env GITHUB_TOKEN / config github.token)。
缓存: WORKSPACE/skills_market_cache/*.json, 24h。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from . import skill_registry as reg
from .skill_search import search as _local_search

TIMEOUT = 10.0
CACHE_TTL = 86400
UA = "LiteCode-SkillMarket/1.0"
MAX_FILES = 150
MAX_BYTES = 15 * 1024 * 1024
ANTHROPIC_REPO = "anthropics/skills"
CLAWHUB = "https://clawhub.ai"
SKILLHUB = "https://api.skillhub.cn"
SOURCES = {"anthropic": "Anthropic 官方", "clawhub": "ClawHub", "skillhub": "腾讯 SkillHub", "github": "GitHub 仓库"}


class MarketError(Exception):
    pass


# ── HTTP / 缓存 ───────────────────────────────────────────────────
def _proxy() -> str | None:
    try:
        from .config import CFG
        p = str(((CFG.get("skills") or {}).get("market_proxy")) or "").strip()
        return p or None
    except Exception:
        return None


def _gh_headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    try:
        try:
            from github_research import get_token     # 服务进程: core 在 sys.path
        except ImportError:
            from core.github_research import get_token
        t = get_token()
        if t:
            h["Authorization"] = f"Bearer {t}"
    except Exception:
        pass
    return h


def client() -> httpx.AsyncClient:
    kw = {"timeout": httpx.Timeout(TIMEOUT, connect=TIMEOUT), "follow_redirects": True,
          "headers": {"User-Agent": UA}}
    p = _proxy()
    if p:
        kw["proxy"] = p
    return httpx.AsyncClient(**kw)


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1) if m else url


async def _get(c: httpx.AsyncClient, url: str, headers: dict | None = None) -> httpx.Response:
    try:
        r = await c.get(url, headers=headers or {})
    except httpx.TimeoutException:
        raise MarketError(f"访问 {_host(url)} 超时 ({int(TIMEOUT)}s), 网络慢或被墙, 可在 config.json skills.market_proxy 配代理")
    except httpx.HTTPError as e:
        raise MarketError(f"访问 {_host(url)} 失败: {type(e).__name__}")
    if r.status_code == 403 and "api.github.com" in url:
        raise MarketError("GitHub API 限流 (未登录 60 次/小时), 稍后再试或配置 GITHUB_TOKEN")
    if r.status_code == 404:
        raise MarketError(f"{_host(url)} 返回 404 (不存在)")
    if r.status_code >= 400:
        raise MarketError(f"{_host(url)} 返回 HTTP {r.status_code}")
    return r


async def _json(c, url, headers=None):
    r = await _get(c, url, headers)
    try:
        return r.json()
    except ValueError:
        raise MarketError(f"{_host(url)} 返回的不是 JSON")


def cache_dir() -> Path:
    return reg.state_path().parent / "skills_market_cache"


def _cache_file(key: str) -> Path:
    return cache_dir() / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")


def cache_get(key: str, ttl: int = CACHE_TTL):
    p = _cache_file(key)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - d.get("ts", 0) <= ttl:
            return d.get("data")
    except Exception:
        pass
    return None


def cache_put(key: str, data) -> None:
    try:
        from .atomic_io import atomic_write_json
        cache_dir().mkdir(parents=True, exist_ok=True)
        atomic_write_json(_cache_file(key), {"ts": time.time(), "key": key, "data": data})
    except Exception:
        pass


def _item(source, id_, name, title, desc, *, author="", version="", downloads=0, url="", extra=None) -> dict:
    d = {"id": id_, "source": source, "source_label": SOURCES.get(source, source), "name": reg.slugify(name) or name,
         "title": title or name, "description": " ".join(str(desc or "").split())[:400], "author": author,
         "version": str(version or ""), "downloads": int(downloads or 0), "url": url}
    if extra:
        d.update(extra)
    return d


# ── GitHub (anthropic / 任意仓库) ─────────────────────────────────
def parse_repo(spec: str) -> tuple[str, str]:
    """owner/repo[/path] 或 https://github.com/owner/repo[/tree/branch/path] → (owner/repo, path)。"""
    s = (spec or "").strip().rstrip("/")
    s = re.sub(r"^https?://github\.com/", "", s)
    s = re.sub(r"\.git$", "", s)
    m = re.match(r"^([\w.-]+)/([\w.-]+)(?:/tree/[^/]+)?(?:/(.*))?$", s)
    if not m:
        raise MarketError("仓库格式应为 owner/repo 或 owner/repo/子目录")
    return f"{m.group(1)}/{m.group(2)}", (m.group(3) or "").strip("/")


async def github_index(repo: str, path: str = "", refresh: bool = False) -> list[dict]:
    key = f"gh-index:{repo}:{path}"
    if not refresh:
        hit = cache_get(key)
        if hit is not None:
            return hit
    source = "anthropic" if repo == ANTHROPIC_REPO else "github"
    async with client() as c:
        info = await _json(c, f"https://api.github.com/repos/{repo}", _gh_headers())
        branch = info.get("default_branch") or "main"
        tree = await _json(c, f"https://api.github.com/repos/{repo}/git/trees/{quote(branch)}?recursive=1",
                           _gh_headers())
        blobs = [t for t in tree.get("tree") or [] if t.get("type") == "blob"]
        pre = (path + "/") if path else ""
        mds = [t["path"] for t in blobs if t["path"].startswith(pre) and re.search(r"(^|/)SKILL\.md$", t["path"])]
        mds = mds[:80]
        sem = asyncio.Semaphore(8)

        async def one(p):
            d = p.rsplit("/", 1)[0] if "/" in p else ""
            async with sem:
                try:
                    r = await _get(c, f"https://raw.githubusercontent.com/{repo}/{quote(branch)}/{quote(p)}")
                    fm = reg.frontmatter(r.text)
                except MarketError:
                    fm = {}
            name = str(fm.get("name") or (d.rsplit("/", 1)[-1] if d else repo.split("/")[1]))
            files = [{"path": t["path"][len(d) + 1:] if d else t["path"], "size": t.get("size", 0)}
                     for t in blobs if (t["path"].startswith(d + "/") if d else True)]
            return _item(source, f"github:{repo}@{branch}:{d}", name, str(fm.get("title") or name),
                         fm.get("description") or "", author=repo.split("/")[0], version=fm.get("version") or "",
                         url=f"https://github.com/{repo}/tree/{branch}/{d}".rstrip("/"),
                         extra={"repo": repo, "branch": branch, "dir": d, "files": files[:MAX_FILES]})
        items = await asyncio.gather(*(one(p) for p in mds))
    items = [i for i in items if i]
    cache_put(key, items)
    return items


def _parse_gh_id(mid: str) -> tuple[str, str, str]:
    m = re.match(r"^github:([\w.-]+/[\w.-]+)@([^:]+):(.*)$", mid)
    if not m:
        raise MarketError("无效的 GitHub 技能 id")
    return m.group(1), m.group(2), m.group(3)


async def github_detail(mid: str) -> dict:
    repo, branch, d = _parse_gh_id(mid)
    idx = cache_get(f"gh-index:{repo}:") or []
    item = next((i for i in idx if i["id"] == mid), None)
    async with client() as c:
        p = f"{d}/SKILL.md" if d else "SKILL.md"
        r = await _get(c, f"https://raw.githubusercontent.com/{repo}/{quote(branch)}/{quote(p)}")
    fm = reg.frontmatter(r.text)
    item = item or _item("anthropic" if repo == ANTHROPIC_REPO else "github", mid, fm.get("name") or d, "",
                         fm.get("description") or "", author=repo.split("/")[0],
                         url=f"https://github.com/{repo}/tree/{branch}/{d}", extra={"repo": repo, "branch": branch,
                                                                                   "dir": d})
    return {**item, "skill_md": r.text}


async def github_download(mid: str, dest: Path) -> Path:
    """把技能目录的全部文件下到 dest/<dir名>/。文件清单优先用缓存的索引, 没有再拉一次树。"""
    repo, branch, d = _parse_gh_id(mid)
    files = None
    for key in (f"gh-index:{repo}:", f"gh-index:{repo}:{d}"):
        idx = cache_get(key) or []
        hit = next((i for i in idx if i["id"] == mid), None)
        if hit and hit.get("files"):
            files = hit["files"]
            break
    async with client() as c:
        if files is None:
            tree = await _json(c, f"https://api.github.com/repos/{repo}/git/trees/{quote(branch)}?recursive=1",
                               _gh_headers())
            files = [{"path": t["path"][len(d) + 1:] if d else t["path"], "size": t.get("size", 0)}
                     for t in tree.get("tree") or [] if t.get("type") == "blob"
                     and (t["path"].startswith(d + "/") if d else True)]
        if len(files) > MAX_FILES:
            raise MarketError(f"技能文件过多 ({len(files)} 个), 请用 git 方式安装")
        if sum(int(f.get("size") or 0) for f in files) > MAX_BYTES:
            raise MarketError("技能文件总大小超过 15MB")
        root = dest / (d.rsplit("/", 1)[-1] if d else repo.split("/")[1])
        sem = asyncio.Semaphore(8)

        async def one(f):
            rel = f["path"]
            if ".." in rel.split("/"):
                return
            src = f"{d}/{rel}" if d else rel
            async with sem:
                r = await _get(c, f"https://raw.githubusercontent.com/{repo}/{quote(branch)}/{quote(src)}")
            out = root / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(r.content)
        await asyncio.gather(*(one(f) for f in files))
    return root


# ── ClawHub ───────────────────────────────────────────────────────
def _claw_item(it: dict) -> dict:
    owner = it.get("ownerHandle") or ((it.get("publisher") or {}).get("handle")) or ""
    slug = it.get("slug") or ""
    native = (it.get("native") or {}).get("skill") or {}
    stats = it.get("stats") or native.get("stats") or {}
    ver = ((it.get("latestVersion") or {}).get("version")) or ((it.get("tags") or {}).get("latest")) or ""
    if len(str(ver)) > 16:      # tags.latest 有时是内部 id
        ver = ""
    return _item("clawhub", f"clawhub:{owner}/{slug}", slug, it.get("displayName") or slug,
                 it.get("summary") or native.get("summary") or "", author=owner, version=ver,
                 downloads=it.get("downloads") or stats.get("downloads") or 0,
                 url=f"{CLAWHUB}/{owner}/skills/{slug}", extra={"official": bool(it.get("official"))})


async def clawhub_search(q: str) -> list[dict]:
    key = f"clawhub:{q}"
    hit = cache_get(key)
    if hit is not None:
        return hit
    async with client() as c:
        if q.strip():
            d = await _json(c, f"{CLAWHUB}/api/v1/search?q={quote(q)}&limit=20")
            rows = d.get("results") or []
        else:
            d = await _json(c, f"{CLAWHUB}/api/v1/skills?limit=20&sort=downloads")
            rows = d.get("items") or []
    items = [_claw_item(r) for r in rows if r.get("slug")]
    cache_put(key, items)
    return items


def _split_id(mid: str, prefix: str) -> tuple[str, str]:
    m = re.match(rf"^{prefix}:([^/]+)/([\w.@-]+)$", mid)
    if not m:
        raise MarketError("无效的技能 id")
    return m.group(1), m.group(2)


async def clawhub_detail(mid: str) -> dict:
    owner, slug = _split_id(mid, "clawhub")
    q = f"ownerHandle={quote(owner)}"
    async with client() as c:
        d = await _json(c, f"{CLAWHUB}/api/v1/skills/{quote(slug)}?{q}")
        md = (await _get(c, f"{CLAWHUB}/api/v1/skills/{quote(slug)}/file?path=SKILL.md&{q}")).text
    sk = d.get("skill") or {}
    item = _claw_item({**sk, "ownerHandle": owner, "slug": slug,
                       "latestVersion": d.get("latestVersion") or sk.get("latestVersion")})
    return {**item, "skill_md": md}


async def clawhub_download(mid: str) -> bytes:
    owner, slug = _split_id(mid, "clawhub")
    async with client() as c:
        r = await _get(c, f"{CLAWHUB}/api/v1/download?slug={quote(slug)}&ownerHandle={quote(owner)}")
    return r.content


# ── 腾讯 SkillHub ─────────────────────────────────────────────────
def _hub_item(it: dict) -> dict:
    ns = (it.get("namespace") or {}).get("handle") or it.get("owner_name") or ""
    slug = it.get("slug") or ""
    return _item("skillhub", f"skillhub:{ns}/{slug}", slug, it.get("displayName") or it.get("name") or slug,
                 it.get("description_zh") or it.get("summary") or it.get("description") or "",
                 author=((it.get("publisher") or {}).get("name")) or ns, version=it.get("version") or "",
                 downloads=it.get("downloads") or 0, url=f"https://skillhub.cn/skills/{slug}",
                 extra={"category": it.get("category") or ""})


async def skillhub_search(q: str) -> list[dict]:
    key = f"skillhub:{q}"
    hit = cache_get(key)
    if hit is not None:
        return hit
    async with client() as c:
        d = await _json(c, f"{SKILLHUB}/api/v1/search?q={quote(q)}&limit=20")
    items = [_hub_item(r) for r in d.get("results") or [] if r.get("slug")]
    cache_put(key, items)
    return items


async def skillhub_detail(mid: str) -> dict:
    ns, slug = _split_id(mid, "skillhub")
    q = f"namespace={quote(ns)}"
    async with client() as c:
        d = await _json(c, f"{SKILLHUB}/api/v1/skills/{quote(slug)}?{q}")
        md = (await _get(c, f"{SKILLHUB}/api/v1/skills/{quote(slug)}/file?path=SKILL.md&{q}")).text
    sk = d.get("skill") or {}
    item = _hub_item({**sk, "namespace": d.get("namespace") or {"handle": ns}, "slug": slug,
                      "version": (d.get("latestVersion") or {}).get("version") or "",
                      "downloads": (sk.get("stats") or {}).get("downloads") or 0,
                      "publisher": d.get("publisher") or {}})
    sec = d.get("securityReports") or {}
    item["remote_security"] = {k: v.get("statusText") or v.get("status") for k, v in sec.items() if isinstance(v, dict)}
    return {**item, "skill_md": md}


async def skillhub_download(mid: str) -> bytes:
    ns, slug = _split_id(mid, "skillhub")
    async with client() as c:
        r = await _get(c, f"{SKILLHUB}/api/v1/download?slug={quote(slug)}&namespace={quote(ns)}")
    return r.content


# ── 聚合 ──────────────────────────────────────────────────────────
async def _anthropic_search(q: str) -> list[dict]:
    items = await github_index(ANTHROPIC_REPO)
    return _local_search(items, q, limit=30)


async def search(q: str = "", source: str = "all", repo: str = "") -> dict:
    """→ {"items": [...], "errors": {source: 中文原因}}; 单个来源失败不影响其它来源。"""
    q = (q or "").strip()[:100]
    jobs = {}
    if repo:
        r, p = parse_repo(repo)
        jobs["github"] = lambda: github_index(r, p)
    else:
        if source in ("all", "anthropic"):
            jobs["anthropic"] = lambda: _anthropic_search(q)
        if source in ("all", "clawhub"):
            jobs["clawhub"] = lambda: clawhub_search(q)
        if source in ("all", "skillhub"):
            jobs["skillhub"] = lambda: skillhub_search(q)
    if not jobs:
        raise MarketError(f"未知来源 {source}")

    async def run(name, fn):
        try:
            return name, await asyncio.wait_for(fn(), timeout=TIMEOUT * 2.5), ""
        except asyncio.TimeoutError:
            return name, [], f"{SOURCES.get(name, name)} 超时"
        except MarketError as e:
            return name, [], str(e)
        except Exception as e:
            return name, [], f"{type(e).__name__}: {str(e)[:120]}"
    res = await asyncio.gather(*(run(n, f) for n, f in jobs.items()))
    items, errors = [], {}
    for name, got, err in res:
        if err:
            errors[name] = err
        items.extend(got)
    if repo and q:
        items = _local_search(items, q)
    elif q and len(jobs) > 1:   # 多来源合并: 按本地打分重排 (同分保持各来源自己的相关度顺序)
        from .skill_search import score as _score
        order = {id(it): i for i, it in enumerate(items)}
        items.sort(key=lambda it: (-_score(it, q), order[id(it)]))
    for it in items:
        it.pop("files", None)
    installed = {s["name"] for s in reg.list_skills()}
    for it in items:
        it["installed"] = it["name"] in installed
    return {"items": items, "errors": errors}


async def detail(mid: str) -> dict:
    if mid.startswith("github:"):
        return await github_detail(mid)
    if mid.startswith("clawhub:"):
        return await clawhub_detail(mid)
    if mid.startswith("skillhub:"):
        return await skillhub_detail(mid)
    raise MarketError("未知的技能来源")


async def fetch(mid: str, dest: Path) -> tuple[str, object]:
    """下载到暂存: ("dir", Path) 或 ("zip", bytes)。"""
    if mid.startswith("github:"):
        return "dir", await github_download(mid, dest)
    if mid.startswith("clawhub:"):
        return "zip", await clawhub_download(mid)
    if mid.startswith("skillhub:"):
        return "zip", await skillhub_download(mid)
    raise MarketError("未知的技能来源")


def origin_url(mid: str) -> str:
    if mid.startswith("github:"):
        repo, branch, d = _parse_gh_id(mid)
        return f"https://github.com/{repo}/tree/{branch}/{d}".rstrip("/")
    if mid.startswith("clawhub:"):
        o, s = _split_id(mid, "clawhub")
        return f"{CLAWHUB}/{o}/skills/{s}"
    if mid.startswith("skillhub:"):
        ns, s = _split_id(mid, "skillhub")
        return f"https://skillhub.cn/skills/{s}?namespace={ns}"
    return mid
