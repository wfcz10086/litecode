"""core/market_quote.py — 股票/指数/ETF 实时行情 (免 key)。

"今天 A 股怎么样""茅台现在多少钱"这类问题走这里拿准确数字, 不靠网页搜索 (搜索结果常是旧闻/港媒/别的日期)。
数据源:
  - 腾讯行情 qt.gtimg.cn: 报价主源 (A 股 / 港股 / 美股, 个股 / 指数 / ETF), 名称解析用 smartbox。
  - 东方财富 push2: 只取两市涨跌家数和行业涨跌榜; 它对频繁请求会 502/断连, 失败就略过这两项。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

_QT = "https://qt.gtimg.cn/q="
_SMARTBOX = "https://smartbox.gtimg.cn/s3/"
_EM_HOSTS = ("push2.eastmoney.com", "1.push2.eastmoney.com")
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
_CN_TZ = timezone(timedelta(hours=8))

A_INDEXES = ["sh000001", "sz399001", "sz399006", "sh000300", "sh000688"]
GLOBAL_INDEXES = ["hkHSI", "hkHSTECH", "usDJI", "usINX", "usIXIC"]

# 常用指数简称 → 腾讯代码 (搜索接口对"恒指""纳指"这类简称不稳定)
INDEX_ALIASES = {
    "上证": "sh000001", "上证指数": "sh000001", "沪指": "sh000001", "大盘": "sh000001",
    "深证": "sz399001", "深证成指": "sz399001", "深成指": "sz399001",
    "创业板": "sz399006", "创业板指": "sz399006", "沪深300": "sh000300", "科创50": "sh000688",
    "北证50": "bj899050", "上证50": "sh000016", "中证500": "sh000905", "中证1000": "sh000852",
    "恒指": "hkHSI", "恒生指数": "hkHSI", "恒生科技": "hkHSTECH", "恒生科技指数": "hkHSTECH",
    "道指": "usDJI", "道琼斯": "usDJI", "标普": "usINX", "标普500": "usINX",
    "纳指": "usIXIC", "纳斯达克": "usIXIC",
}
_INDEX_CODES = set(INDEX_ALIASES.values()) | set(GLOBAL_INDEXES)
_MARKET_RANK = {"sh": 0, "sz": 0, "bj": 0, "hk": 1, "us": 2}


def _client():
    # 国内行情接口直连; 不走容器里给海外站点配的代理
    return httpx.Client(timeout=8, headers=_HEADERS, trust_env=False)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_amount(v) -> str:
    if not v:
        return "-"
    return f"{v / 1e8:.2f}亿" if v >= 1e8 else f"{v / 1e4:.0f}万"


def _fmt_time(raw: str) -> str:
    raw = (raw or "").strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})\d{2}", raw)          # A 股 20261022100048
    if m:
        return f"{m[2]}-{m[3]} {m[4]}:{m[5]}"
    m = re.match(r"(\d{4})[/-](\d{2})[/-](\d{2}) (\d{2}):(\d{2})", raw)         # 港股 / 美股 (美股为美东时间)
    return f"{m[2]}-{m[3]} {m[4]}:{m[5]}" if m else raw


def _local_code(code: str) -> str | None:
    """用户直接给的代码 → 腾讯代码; 不像代码返回 None。"""
    c = code.strip()
    if re.fullmatch(r"(sh|sz|bj)\d{6}|hk\d{5}|hk[A-Z]+|us[A-Z.]+", c, re.I):
        return c[:2].lower() + c[2:].upper() if c[:2].lower() in ("hk", "us") else c.lower()
    if re.fullmatch(r"\d{6}", c):
        if c[0] in "659":                       # 6xxxxx 沪主板/科创, 5xxxxx 沪 ETF, 9xxxxx 沪 B
            return "sh" + c
        if c[0] in "48" or c.startswith("92"):  # 北交所
            return "bj" + c
        return "sz" + c
    if re.fullmatch(r"\d{5}", c):
        return "hk" + c
    if re.fullmatch(r"[A-Z]{1,5}", c):          # 全大写才当美股代码, 免得把中文拼音缩写误判
        return "us" + c
    return None


def resolve(name: str) -> str | None:
    """名称/代码 → 腾讯行情代码。默认股票优先; 名字带"指数"/"ETF"时优先对应类型; 同类按 A 股 > 港股 > 美股。"""
    name = name.strip()
    if not name:
        return None
    if name in INDEX_ALIASES:
        return INDEX_ALIASES[name]
    local = _local_code(name)
    if local:
        return local
    with _client() as c:
        r = c.get(_SMARTBOX, params={"v": 2, "q": name, "t": "all"})
    body = r.text.split("=", 1)[-1].strip().strip(";").strip('"')
    if not body or body == "N":
        return None
    if "\\u" in body:                         # 接口返回 \uXXXX 转义; 已是中文就别再解 (会乱码)
        body = body.encode("latin-1", "backslashreplace").decode("unicode_escape")
    want = "ZS" if "指数" in name else ("ETF" if "etf" in name.lower() else "GP")
    cands = []
    for i, item in enumerate(body.split("^")):
        p = item.split("~")
        if len(p) < 5 or p[0] not in _MARKET_RANK:
            continue
        typ = p[4].upper()
        code = p[1].split(".")[0].upper() if p[0] == "us" else p[1]
        exact = 0 if p[2].lower() == name.lower() else 1      # 名称完全一致优先 (免得"英伟达"选到港股的做空英伟达 ETF)
        cands.append((0 if typ.startswith(want) else 1, exact, _MARKET_RANK[p[0]], i, p[0] + code))
    return min(cands)[-1] if cands else None


def _fetch(codes: list[str]) -> list[dict]:
    if not codes:
        return []
    with _client() as c:
        r = c.get(_QT + ",".join(codes))
        r.raise_for_status()
    text = r.content.decode("gbk", "replace")
    rows = {}
    for line in text.split(";"):
        if "=" not in line:
            continue
        key, val = line.strip().split("=", 1)
        f = val.strip().strip('"').split("~")
        if len(f) < 38 or not f[1]:
            continue
        code = key.strip()[2:]                  # v_sh600519 → sh600519
        amount = _f(f[37]) or 0
        if code[:2] in ("sh", "sz", "bj"):
            amount *= 1e4                       # A 股成交额单位是万元
        elif code in _INDEX_CODES:
            amount = 0                          # 港美指数这一栏单位不一, 不显示
        rows[code] = {"code": code, "name": f[1], "price": f[3], "prev": f[4], "open": f[5],
                      "chg": f[31], "pct": _f(f[32]), "high": f[33], "low": f[34],
                      "amount": amount, "time": _fmt_time(f[30]), "raw_time": f[30]}
    return [rows[c] for c in codes if c in rows]


def _table(rows: list[dict]) -> list[str]:
    out = ["| 名称 | 代码 | 最新 | 涨跌幅 | 涨跌 | 今开 | 最高 | 最低 | 昨收 | 成交额 | 时间 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in rows:
        pct = "-" if d["pct"] is None else f"{d['pct']:+.2f}%"
        out.append(f"| {d['name']} | {d['code']} | {d['price']} | {pct} | {d['chg']} | {d['open']} | "
                   f"{d['high']} | {d['low']} | {d['prev']} | {_fmt_amount(d['amount'])} | {d['time']} |")
    return out


def _em_get(path: str, params: dict) -> dict:
    """东方财富: 两个域名各试一次, 都不行抛异常 (调用方略过)。"""
    last = None
    with _client() as c:
        for host in _EM_HOSTS:
            try:
                r = c.get(f"https://{host}{path}", params=params, timeout=5)
                if r.status_code == 200:
                    return (r.json() or {}).get("data") or {}
                last = f"HTTP {r.status_code}"
            except Exception as e:
                last = str(e)
    raise RuntimeError(last or "eastmoney unavailable")


def _breadth() -> str:
    d = _em_get("/api/qt/ulist.np/get", {"fltt": 2, "secids": "1.000001,0.399001", "fields": "f104,f105,f106"})
    rows = d.get("diff") or []
    up, down, flat = (sum(int(x.get(k) or 0) for x in rows) for k in ("f104", "f105", "f106"))
    return f"两市: 上涨 {up} 家, 下跌 {down} 家, 平盘 {flat} 家" if up or down else ""


def _boards() -> list[str]:
    out = []
    for top, label in ((1, "行业涨幅前五"), (0, "行业跌幅前五")):
        d = _em_get("/api/qt/clist/get", {"pn": 1, "pz": 5, "po": top, "np": 1, "fltt": 2, "fid": "f3",
                                          "fs": "m:90+t:2+f:!50", "fields": "f12,f14,f3"})
        items = [f"{b['f14']} {b['f3']:+.2f}%" for b in (d.get("diff") or []) if isinstance(b.get("f3"), (int, float))]
        if items:
            out.append(f"{label}: " + "、".join(items))
    return out


def _stale_note(rows: list[dict]) -> str:
    days = {d["raw_time"][:8] for d in rows if re.fullmatch(r"\d{14}", d.get("raw_time") or "")}
    today = datetime.now(_CN_TZ).strftime("%Y%m%d")
    if days and today not in days:
        last = max(days)
        return f"\n注意: A 股数据时间是 {last[:4]}-{last[4:6]}-{last[6:]}, 今天休市或尚未开盘, 以上为最近一个交易日。"
    return ""


def overview() -> str:
    a = _fetch(A_INDEXES)
    lines = [f"## A 股行情 (腾讯/东方财富, 查询于 {datetime.now(_CN_TZ):%Y-%m-%d %H:%M})", *_table(a)]
    turnover = sum(d["amount"] for d in a if d["code"] in ("sh000001", "sz399001"))
    extra = []
    try:
        b = _breadth()
        if b:
            extra.append(b)
    except Exception:
        pass
    if turnover:
        extra.append(f"两市成交额 {_fmt_amount(turnover)}")
    lines.append("\n" + "; ".join(extra) if extra else "")
    try:
        lines += _boards()
    except Exception:
        lines.append("(行业涨跌榜暂时取不到)")
    lines += ["\n## 港股 / 美股主要指数 (美股时间为美东时间)", *_table(_fetch(GLOBAL_INDEXES))]
    return "\n".join(x for x in lines if x is not None) + _stale_note(a)


def quote(names: list[str]) -> str:
    codes, missing = [], []
    for n in names[:20]:
        try:
            code = resolve(str(n))
        except Exception:
            code = None
        (codes.append(code) if code else missing.append(str(n)))
    codes = list(dict.fromkeys(codes))
    rows = _fetch(codes)
    found = {d["code"] for d in rows}
    missing += [c for c in codes if c not in found]
    lines = [f"## 行情 (腾讯, 查询于 {datetime.now(_CN_TZ):%Y-%m-%d %H:%M}; 美股时间为美东时间)", *_table(rows)] if rows else []
    if missing:
        lines.append("没找到: " + "、".join(missing) + " (换成股票代码或全称再试)")
    return ("\n".join(lines) + _stale_note(rows)) if lines else "ERROR: 没查到任何行情"


def run(symbols=None, overview_flag: bool | None = None) -> str:
    if httpx is None:
        return "ERROR: httpx 未安装"
    if isinstance(symbols, str):
        symbols = [s for s in re.split(r"[,，、;；\s]+", symbols) if s]
    symbols = [str(s).strip() for s in (symbols or []) if str(s).strip()]
    t0 = time.time()
    try:
        parts = []
        if overview_flag or not symbols:
            parts.append(overview())
        if symbols:
            parts.append(quote(symbols))
        return "\n\n".join(parts) + f"\n\n(用时 {time.time() - t0:.1f}s; 行情数据仅供参考)"
    except Exception as e:
        return f"ERROR: 行情接口调用失败: {e} — 可改用 web_search 搜新闻"
