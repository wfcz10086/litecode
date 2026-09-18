---
name: crypto-tracker
description: >
  加密货币实时数据追踪与分析。使用 Binance、CoinGecko 等免费公开API获取BTC/ETH/任意币种
  的实时价格、K线、深度、24h涨跌幅、市值排名等数据。触发关键词：BTC、ETH、比特币、以太坊、
  币安、加密货币、币价、行情、K线、涨跌、市值、交易量、山寨币、USDT、合约、现货、
  crypto、binance、coinmarket。遇到任何加密货币相关的数据查询必须使用本技能。

## Keywords
btc eth bitcoin ethereum 比特币 以太坊 币安 binance coinbase 加密货币 cryptocurrency
币价 行情 k线 kline 涨跌 市值 交易量 volume 山寨币 altcoin usdt 合约 现货 spot futures
defi nft sol bnb doge xrp 狗狗币 莱特币 瑞波 链上 whale 巨鲸 清算 liquidation
---

# Crypto Tracker Skill

## 免费 API 源（无需 API Key）

### Binance Public API（首选，最快）
```
基础URL: https://api.binance.com

# 实时价格（单币种）
GET /api/v3/ticker/price?symbol=BTCUSDT

# 24h 行情摘要
GET /api/v3/ticker/24hr?symbol=BTCUSDT

# K线数据（interval: 1m/5m/15m/1h/4h/1d/1w）
GET /api/v3/klines?symbol=BTCUSDT&interval=1h&limit=24

# 深度（买卖盘口）
GET /api/v3/depth?symbol=BTCUSDT&limit=10

# 最近成交
GET /api/v3/trades?symbol=BTCUSDT&limit=20

# 所有交易对价格
GET /api/v3/ticker/price
```

### CoinGecko Free API（无需Key，限50次/分钟）
```
基础URL: https://api.coingecko.com/api/v3

# 实时价格（多币种）
GET /simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd,cny

# 市值排名Top100
GET /coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1

# 历史价格（最近N天）
GET /coins/bitcoin/market_chart?vs_currency=usd&days=30

# 币种详情
GET /coins/bitcoin

# 全球市场概览
GET /global
```

### 备用源
```
# OKX Public API
GET https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT

# 恐惧贪婪指数
GET https://api.alternative.me/fng/?limit=10
```

## 标准工作流

### 快速价格查询
```python
# web_fetch 直接调 Binance API
web_fetch(url="https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT")
```
返回 JSON，提取 lastPrice / priceChangePercent / volume / highPrice / lowPrice。

### K线分析（写Python脚本）
```python
import json, urllib.request

def get_klines(symbol="BTCUSDT", interval="1h", limit=48):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    data = json.loads(urllib.request.urlopen(url).read())
    # [open_time, open, high, low, close, volume, close_time, ...]
    return [{"time": k[0], "open": float(k[1]), "high": float(k[2]),
             "low": float(k[3]), "close": float(k[4]), "vol": float(k[5])} for k in data]

def analyze(klines):
    closes = [k["close"] for k in klines]
    current = closes[-1]
    ma7 = sum(closes[-7:]) / 7
    ma25 = sum(closes[-25:]) / 25 if len(closes) >= 25 else None
    high = max(k["high"] for k in klines)
    low = min(k["low"] for k in klines)
    total_vol = sum(k["vol"] for k in klines)
    return {
        "current": current, "ma7": ma7, "ma25": ma25,
        "high": high, "low": low, "total_volume": total_vol,
        "trend": "bullish" if current > ma7 else "bearish"
    }
```

### 多币种对比
```python
# CoinGecko 一次请求多个币种
web_fetch(url="https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,binancecoin&vs_currencies=usd&include_24hr_change=true&include_market_cap=true")
```

## 输出格式
```
[CRYPTO] BTC/USDT 实时行情
价格: $67,432.15 (+2.3% 24h)
24h 高/低: $68,100 / $65,800
24h 交易量: 28,543 BTC ($1.92B)
MA7: $66,890 | MA25: $65,200
趋势: 短线看多（价格 > MA7 > MA25）
恐惧贪婪指数: 72 (贪婪)
```

## 注意事项
- Binance API 国内可直连，无需代理
- CoinGecko 免费版限 50 次/分钟，做批量查询时注意间隔
- 所有 API 返回 UTC 时间，需转换为北京时间 (+8)
- 价格数据用于参考，不构成投资建议
