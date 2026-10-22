#!/usr/bin/env python3
"""币安 U 本位合约公开行情 (免 key, 只读): 标记价格、资金费率、24h 涨跌、合约规格、ATR。

用法: binance.py BTCUSDT [ETHUSDT ...] [--interval 4h] [--json]
输出每个合约: 标记价 / 指数价 / 资金费率 (及下次结算) / 24h 涨跌与成交额 / 数量步长 / 最小数量 / 最小名义价值 /
ATR14 (按 --interval 的 K 线), 以及可直接粘给 position.py 的参数。
"""
import argparse
import json
import sys
import time
import urllib.request

FAPI = "https://fapi.binance.com"
_UA = {"User-Agent": "Mozilla/5.0"}


def _get(path: str, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(urllib.request.Request(f"{FAPI}{path}?{q}", headers=_UA), timeout=10) as r:
        return json.loads(r.read().decode())


def norm(sym: str) -> str:
    s = sym.upper().replace("/", "").replace("-", "").replace("_", "")
    return s if s.endswith(("USDT", "USDC", "BUSD")) else s + "USDT"


def atr14(sym: str, interval: str = "4h") -> float:
    k = _get("/fapi/v1/klines", symbol=sym, interval=interval, limit=15)
    trs, prev = [], None
    for row in k:
        h, l, c = float(row[2]), float(row[3]), float(row[4])
        trs.append(h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev)))
        prev = c
    trs = trs[1:] or trs
    return sum(trs) / len(trs)


def info(sym: str, interval: str = "4h", spec_cache: dict | None = None) -> dict:
    sym = norm(sym)
    pi = _get("/fapi/v1/premiumIndex", symbol=sym)
    t24 = _get("/fapi/v1/ticker/24hr", symbol=sym)
    if spec_cache is None:
        spec_cache = {s["symbol"]: s for s in _get("/fapi/v1/exchangeInfo")["symbols"]}
    spec = spec_cache.get(sym)
    if not spec:
        raise ValueError(f"币安 U 本位合约里没有 {sym}")
    f = {x["filterType"]: x for x in spec["filters"]}
    lot = f.get("MARKET_LOT_SIZE") or f.get("LOT_SIZE") or {}
    return {
        "symbol": sym, "mark": float(pi["markPrice"]), "index": float(pi["indexPrice"]),
        "funding_rate_pct": round(float(pi["lastFundingRate"]) * 100, 4),
        "next_funding": time.strftime("%m-%d %H:%M", time.localtime(int(pi["nextFundingTime"]) / 1000)),
        "change_24h_pct": float(t24["priceChangePercent"]), "quote_volume_24h": float(t24["quoteVolume"]),
        "step": float(lot.get("stepSize", 0.001)), "min_qty": float(lot.get("minQty", 0.001)),
        "min_notional": float((f.get("MIN_NOTIONAL") or {}).get("notional", 5)),
        "tick": float((f.get("PRICE_FILTER") or {}).get("tickSize", 0.01)),
        "atr14": round(atr14(sym, interval), 6), "atr_interval": interval,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="币安合约公开行情")
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--interval", default="4h", help="ATR 用的 K 线周期: 15m/1h/4h/1d")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cache = {s["symbol"]: s for s in _get("/fapi/v1/exchangeInfo")["symbols"]}
        rows = [info(s, a.interval, cache) for s in a.symbols]
    except Exception as e:
        print(f"ERROR: 币安接口调用失败: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for r in rows:
        print(f"## {r['symbol']} 标记价 {r['mark']} (指数 {r['index']}) · 24h {r['change_24h_pct']:+.2f}% · "
              f"成交额 {r['quote_volume_24h'] / 1e8:.2f} 亿 USDT")
        print(f"  资金费率 {r['funding_rate_pct']}% (下次 {r['next_funding']}) · ATR14({r['atr_interval']}) {r['atr14']}")
        print(f"  规格: 数量步长 {r['step']} · 最小数量 {r['min_qty']} · 最小名义 {r['min_notional']} USDT")
        print(f"  position.py 参数: --market binance --entry {r['mark']} --step {r['step']} --min-qty {r['min_qty']} "
              f"--min-notional {r['min_notional']} --atr {r['atr14']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
