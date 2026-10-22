"""
LiteCode DeepSearch Module (vLLM版)
───────────────────────────────────
完全使用 vLLM backend，无 Anthropic SDK 依赖。

支持:
  1. vLLM /v1/chat/completions 非流式调用（查询展开 + 摘要合成）
  2. asyncio 并行多查询
  3. 国内搜索引擎 web_fetch（Bing CN / Baidu）
  4. /effort 深度控制（low=3源 / mid=8源 / high=15源）
  5. 结果去重 + 摘要注入
"""

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

# [拆分] 模块级常量/工具函数搬到 deep_search_common.py, 方法按职责拆成 Mixin;
# 这里全部 re-export, 保证 `import deep_search` / `from core.deep_search import ...` 仍可用。
try:
    from .deep_search_common import (
        _wire, httpx, EFFORT, _SPAM_DOMAINS, _is_spam_url, _DEFAULT_SEARCH_SOURCES,
    )
    from .deep_search_direct import DirectQueryMixin
    from .deep_search_apis import FreeApiSearchMixin
    from .deep_search_web import WebFetchSearchMixin
except ImportError:  # core/ 目录直接在 sys.path 上 (import deep_search 方式)
    from deep_search_common import (
        _wire, httpx, EFFORT, _SPAM_DOMAINS, _is_spam_url, _DEFAULT_SEARCH_SOURCES,
    )
    from deep_search_direct import DirectQueryMixin
    from deep_search_apis import FreeApiSearchMixin
    from deep_search_web import WebFetchSearchMixin


