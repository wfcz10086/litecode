#!/usr/bin/env python3
"""财神爷 · 仓位计算器: 按"每笔最多亏账户 risk%"算数量, 受单笔保证金上限约束; 股票按一手取整, 合约按步长取整。

股票 (现货, 做多):
  position.py --capital 200000 --entry 26.10 --stop 24.50 [--risk 1] [--max-pos 25] [--market a|hk|us] [--lot N]
币安 U 本位合约 (做多/做空 + 杠杆, 逐仓强平价估算, 含开平手续费):
  position.py --market binance --capital 5000 --side short --entry 82850 --stop 84200 --leverage 10 \
              [--step 0.001 --min-qty 0.001 --min-notional 100] [--mmr 0.4] [--fee 0.05]
通用: [--atr 1200 --atr-mult 2 代替 --stop] [--target 价格] [--winrate 0.45 --rr 2.5] [--json]
"""
import argparse
import json
import math
import sys

LOT = {"a": 100, "hk": 100, "us": 1}   # 港股每手股数因股而异, 用 --lot 指定


def _floor_step(x: float, step: float) -> float:
    n = math.floor(x / step + 1e-9)
    return round(n * step, 10)


def plan(capital, entry, stop=None, atr=None, atr_mult=2.0, risk=1.0, max_pos=25.0, market="a", lot=None,
         side="long", leverage=1.0, step=None, min_qty=0.0, min_notional=0.0, mmr=0.4, fee=None,
         target=None, winrate=None, rr=None) -> dict:
    futures = market == "binance"
    side = side.lower()
    if side not in ("long", "short"):
        raise ValueError("--side 只能是 long 或 short")
    if not futures and side == "short":
        raise ValueError("股票现货只算做多; 做空请用 --market binance (合约)")
    if capital <= 0 or entry <= 0 or leverage < 1:
        raise ValueError("资金、开仓价必须大于 0, 杠杆 ≥ 1")
    sign = 1 if side == "long" else -1
    if stop is None:
        if not atr:
            raise ValueError("需要 --stop 止损价, 或 --atr 波动幅度")
        stop = round(entry - sign * atr * atr_mult, 8)
    per_unit = (entry - stop) * sign
    if per_unit <= 0:
        raise ValueError(f"止损价 {stop} 方向不对: 做多要低于开仓价, 做空要高于开仓价 {entry}")
    fee = (0.05 if futures else 0.0) if fee is None else fee     # 币安合约吃单约 0.05%/边
    unit_loss = per_unit + fee / 100 * (entry + stop)            # 触发止损时每单位亏损 (含开平手续费)
    risk_money = capital * risk / 100
    q_risk = risk_money / unit_loss
    q_cap = capital * max_pos / 100 * leverage / entry           # 保证金不超过账户 max_pos%
    unit = step if futures else (lot or LOT.get(market, 1))
    qty = _floor_step(min(q_risk, q_cap), unit) if futures else math.floor(min(q_risk, q_cap) / unit) * unit
    notional = qty * entry
    out = {
        "market": market, "side": side, "capital": capital, "risk_pct": risk, "risk_money": round(risk_money, 2),
        "entry": entry, "stop": stop, "stop_pct": round(per_unit / entry * 100, 2), "fee_pct_per_side": fee,
        "qty": qty, "unit": unit, "notional": round(notional, 2), "leverage": leverage,
        "margin": round(notional / leverage, 2), "margin_pct": round(notional / leverage / capital * 100, 2),
        "loss_if_stopped": round(qty * unit_loss, 2), "loss_pct": round(qty * unit_loss / capital * 100, 2),
        "limited_by": "保证金上限" if q_cap < q_risk else "单笔风险", "warnings": [],
    }
    if futures or leverage > 1:
        liq = entry * (1 - 1 / leverage + mmr / 100) if side == "long" else entry * (1 + 1 / leverage - mmr / 100)
        out["liq_price_est"] = round(liq, 6)
        out["liq_distance_pct"] = round(abs(entry - liq) / entry * 100, 2)
        if (side == "long" and stop <= liq) or (side == "short" and stop >= liq):
            out["warnings"].append(f"止损 {stop} 在强平价 {liq:.6g} 之外: 会先被强平而不是止损! 降低杠杆或收紧止损。")
        elif out["liq_distance_pct"] < out["stop_pct"] * 1.5:
            out["warnings"].append("强平价离止损太近 (不足止损距离 1.5 倍), 插针可能直接强平, 建议降杠杆。")
        if leverage > 20:
            out["warnings"].append(f"{leverage:g} 倍杠杆过高, 财神爷建议主流币 ≤ 10 倍、山寨币 ≤ 5 倍。")
    if qty <= 0:
        out["warnings"].append("按当前风险预算连最小单位都开不了: 止损太宽或资金太少, 建议放弃这笔。")
    elif futures and (qty < min_qty or notional < min_notional):
        out["warnings"].append(f"数量 {qty} / 名义价值 {notional:.2f} 低于交易所最小要求 "
                               f"({min_qty} / {min_notional} USDT), 下不了单。")
    if out["stop_pct"] > (8 if futures else 15):
        out["warnings"].append(f"止损距离 {out['stop_pct']}% 偏宽, 仓位被压得很小; 考虑等更好的位置。")
    if target:
        rr_real = (target - entry) * sign / per_unit
        out.update(target=target, reward_risk=round(rr_real, 2))
        if rr_real < 1.5:
            out["warnings"].append(f"盈亏比只有 {rr_real:.2f}, 低于 1.5, 性价比差。")
    if winrate is not None and rr:
        kelly = winrate - (1 - winrate) / rr
        out["kelly_pct"] = round(kelly * 100, 2)
        out["half_kelly_pct"] = round(max(0.0, kelly / 2) * 100, 2)
        if kelly <= 0:
            out["warnings"].append("按这组胜率和盈亏比, 凯利值 ≤ 0: 长期是亏钱的系统, 不该下注。")
    return out


