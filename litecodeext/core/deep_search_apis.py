"""DeepSearch 免费搜索 API Mixin: DDG Instant / Wikipedia / SearXNG / Mwmbl / Marginalia 并行查询 (从 deep_search.py 拆出)。"""

import asyncio
from urllib.parse import quote_plus

try:
    from .deep_search_common import httpx, _is_spam_url
except ImportError:  # core/ 目录直接在 sys.path 上 (import deep_search 方式)
    from deep_search_common import httpx, _is_spam_url


class FreeApiSearchMixin:
    # ── 免费 API 搜索（无 KEY）──────────────────────────
    # [v1.0] 这些 API 有结构化 JSON 响应, 比 HTML 抓取快/稳
    # [v1.2] SearXNG 公共实例 — 跟着 https://searx.space 更新, 加了几个稳定的.
    _SEARXNG_INSTANCES = [
        "https://searx.be",
        "https://paulgo.io",
        "https://search.inetol.net",
        "https://searx.tiekoetter.com",
        "https://priv.au",
        "https://search.bus-hit.me",
        "https://searx.sev.monster",
    ]

    def _search_via_free_apis(self, query: str, max_items: int = 8) -> list:
        """[v1.2] 并行调多个免费搜索 API (无 KEY, 有结构化 JSON 响应), 总预算 7s.
        新增: Mwmbl (开源社区搜索) + Marginalia (独立长尾索引)
        已有: DuckDuckGo Instant + Wikipedia + SearXNG 公共实例轮询
        每个源返回 [{title, url, snippet, source}, ...] 格式。"""
        if httpx is None:
            return []

        async def _gather():
            loop = asyncio.get_event_loop()
            tasks = [
                loop.run_in_executor(None, self._ddg_instant, query),
                loop.run_in_executor(None, self._wiki_api, query),
                loop.run_in_executor(None, self._searxng_first, query),
                loop.run_in_executor(None, self._mwmbl_api, query),
                loop.run_in_executor(None, self._marginalia_api, query),
            ]
            try:
                return await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=7.0,
                )
            except asyncio.TimeoutError:
                return [t.result() if t.done() and not t.cancelled() else []
                        for t in tasks]

        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures as _cf
                    with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                        batches = ex.submit(asyncio.run, _gather()).result(timeout=8.0)
                else:
                    batches = asyncio.run(_gather())
            except Exception:
                batches = []
        except Exception:
            batches = []

        # 合并去重
        all_items: list = []
        seen_urls = set()
        for batch in (batches or []):
            if isinstance(batch, Exception) or not batch:
                continue
            for it in batch:
                u = it.get("url", "")
                if u and u not in seen_urls and not _is_spam_url(u):
                    seen_urls.add(u)
                    all_items.append(it)
                if len(all_items) >= max_items:
                    break
            if len(all_items) >= max_items:
                break
        return all_items

    def _ddg_instant(self, query: str) -> list:
        """DuckDuckGo Instant Answer API (无 KEY). 擅长定义/概念类查询。"""
        try:
            r = httpx.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                timeout=3.0, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 LiteCode"},
            )
            if r.status_code != 200:
                return []
            d = r.json()
            items = []
            # Abstract (主答案)
            abs_txt = (d.get("AbstractText") or "").strip()
            abs_url = (d.get("AbstractURL") or "").strip()
            heading = (d.get("Heading") or query).strip()
            if abs_txt and abs_url:
                items.append({
                    "title": heading, "url": abs_url,
                    "snippet": abs_txt[:400], "source": "ddg-instant",
                })
            # RelatedTopics
            for t in (d.get("RelatedTopics") or [])[:6]:
                if isinstance(t, dict) and t.get("FirstURL"):
                    text = (t.get("Text") or "").strip()
                    if text:
                        items.append({
                            "title": text[:80], "url": t["FirstURL"],
                            "snippet": text[:300], "source": "ddg-instant",
                        })
            return items
        except Exception:
            return []

    def _wiki_api(self, query: str) -> list:
        """Wikipedia REST API (zh + en, 无 KEY). 擅长人物/概念/历史类查询。"""
        items = []
        # 中文优先
        for lang in ("zh", "en"):
            try:
                r = httpx.get(
                    f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote_plus(query)}",
                    timeout=3.0, follow_redirects=True,
                    headers={"User-Agent": "LiteCode/1.0 (contact: none)"},
                )
                if r.status_code != 200:
                    continue
                d = r.json()
                title = d.get("title") or query
                extract = (d.get("extract") or "").strip()
                url = ((d.get("content_urls") or {}).get("desktop") or {}).get("page", "")
                if extract and url:
                    items.append({
                        "title": title, "url": url,
                        "snippet": extract[:500],
                        "source": f"wikipedia-{lang}",
                    })
                    break  # zh 拿到就不要 en
            except Exception:
                continue
        return items

    def _searxng_first(self, query: str) -> list:
        """遍历 SearXNG 公共实例, 第一个返回 JSON 的就用。"""
        for base in self._SEARXNG_INSTANCES:
            try:
                r = httpx.get(
                    f"{base}/search",
                    params={"q": query, "format": "json", "safesearch": 1},
                    timeout=3.0, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 LiteCode", "Accept": "application/json"},
                )
                if r.status_code != 200 or not r.text or r.text[:1] not in "{[":
                    continue
                try:
                    d = r.json()
                except Exception:
                    continue
                items = []
                for it in (d.get("results") or [])[:6]:
                    u = it.get("url", "")
                    if not u or _is_spam_url(u):
                        continue
                    items.append({
                        "title": (it.get("title") or "").strip()[:120],
                        "url": u,
                        "snippet": (it.get("content") or "").strip()[:300],
                        "source": "searxng",
                    })
                if items:
                    return items
            except Exception:
                continue
        return []

    # [v1.2] Mwmbl: 开源社区搜索 (非营利, 无 key), 有结构化 JSON API
    def _mwmbl_api(self, query: str) -> list:
        try:
            r = httpx.get(
                "https://api.mwmbl.org/search",
                params={"q": query, "s": "1"},
                timeout=3.5, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 LiteCode",
                         "Accept": "application/json"},
            )
            if r.status_code != 200 or not r.text:
                return []
            try:
                d = r.json()
            except Exception:
                return []
            items = []
            raw = d if isinstance(d, list) else (d.get("results") or [])
            for it in raw[:6]:
                u = (it.get("url") or "").strip()
                if not u or _is_spam_url(u):
                    continue
                snippet = (it.get("extract") or it.get("content")
                           or it.get("snippet") or "").strip()
                items.append({
                    "title": (it.get("title") or "").strip()[:120],
                    "url": u,
                    "snippet": snippet[:300],
                    "source": "mwmbl",
                })
            return items
        except Exception:
            return []

    # [v1.2] Marginalia: 独立搜索引擎, 擅长长尾/技术博客/非商业内容, 无 key
    def _marginalia_api(self, query: str) -> list:
        try:
            r = httpx.get(
                "https://api.search.marginalia.nu/public/search/",
                params={"query": query, "count": 6},
                timeout=3.5, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 LiteCode",
                         "Accept": "application/json"},
            )
            if r.status_code != 200 or not r.text:
                return []
            try:
                d = r.json()
            except Exception:
                return []
            items = []
            for it in (d.get("results") or [])[:6]:
                u = (it.get("url") or "").strip()
                if not u or _is_spam_url(u):
                    continue
                items.append({
                    "title": (it.get("title") or "").strip()[:120],
                    "url": u,
                    "snippet": (it.get("description") or "").strip()[:300],
                    "source": "marginalia",
                })
            return items
        except Exception:
            return []