class DeepSearchTool(DirectQueryMixin, FreeApiSearchMixin, WebFetchSearchMixin):
    """
    DeepSearch 执行引擎。
    使用 vLLM backend 做查询展开和摘要合成。
    使用国内搜索引擎 web_fetch 获取搜索结果。
    """

    def __init__(self, workspace: Path,
                 backend_url: str = None,
                 api_key: str = "EMPTY",
                 model_id: str = "Qwen3.5-122B",
                 search_sources: list = None):
        self.workspace = workspace
        # [deep-search LLM 后端] 优先用传入/配置的 backend_url; 下面是内置默认(自建 vLLM,
        # 可继续用)。发布脱敏时该 IP 会被替换为占位符 —— 改这里或传参即可换成你自己的端点。
        self.backend_url = (backend_url or "http://your-llm-host.example:8001/v1").rstrip("/")
        self.api_key = api_key
        self.model_id = model_id
        self.search_sources = search_sources or _DEFAULT_SEARCH_SOURCES

    # ── vLLM 非流式调用 ──────────────────────────────
    def _vllm_chat(self, messages: list, max_tokens: int = 2048) -> str:
        """同步调用 vLLM /v1/chat/completions（非流式）。"""
        payload = {
            "model": _wire(self.model_id),
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        import time as _time
        _t0 = _time.time()
        try:
            resp = httpx.post(
                f"{self.backend_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=60.0,
            )
            try:   # [usage-ledger] DeepSearch 查询展开 / 摘要记账
                from lib.usage_ledger import record_http as _usage_rec
                _usage_rec("deep_search", self.model_id, payload, resp, _t0)
            except ImportError:
                pass
            if resp.status_code != 200:
                return ""
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
        except Exception:
            pass
        return ""

    # ── 主入口 ──────────────────────────────────────────
    def run(self, query: str, effort: str = "mid",
            queries: list = None) -> str:
        # /effort 覆盖
        if "/effort high" in query:
            effort = "high"
            query = query.replace("/effort high", "").strip()
        elif "/effort low" in query:
            effort = "low"
            query = query.replace("/effort low", "").strip()

        # 天气类查询：直接 fetch 权威气象站，跳过搜索引擎
        weather_direct = self._try_weather_direct(query)
        if weather_direct:
            return weather_direct

        # [v1.0] 加密货币查询: 直接 Binance / CoinGecko, 跳过搜索引擎
        crypto_direct = self._try_crypto_direct(query)
        if crypto_direct:
            return crypto_direct

        # [v1.0] A股/港股查询: 直接 eastmoney 实时行情
        stock_direct = self._try_stock_direct(query)
        if stock_direct:
            return stock_direct

        max_sources = EFFORT.get(effort, 8)

        # [v1.0] 优先用免费 API (DuckDuckGo Instant / Wikipedia / SearXNG JSON)
        # 这些有结构化响应, 比 HTML 抓取快 10x 且质量高
        api_results = self._search_via_free_apis(query, max_sources)

        # [v1.0] 分层策略，大幅加速 low/mid
        if effort == "low" or (effort == "mid" and not queries):
            # 快速路径：免费 API 够了直接返回, 否则才走 HTML 抓取
            if api_results:
                return self._format_fast(query, api_results, fetch_top=(1 if effort == "mid" else 0))
            results = self._search_via_webfetch(query)
            if not results:
                return f"搜索无结果: {query}。建议用 web_fetch 访问权威网站。"
            return self._format_fast(query, results, fetch_top=(2 if effort == "mid" else 0))

        # high: 完整 pipeline（vLLM 扩展 + 多查询并行 + vLLM 合成）
        if not queries:
            queries = self._expand_queries(query, max_sources)
        results = asyncio.run(self._parallel_search(queries, max_sources))
        # 合并免费 API 结果 (去重)
        if api_results:
            seen = {r.get("url","") for r in results}
            for r in api_results:
                u = r.get("url", "")
                if u and u not in seen:
                    results.append(r)
                    seen.add(u)
        if not results:
            return f"搜索无结果: {query}。建议用 web_fetch 访问权威网站。"
        return self._synthesize(query, results, effort)

    def _format_fast(self, query: str, results: list, fetch_top: int = 0) -> str:
        """快速格式化搜索结果（不调 vLLM）。
        [v1.0] 并行抓前 N 个 URL, 而非串行阻塞。"""
        top_list = results[:8]
        fetched: dict = {}
        if fetch_top > 0 and httpx is not None:
            targets = [(i, r.get("url", ""))
                       for i, r in enumerate(top_list[:fetch_top])
                       if r.get("url")]
            if targets:
                async def _pfetch():
                    loop = asyncio.get_event_loop()
                    tasks = [loop.run_in_executor(None, self._fetch_full, u, 1500)
                             for _, u in targets]
                    try:
                        return await asyncio.wait_for(
                            asyncio.gather(*tasks, return_exceptions=True),
                            timeout=5.0,
                        )
                    except asyncio.TimeoutError:
                        return [t.result() if t.done() and not t.cancelled() else ""
                                for t in tasks]
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        import concurrent.futures as _cf
                        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                            bodies = ex.submit(asyncio.run, _pfetch()).result(timeout=7.0)
                    else:
                        bodies = asyncio.run(_pfetch())
                except Exception:
                    bodies = []
                for (idx, _u), body in zip(targets, bodies or []):
                    if isinstance(body, str) and body:
                        fetched[idx] = body

        lines = ["## 搜索结果"]
        for i, r in enumerate(top_list, 1):
            title   = r.get("title", "")
            url     = r.get("url", "")
            snippet = r.get("snippet", "")
            src     = r.get("source", "")
            idx0 = i - 1
            if idx0 in fetched and len(fetched[idx0]) > len(snippet):
                snippet = fetched[idx0]
            body = f"\n    {snippet[:300]}" if snippet else ""
            lines.append(f"[{i}] {title} ({src})\n    URL: {url}{body}")
        return "\n\n".join(lines)

    # ── 查询展开（用 vLLM 生成多角度查询）──────────────
    def _expand_queries(self, query: str, n: int) -> list:
        # [v1.0] mid 以上都展开；low 只用原始查询
        if n <= 3:
            return [query]
        target = min(n, 5)
        try:
            text = self._vllm_chat(
                messages=[{"role": "user", "content":
                    f"为搜索引擎生成 {target} 个不同角度的搜索查询来研究：{query}\n"
                    f"要求：\n"
                    f"1. 查询语言与原问题一致\n"
                    f"2. 每个查询必须用完全不同的关键词组合，不能只是同义替换\n"
                    f"3. 覆盖不同维度：事实、原因、数据、观点、对比\n"
                    f"4. 只输出 JSON 字符串数组，不要其他任何内容\n"
                    f"例如原问题\"比特币最近为什么下跌\"应展开为：\n"
                    f"[\"BTC 价格 跌幅 本周\", \"加密货币 抛售 原因 2026\", "
                    f"\"美联储 利率 比特币 影响\", \"币圈 恐慌指数 最新\", "
                    f"\"比特币 链上数据 巨鲸 动向\"]"
                }],
                max_tokens=256,
            )
            text = re.sub(r"```json|```", "", text).strip()
            if text.startswith("["):
                qs = json.loads(text)
                if isinstance(qs, list) and len(qs) >= 2:
                    return [str(q) for q in qs[:target]]
        except Exception:
            pass
        # 降级：手动构造 2 个变体
        return [query, f"{query} 最新", f"{query} 分析"]

    # ── 并行搜索（web_fetch 搜索引擎）────────────────
    async def _parallel_search(self, queries: list,
                                max_sources: int) -> list:
        tasks = [self._search_one(q) for q in queries]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)

        # 展平 + 去重（按 URL）
        seen_urls = set()
        flat = []
        for batch in results_nested:
            if isinstance(batch, Exception):
                continue
            for item in (batch or []):
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    flat.append(item)

        return flat[:max_sources]

    async def _search_one(self, query: str) -> list:
        """单次搜索，通过 web_fetch 调国内搜索引擎。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._search_via_webfetch, query)

    def _synthesize(self, query: str, results: list,
                    effort: str) -> str:
        source_lines = []
        # [v1.0] mid 也抓前 3 个全文，high 抓全部
        fetch_count = 0
        max_fetch = {"low": 0, "mid": 3, "high": len(results)}.get(effort, 3)

        for i, r in enumerate(results, 1):
            title   = r.get("title", "untitled")
            url     = r.get("url", "")
            snippet = r.get("snippet", "")
            src     = r.get("source", "")

            body = snippet
            if url and fetch_count < max_fetch:
                full = self._fetch_full(url, 2000)
                if full and len(full) > len(snippet):
                    body = full
                    fetch_count += 1

            source_lines.append(
                f"[{i}] {title} ({src})\n    URL: {url}\n    {body[:500]}"
            )

        sources_text = "\n\n".join(source_lines)

        # 用 vLLM 合成
        summary = self._vllm_chat(
            messages=[{"role": "user", "content":
                f"研究问题: {query}\n\n"
                f"来源 ({len(results)}):\n{sources_text}\n\n"
                f"请用中文写一份结构化研究摘要：\n"
                f"- ## 关键发现 (3-5条)\n"
                f"- ## 详细分析 (引用来源编号 [1],[2]...)\n"
                f"- ## 来源列表 (带URL的编号列表)\n"
                f"重要：如果所有来源均与问题无关，请直接说明'搜索结果无关，建议直接访问权威网站获取信息'，"
                f"并推荐1-2个适合的权威URL供 web_fetch 直接访问。\n"
                f"简洁准确，不要废话。"
            }],
            max_tokens=2048,
        )

        if not summary:
            summary = f"## 搜索结果\n{sources_text}"

        return summary
