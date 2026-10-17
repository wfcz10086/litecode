"""DeepSearch 网页抓取 Mixin: 搜索引擎 HTML 抓取 / 结果页解析 / DDG 兜底 / 正文全文 fetch (从 deep_search.py 拆出)。"""

import asyncio
import re
import subprocess
from urllib.parse import quote_plus

try:
    from .deep_search_common import httpx, _is_spam_url
except ImportError:  # core/ 目录直接在 sys.path 上 (import deep_search 方式)
    from deep_search_common import httpx, _is_spam_url


class WebFetchSearchMixin:
    # ── HTML 抓取搜索引擎（免费 API 失败时的兜底）──────────
    _SEARCH_SOURCE_TIMEOUT = 3.0
    _SEARCH_TOTAL_TIMEOUT = 6.0

    def _fetch_one_source(self, source: dict, encoded_q: str, headers: dict) -> list:
        """单源抓取, 3s 硬超时。"""
        url = source["url_template"].replace("{q}", encoded_q)
        try:
            resp = httpx.get(url, headers=headers,
                             timeout=self._SEARCH_SOURCE_TIMEOUT,
                             follow_redirects=True)
            if resp.status_code != 200:
                return []
            items = self._parse_search_html(resp.text, source["name"])
            out = []
            for r in items:
                u = r.get("url", "")
                if u and not _is_spam_url(u):
                    r["source"] = source["name"]
                    out.append(r)
            return out
        except Exception:
            return []

    def _search_via_webfetch(self, query: str) -> list:
        """
        [v1.0] 并行抓所有搜索源, 单源 3s, 总预算 6s.
        返回 [{title, url, snippet, source}, ...]
        """
        encoded_q = quote_plus(query)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        async def _gather():
            loop = asyncio.get_event_loop()
            tasks = [
                loop.run_in_executor(None, self._fetch_one_source, src, encoded_q, headers)
                for src in self.search_sources
            ]
            try:
                return await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self._SEARCH_TOTAL_TIMEOUT,
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

        all_items = []
        seen_urls = set()
        for batch in (batches or []):
            if isinstance(batch, Exception) or not batch:
                continue
            for r in batch:
                u = r.get("url", "")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    all_items.append(r)

        if not all_items:
            return self._fallback_ddg(query)
        return all_items

    def _parse_search_html(self, html: str, source_name: str) -> list:
        """从搜索引擎 HTML 提取结果。"""
        items = []

        # [v1.0] 统一用模块级 _SPAM_DOMAINS (包含 weixin 垃圾)
        def _is_bad_url(url: str) -> bool:
            return _is_spam_url(url)

        if "duckduckgo" in source_name:
            # DDG HTML 版 — 尝试两种已知 class 格式
            for pattern in [
                r'<a[^>]+href="(https?://[^"]+)"[^>]*class="result__a"[^>]*>(.*?)</a>',
                r'class="result__a"[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                r'<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            ]:
                for m in re.finditer(pattern, html, re.S):
                    url = m.group(1)
                    title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                    if url and title and "duckduckgo" not in url and not _is_bad_url(url):
                        items.append({"title": title, "url": url, "snippet": ""})
                    if len(items) >= 10:
                        break
                if items:
                    break
            # DDG snippet
            if items:
                for m in re.finditer(
                    r'class="result__snippet"[^>]*>(.*?)</(?:a|div|span)>', html, re.S
                ):
                    snippet = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                    idx = len([i for i in items if i["snippet"]])
                    if idx < len(items):
                        items[idx]["snippet"] = snippet[:200]

        elif "google" in source_name:
            for pattern in [
                r'<a[^>]+href="(https?://[^"]+)"[^>]*>.*?<h3[^>]*>(.*?)</h3>',
                r'<h3[^>]*>.*?<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            ]:
                for m in re.finditer(pattern, html, re.S | re.I):
                    url = m.group(1)
                    title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                    if url and title and "google.com" not in url and not _is_bad_url(url):
                        items.append({"title": title, "url": url, "snippet": ""})
                    if len(items) >= 8:
                        break
                if items:
                    break

        elif "bing" in source_name:
            # Bing CN 结果：多种 pattern 兼容不同版本的 HTML
            for pattern in [
                r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>.*?<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>.*?(?:<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>)?',
                r'<li[^>]+class="[^"]*b_algo[^"]*"[^>]*>.*?<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>.*?(?:<p[^>]*>(.*?)</p>)?',
                r'<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            ]:
                for m in re.finditer(pattern, html, re.S | re.I):
                    url = m.group(1)
                    title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                    snippet = re.sub(r"<[^>]+>", "", m.group(3) if len(m.groups()) >= 3 and m.group(3) else "").strip()
                    if url and title and not _is_bad_url(url) and "bing.com" not in url:
                        items.append({"title": title, "url": url, "snippet": snippet[:200]})
                    if len(items) >= 8:
                        break
                if items:
                    break

        elif "baidu" in source_name:
            pattern = r'<h3[^>]*class="[ct]?-?title"[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>'
            for m in re.finditer(pattern, html, re.S | re.I):
                url = m.group(1)
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if url and title and not _is_bad_url(url):
                    items.append({"title": title, "url": url, "snippet": ""})
                if len(items) >= 8:
                    break

        elif "sogou" in source_name:
            # Sogou: <h3><a href="..." id="...">TITLE</a></h3>
            for pattern in [
                r'<h3[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                r'<a[^>]+href="(https?://[^"]+)"[^>]+id="[^"]*"[^>]*>(.*?)</a>',
            ]:
                for m in re.finditer(pattern, html, re.S | re.I):
                    url = m.group(1)
                    title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                    if url and title and len(title) >= 6 and "sogou.com" not in url and not _is_bad_url(url):
                        items.append({"title": title, "url": url, "snippet": ""})
                    if len(items) >= 8:
                        break
                if items:
                    break

        elif "so360" in source_name or "360" in source_name:
            # 360 Search: <h3><a href="..." data-...>TITLE</a></h3>
            for pattern in [
                r'<h3[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                r'class="res-title"[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            ]:
                for m in re.finditer(pattern, html, re.S | re.I):
                    url = m.group(1)
                    title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                    if url and title and len(title) >= 6 and "so.com" not in url and not _is_bad_url(url):
                        items.append({"title": title, "url": url, "snippet": ""})
                    if len(items) >= 8:
                        break
                if items:
                    break

        elif "ecosia" in source_name:
            # Ecosia 结构类似 Bing, 用 h2 a
            for m in re.finditer(
                r'<a[^>]+class="[^"]*result[^"]*"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                html, re.S | re.I,
            ):
                url = m.group(1); title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if url and title and "ecosia.org" not in url and not _is_bad_url(url):
                    items.append({"title": title, "url": url, "snippet": ""})
                if len(items) >= 8: break
            if not items:
                for m in re.finditer(
                    r'<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                    html, re.S | re.I,
                ):
                    url = m.group(1); title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                    if url and title and "ecosia.org" not in url and not _is_bad_url(url):
                        items.append({"title": title, "url": url, "snippet": ""})
                    if len(items) >= 8: break

        elif "yandex" in source_name:
            for m in re.finditer(
                r'<a[^>]+class="[^"]*(?:OrganicTitle|Link)[^"]*"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                html, re.S | re.I,
            ):
                url = m.group(1); title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if url and title and "yandex.com" not in url and not _is_bad_url(url):
                    items.append({"title": title, "url": url, "snippet": ""})
                if len(items) >= 8: break

        elif "qwant" in source_name:
            # Qwant 结果通常在 data-testid="web-result" 或 a.external
            for m in re.finditer(
                r'<a[^>]+href="(https?://[^"]+)"[^>]+(?:data-testid|rel)="[^"]*(?:web-result|external)[^"]*"[^>]*>(.*?)</a>',
                html, re.S | re.I,
            ):
                url = m.group(1); title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if url and title and "qwant.com" not in url and not _is_bad_url(url):
                    items.append({"title": title, "url": url, "snippet": ""})
                if len(items) >= 8: break

        elif "mojeek" in source_name:
            # Mojeek <a class="title" href="...">TITLE</a>
            for m in re.finditer(
                r'<a[^>]+class="title"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                html, re.S | re.I,
            ):
                url = m.group(1); title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if url and title and "mojeek.com" not in url and not _is_bad_url(url):
                    items.append({"title": title, "url": url, "snippet": ""})
                if len(items) >= 8: break

        if not items:
            # 通用回退：更严格的过滤
            skip_domains = {
                "bing.com", "baidu.com", "google.com", "microsoft.com",
                "duckduckgo.com", "yahoo.com", "sogou.com", "so.com",
                "beian.gov.cn", "miit.gov.cn", "beian.miit.gov.cn",
                "creditchina.gov.cn", "gsxt.gov.cn",
            }
            for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.S):
                url = m.group(1)
                title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if not title or len(title) < 8:
                    continue
                if any(d in url for d in skip_domains):
                    continue
                # 过滤明显的无关链接（登录、注册、帮助页面等）
                if any(kw in url.lower() for kw in ("/login", "/register", "/help", "/about", "javascript:")):
                    continue
                items.append({"title": title[:100], "url": url, "snippet": ""})
                if len(items) >= 5:
                    break

        return items

    def _fallback_ddg(self, query: str) -> list:
        """curl DuckDuckGo HTML 降级。"""
        try:
            url = f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
            result = subprocess.run(
                ["curl", "-sL", "--max-time", "10",
                 "-H", "User-Agent: Mozilla/5.0", url],
                capture_output=True, text=True, timeout=15
            )
            html = result.stdout
            items = []
            pattern = r'<a[^>]+href="(https?://[^"]+)"[^>]*class="result__a"[^>]*>([^<]+)<'
            for m in re.finditer(pattern, html):
                url_, title = m.group(1), m.group(2).strip()
                if "duckduckgo" not in url_:
                    items.append({"title": title, "url": url_, "snippet": ""})
                if len(items) >= 5:
                    break
            return items
        except Exception:
            return []

    # ── 全文 fetch（high effort 时启用）──────────────
    def _fetch_full(self, url: str, max_chars: int = 3000) -> str:
        """httpx 获取正文，去除 HTML 标签。"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
            resp = httpx.get(url, headers=headers, timeout=10.0, follow_redirects=True)
            html = resp.text
            html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
            html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s{2,}", " ", text).strip()
            return text[:max_chars]
        except Exception:
            return ""
