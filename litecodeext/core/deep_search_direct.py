"""DeepSearch 直连查询 Mixin: 天气 / 加密货币 / A股港股 跳过搜索引擎直接打权威数据源 (从 deep_search.py 拆出)。"""

try:
    from .deep_search_common import httpx
except ImportError:  # core/ 目录直接在 sys.path 上 (import deep_search 方式)
    from deep_search_common import httpx


class DirectQueryMixin:
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
