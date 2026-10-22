"""行情工具: 代码识别 / 腾讯行情解析 / 名称解析排序 (mock, 不打真实接口)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import market_quote as mq  # noqa: E402


def test_local_code():
    assert mq._local_code("600519") == "sh600519"
    assert mq._local_code("510300") == "sh510300"
    assert mq._local_code("300750") == "sz300750"
    assert mq._local_code("830799") == "bj830799"
    assert mq._local_code("00700") == "hk00700"
    assert mq._local_code("NVDA") == "usNVDA"
    assert mq._local_code("usnvda") == "usNVDA"
    assert mq._local_code("贵州茅台") is None and mq._local_code("gzmt") is None
    assert mq.resolve("恒指") == "hkHSI" and mq.resolve("大盘") == "sh000001"


class _R:
    def __init__(self, text=None, content=None):
        self.text, self.content, self.status_code = text, content, 200

    def raise_for_status(self):
        pass


class _C:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **k):
        return self.resp


def _qt_line(code, name, price, pct, amount, tm):
    f = [""] * 50
    f[1], f[3], f[4], f[5], f[30], f[31], f[32], f[33], f[34], f[37] = name, price, "1", "1", tm, "0.1", pct, "2", "0.5", amount
    return f'v_{code}="' + "~".join(f) + '";'


def test_fetch_parse_units(monkeypatch):
    text = _qt_line("sh600519", "贵州茅台", "1314.69", "0.38", "91700", "20261022100100") + \
        _qt_line("hkHSI", "恒生指数", "23203.5", "-1.90", "3180000", "2026/10/22 09:47:00")
    monkeypatch.setattr(mq, "_client", lambda: _C(_R(content=text.encode("gbk"))))
    rows = mq._fetch(["sh600519", "hkHSI", "usNVDA"])
    assert [r["code"] for r in rows] == ["sh600519", "hkHSI"]
    assert rows[0]["amount"] == 91700 * 1e4 and rows[0]["time"] == "10-22 10:01" and rows[0]["pct"] == 0.38
    assert rows[1]["amount"] == 0           # 港美指数不显示成交额
    assert "9.17亿" in "\n".join(mq._table(rows))


def test_resolve_prefers_exact_name_then_stock_then_a_share(monkeypatch):
    hint = 'v_hint="hk~07388~南方2倍做空英伟达~nf~GP^us~nvda.oq~英伟达~ywd~GP"'
    monkeypatch.setattr(mq, "_client", lambda: _C(_R(text=hint)))
    assert mq.resolve("英伟达") == "usNVDA"
    hint = 'v_hint="sh~000847~腾讯济安~txja~ZS^hk~00700~腾讯控股~txkg~GP^hk~80700~腾讯控股r~txkgr~GP"'
    monkeypatch.setattr(mq, "_client", lambda: _C(_R(text=hint)))
    assert mq.resolve("腾讯") == "hk00700"
    hint = 'v_hint="us~shoc.n~半导体etf~b~GP^sh~512480~半导体ETF国联安~b~ETF"'
    monkeypatch.setattr(mq, "_client", lambda: _C(_R(text=hint)))
    assert mq.resolve("半导体ETF") == "sh512480"
