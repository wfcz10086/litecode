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


# [wire-name 2026-08-28] config 的 `id` 是显示名, 上游认的可能是别的名字
# (自建 vLLM 的 --served-model-name)。本模块直接打上游 /chat/completions,
# 不经过 lib.transport, 所以要自己翻译 —— 否则会 404
# ("The model `xxx-local` does not exist", 实测撞过)。
def _wire(mid: str) -> str:
    try:
        from lib.config import wire_model_name
        return wire_model_name(mid)
    except Exception:
        return mid

try:
    import httpx
except ImportError:
    httpx = None  # [v1.0] 降级处理，后续使用时检查

EFFORT = {"low": 3, "mid": 8, "high": 15}

# [v1.0] 垃圾域名黑名单 — 搜索结果里的 weixin/广告/备案页全部过滤掉
_SPAM_DOMAINS = frozenset({
    # sogou 常返回的微信公众号 (都是转发/软文/SEO垃圾)
    "mp.weixin.qq.com", "weixin.qq.com",
    # 政府备案/许可证查询页, 几乎不是用户想要的
    "beian.gov.cn", "beian.miit.gov.cn", "miit.gov.cn",
    "creditchina.gov.cn", "gsxt.gov.cn", "samr.gov.cn",
    "icp.gov.cn", "mca.gov.cn", "nxiang.gov.cn",
    # 国内图床 / 淘宝联盟 / 广告聚合
    "img.alicdn.com", "click.taobao.com", "union.click.jd.com",
    # 搜索引擎自身的广告/跳转页
    "ad.doubleclick.net", "baidu.com/link?", "www.sogou.com/link?",
})

def _is_spam_url(url: str) -> bool:
    if not url:
        return True
    u = url.lower()
    return any(d in u for d in _SPAM_DOMAINS)

# ── 默认搜索源（可被 config.json 覆盖）────────────────
_DEFAULT_SEARCH_SOURCES = [
    {"name": "duckduckgo", "url_template": "https://html.duckduckgo.com/html/?q={q}"},
    {"name": "sogou",      "url_template": "https://www.sogou.com/web?query={q}"},
    {"name": "so360",      "url_template": "https://www.so.com/s?q={q}"},
    {"name": "baidu",      "url_template": "https://www.baidu.com/s?wd={q}"},
]