def render(p: dict) -> str:
    fut = p["market"] == "binance"
    side_cn = "做多" if p["side"] == "long" else "做空"
    u = "张/币" if fut else "股"
    rows = [("账户 / 单笔风险", f"{p['capital']:,.2f} · {p['risk_pct']}% = {p['risk_money']:,.2f}"),
            ("方向 / 开仓 / 止损", f"{side_cn} {p['entry']} / {p['stop']} (距离 {p['stop_pct']}%)"),
            ("建议数量", f"{p['qty']:g} {u} (步长 {p['unit']:g}, 受 {p['limited_by']} 限制)")]
    if fut or p["leverage"] > 1:
        rows += [("名义价值 / 杠杆", f"{p['notional']:,.2f} / {p['leverage']:g} 倍"),
                 ("占用保证金", f"{p['margin']:,.2f} ({p['margin_pct']}%)"),
                 ("强平价 (逐仓估算)", f"{p['liq_price_est']} (距开仓 {p['liq_distance_pct']}%)")]
    else:
        rows.append(("占用资金", f"{p['notional']:,.2f} ({p['margin_pct']}%)"))
    rows.append(("触发止损亏损", f"{p['loss_if_stopped']:,.2f} ({p['loss_pct']}%)"
                 + (f", 含手续费 {p['fee_pct_per_side']}%/边" if p["fee_pct_per_side"] else "")))
    if "reward_risk" in p:
        rows.append(("目标价 / 盈亏比", f"{p['target']} / {p['reward_risk']}"))
    if "kelly_pct" in p:
        rows.append(("凯利 / 半凯利", f"{p['kelly_pct']}% / {p['half_kelly_pct']}% (仓位上限参考)"))
    out = ["| 项目 | 数值 |", "|---|---|"] + [f"| {a} | {b} |" for a, b in rows]
    out += [f"⚠ {w}" for w in p["warnings"]]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="财神爷仓位计算器")
    ap.add_argument("--capital", type=float, required=True)
    ap.add_argument("--entry", type=float, required=True)
    ap.add_argument("--stop", type=float)
    ap.add_argument("--atr", type=float)
    ap.add_argument("--atr-mult", type=float, default=2.0)
    ap.add_argument("--risk", type=float, default=1.0, help="单笔风险占账户百分比: 保守 0.5 / 稳健 1 / 积极 2")
    ap.add_argument("--max-pos", type=float, default=25.0, help="单笔保证金(现货=资金)最多占账户百分比")
    ap.add_argument("--market", choices=list(LOT) + ["binance"], default="a")
    ap.add_argument("--lot", type=int, help="股票每手股数 (港股按个股)")
    ap.add_argument("--side", default="long")
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--step", type=float, default=0.001, help="合约数量步长 (binance.py 可查)")
    ap.add_argument("--min-qty", type=float, default=0.0)
    ap.add_argument("--min-notional", type=float, default=0.0)
    ap.add_argument("--mmr", type=float, default=0.4, help="维持保证金率 %%, 主流币小仓位约 0.4")
    ap.add_argument("--fee", type=float, help="手续费 %%/边, 合约默认 0.05, 股票默认 0")
    ap.add_argument("--target", type=float)
    ap.add_argument("--winrate", type=float)
    ap.add_argument("--rr", type=float)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        p = plan(a.capital, a.entry, a.stop, a.atr, a.atr_mult, a.risk, a.max_pos, a.market, a.lot, a.side,
                 a.leverage, a.step, a.min_qty, a.min_notional, a.mmr, a.fee, a.target, a.winrate, a.rr)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(json.dumps(p, ensure_ascii=False, indent=1) if a.json else render(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
