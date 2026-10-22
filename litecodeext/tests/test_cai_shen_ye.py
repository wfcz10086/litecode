"""财神爷仓位计算: 股票按一手取整、合约按步长取整 + 强平价、止损在强平价之外必须报警。"""
import importlib.util
from pathlib import Path

import pytest

_p = Path(__file__).resolve().parent.parent / "skills" / "cai-shen-ye" / "scripts" / "position.py"
_spec = importlib.util.spec_from_file_location("cai_position", _p)
pos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pos)


def test_stock_lot_rounding():
    p = pos.plan(200000, 26.10, 24.50, market="hk", lot=200)
    assert p["qty"] == 1200 and p["loss_if_stopped"] == 1920 and p["limited_by"] == "单笔风险"


def test_stock_cap_limit():
    p = pos.plan(100000, 10, 9.9, max_pos=20)          # 止损很窄, 受单票上限约束
    assert p["qty"] == 2000 and p["limited_by"] == "保证金上限"


def test_futures_short_liq_and_fees():
    p = pos.plan(5000, 80000, 81200, market="binance", side="short", leverage=10, step=0.001)
    unit_loss = 1200 + 0.0005 * (80000 + 81200)
    assert p["qty"] == pytest.approx(int(50 / unit_loss / 0.001) * 0.001)
    assert p["liq_price_est"] == pytest.approx(80000 * (1 + 0.1 - 0.004))
    assert not any("强平" in w and "之外" in w for w in p["warnings"])


def test_futures_stop_beyond_liq_warns():
    p = pos.plan(5000, 80000, 77600, market="binance", leverage=50, step=0.001)
    assert any("先被强平" in w for w in p["warnings"]) and any("杠杆过高" in w for w in p["warnings"])


def test_bad_inputs():
    with pytest.raises(ValueError):
        pos.plan(1000, 10, 11)                          # 做多止损在上方
    with pytest.raises(ValueError):
        pos.plan(1000, 10, 11, side="short")           # 股票不支持做空