class DeepSearchTool:
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
        try:
            resp = httpx.post(
                f"{self.backend_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=60.0,
            )
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

    # ── 天气直连（跳过搜索引擎）─────────────────────────
    _CITY_CODES = {
        "北京": "101010100", "上海": "101020100", "广州": "101280101",
        "深圳": "101280601", "杭州": "101210101", "南京": "101190101",
        "成都": "101270101", "重庆": "101040100", "武汉": "101200101",
        "西安": "101110101", "天津": "101030100", "苏州": "101190401",
        "宁波": "101210401", "长沙": "101250101", "郑州": "101180101",
        "济南": "101120101", "青岛": "101120201", "大连": "101070201",
        "沈阳": "101070101", "哈尔滨": "101050101", "昆明": "101290101",
        "贵阳": "101260101", "南宁": "101300101", "合肥": "101220101",
        "福州": "101230101", "厦门": "101230201", "南昌": "101240101",
        "石家庄": "101090101", "太原": "101100101", "呼和浩特": "101080101",
        "乌鲁木齐": "101130101", "兰州": "101160101", "西宁": "101150101",
        "银川": "101170101", "拉萨": "101140101", "海口": "101310101",
    }

    def _try_weather_direct(self, query: str) -> str:
        """对天气类查询直接 fetch 中国天气网，跳过搜索引擎。"""
        import re as _re
        weather_kw = ("天气", "气温", "温度", "下雨", "晴", "阴", "雪", "forecast", "weather")
        if not any(kw in query for kw in weather_kw):
            return ""
        # 找城市
        city = None
        for c in self._CITY_CODES:
            if c in query:
                city = c
                break
        if not city:
            return ""
        code = self._CITY_CODES[city]
        url = f"https://www.weather.com.cn/weather/{ code }.shtml"
        try:
            content = self._fetch_full(url, 3000)
            if content and len(content) > 100:
                return f"## {city}天气（来源：中国天气网）\n\n{content[:2500]}\n\n来源URL: {url}"
        except Exception:
            pass
        return ""

    # ── 加密货币直连（Binance / CoinGecko, 跳过搜索引擎）──
    _CRYPTO_SYMBOLS = {
        "btc": "BTCUSDT", "比特币": "BTCUSDT", "bitcoin": "BTCUSDT",
        "eth": "ETHUSDT", "以太坊": "ETHUSDT", "ethereum": "ETHUSDT",
        "dot": "DOTUSDT", "polkadot": "DOTUSDT", "波卡": "DOTUSDT",
        "sol": "SOLUSDT", "solana": "SOLUSDT",
        "bnb": "BNBUSDT",  "币安币": "BNBUSDT",
        "doge": "DOGEUSDT", "狗狗币": "DOGEUSDT", "dogecoin": "DOGEUSDT",
        "xrp": "XRPUSDT", "瑞波": "XRPUSDT", "ripple": "XRPUSDT",
        "ltc": "LTCUSDT", "莱特币": "LTCUSDT", "litecoin": "LTCUSDT",
        "ada": "ADAUSDT", "cardano": "ADAUSDT",
        "avax": "AVAXUSDT", "avalanche": "AVAXUSDT",
        "link": "LINKUSDT", "chainlink": "LINKUSDT",
        "trx": "TRXUSDT", "tron": "TRXUSDT", "波场": "TRXUSDT",
    }

    def _try_crypto_direct(self, query: str) -> str:
        """[v1.0] 加密货币查询: 直接打 Binance public API + CoinGecko 备选。"""
        ql = query.lower()
        crypto_kw = ("币价", "行情", "价格", "btc", "eth", "比特币", "以太坊",
                     "binance", "币安", "加密货币", "cryptocurrency", "crypto",
                     "usdt", "coin")
        if not any(kw in ql for kw in crypto_kw):
            return ""
        # 找出现的币种
        symbols = []
        for alias, sym in self._CRYPTO_SYMBOLS.items():
            if alias in ql and sym not in symbols:
                symbols.append(sym)
        if not symbols:
            return ""
        if httpx is None:
            return ""
        lines = ["## 加密货币实时行情（来源：Binance Public API）\n"]
        ok_count = 0
        for sym in symbols[:6]:  # 最多 6 个
            try:
                resp = httpx.get(
                    f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}",
                    timeout=6.0,
                )
                if resp.status_code != 200:
                    continue
                d = resp.json()
                price = float(d.get("lastPrice", 0))
                change_pct = float(d.get("priceChangePercent", 0))
                high = float(d.get("highPrice", 0))
                low = float(d.get("lowPrice", 0))
                volume = float(d.get("quoteVolume", 0))  # USDT volume
                arrow = "📈" if change_pct >= 0 else "📉"
                lines.append(
                    f"### {sym} {arrow}\n"
                    f"- 当前价: **${price:,.4f}**\n"
                    f"- 24h 涨跌: {change_pct:+.2f}%\n"
                    f"- 24h 高/低: ${high:,.4f} / ${low:,.4f}\n"
                    f"- 24h 成交额: ${volume:,.0f} USDT\n"
                )
                ok_count += 1
            except Exception:
                continue
        if ok_count == 0:
            return ""
        lines.append(f"\n来源: https://api.binance.com/api/v3/ticker/24hr")
        lines.append(f"查询: {query}")
        return "\n".join(lines)

    # ── A股/港股直连（eastmoney 实时行情）────────────────
    _STOCK_CODES = {
        # A股指数
        "上证指数": "1.000001", "上证综指": "1.000001", "a股": "1.000001",
        "沪指": "1.000001",
        "深证成指": "0.399001", "深成指": "0.399001",
        "创业板指": "0.399006", "创业板": "0.399006",
        "沪深300": "1.000300", "科创50": "1.000688",
        # 港股指数
        "恒生指数": "100.HSI", "恒指": "100.HSI",
        "国企指数": "100.HSCEI", "红筹指数": "100.HSCCI",
        # 常见个股（code 前缀: 1=沪, 0=深）
        "贵州茅台": "1.600519", "茅台": "1.600519",
        "五粮液": "0.000858", "招商银行": "1.600036",
        "工商银行": "1.601398", "宁德时代": "0.300750",
        "比亚迪": "1.002594", "平安银行": "0.000001",
        "万科a": "0.000002", "中国平安": "1.601318",
        "伊利股份": "0.600887", "格力电器": "0.000651",
        "美的集团": "0.000333", "海尔智家": "1.600690",
        "中国石油": "1.601857", "中国石化": "1.600028",
        "中国移动": "1.600941", "中国人寿": "1.601628",
        "腾讯控股": "116.00700", "阿里巴巴": "116.09988",
        "美团": "116.03690", "小米集团": "116.01810",
    }

    def _try_stock_direct(self, query: str) -> str:
        """[v1.0] A股/港股查询: eastmoney 实时行情 API。"""
        ql = query.lower()
        stock_kw = ("股价", "股票", "a股", "港股", "沪指", "上证", "深证",
                    "创业板", "恒生", "点位", "指数", "行情")
        if not any(kw in ql for kw in stock_kw) and not any(
            k in query for k in self._STOCK_CODES
        ):
            return ""
        if httpx is None:
            return ""
        hits = []
        for alias, code in self._STOCK_CODES.items():
            if alias in ql:
                hits.append((alias, code))
                if len(hits) >= 6:
                    break
        if not hits:
            return ""
        lines = ["## A股/港股实时行情（来源：东方财富 eastmoney）\n"]
        ok_count = 0
        for alias, code in hits:
            try:
                # eastmoney push2 API: https://push2.eastmoney.com/api/qt/stock/get?secid={code}&fields=...
                url = (
                    "https://push2.eastmoney.com/api/qt/stock/get"
                    f"?secid={code}"
                    "&fields=f43,f44,f45,f46,f47,f48,f60,f170,f58,f71,f86"
                )
                resp = httpx.get(url, timeout=6.0, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/",
                })
                if resp.status_code != 200:
                    continue
                d = resp.json().get("data") or {}
                if not d:
                    continue
                # eastmoney 价格字段通常需除以 100
                # f43=现价, f44=最高, f45=最低, f46=开盘, f47=成交量(手), f48=成交额
                # f60=昨收, f170=涨跌幅(%), f58=名称, f86=时间戳
                price_raw = d.get("f43")
                pclose_raw = d.get("f60")
                pct = d.get("f170")
                name = d.get("f58", alias)
                if price_raw is None:
                    continue
                # 指数/个股精度不同, eastmoney 统一放大 100; 部分港股放大 1000
                divisor = 100
                price = price_raw / divisor if isinstance(price_raw, (int, float)) else 0
                pclose = pclose_raw / divisor if isinstance(pclose_raw, (int, float)) else 0
                high_raw = d.get("f44")
                low_raw = d.get("f45")
                high = high_raw / divisor if isinstance(high_raw, (int, float)) else 0
                low = low_raw / divisor if isinstance(low_raw, (int, float)) else 0
                arrow = "📈" if (pct or 0) >= 0 else "📉"
                lines.append(
                    f"### {name} ({code}) {arrow}\n"
                    f"- 当前价: **{price:.2f}**\n"
                    f"- 涨跌幅: {pct:+.2f}%\n"
                    f"- 昨收: {pclose:.2f}  最高: {high:.2f}  最低: {low:.2f}\n"
                )
                ok_count += 1
            except Exception:
                continue
        if ok_count == 0:
            return ""
        lines.append(f"\n来源: eastmoney push2 API")
        lines.append(f"查询: {query}")
        return "\n".join(lines)

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
