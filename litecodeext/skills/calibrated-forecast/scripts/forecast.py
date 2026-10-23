#!/usr/bin/env python3
"""概率校准小工具 (只用标准库)。

子命令:
  update  --prior 0.2 --lr 3 --lr 0.5 [--note "证据1" --note "证据2"]
          从基础比率出发, 按似然比 (证据在"为真"时出现的概率 / 在"为假"时出现的概率) 逐条更新。
  combine 0.6 0.7 0.45
          合并多个独立估计 (对数赔率平均), 并给出极差提示。
  brier   forecasts.csv
          给历史预测打分。CSV 两列: prob,outcome (outcome 为 1/0), 可选第三列 label。
          输出 Brier 分数和分桶校准表 (说 70% 的事, 实际发生了多少)。
"""
from __future__ import annotations

import argparse
import csv
import math
import sys


def to_odds(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return p / (1 - p)


def to_p(o: float) -> float:
    return o / (1 + o)


def cmd_update(a):
    if not 0 < a.prior < 1:
        sys.exit("--prior 必须在 0 和 1 之间 (不含端点)")
    notes = a.note or []
    p = a.prior
    print(f"起点 (基础比率): {p * 100:.1f}%")
    for i, lr in enumerate(a.lr or []):
        if lr <= 0:
            sys.exit("似然比必须 > 0")
        new = to_p(to_odds(p) * lr)
        tag = notes[i] if i < len(notes) else f"证据{i + 1}"
        arrow = "↑" if new > p else ("↓" if new < p else "→")
        print(f"- {tag}: 似然比 {lr:g} → {p * 100:.1f}% {arrow} {new * 100:.1f}%")
        p = new
    print(f"最终: {p * 100:.1f}%")
    big = [lr for lr in (a.lr or []) if lr >= 5 or lr <= 0.2]
    if big:
        print("提示: 有似然比 ≥5 或 ≤0.2 的强证据, 请再确认它真的这么有区分力 (常见高估)。")


def cmd_combine(a):
    ps = [float(x) for x in a.probs]
    if any(not 0 < p < 1 for p in ps):
        sys.exit("每个概率必须在 0 和 1 之间")
    lo = sum(math.log(to_odds(p)) for p in ps) / len(ps)
    print(f"合并估计 (对数赔率平均): {to_p(math.exp(lo)) * 100:.1f}%")
    print(f"简单平均: {sum(ps) / len(ps) * 100:.1f}%   极差: {min(ps) * 100:.0f}% ~ {max(ps) * 100:.0f}%")
    if max(ps) - min(ps) > 0.3:
        print("提示: 各估计分歧 > 30 个百分点, 先弄清分歧来自哪个假设, 再谈合并。")


def cmd_brier(a):
    rows = []
    with open(a.csv, encoding="utf-8") as f:
        for r in csv.reader(f):
            if not r or r[0].strip().lower() in ("prob", "p", "概率"):
                continue
            p = float(r[0])
            p = p / 100 if p > 1 else p
            rows.append((p, int(float(r[1]))))
    if not rows:
        sys.exit("CSV 里没有数据")
    brier = sum((p - o) ** 2 for p, o in rows) / len(rows)
    base = sum(o for _, o in rows) / len(rows)
    ref = sum((base - o) ** 2 for _, o in rows) / len(rows)
    print(f"样本 {len(rows)} 条, Brier = {brier:.3f} (0 最好; 全猜 50% = 0.250; 只报基础比率 = {ref:.3f})")
    print("\n| 预测区间 | 条数 | 平均预测 | 实际发生率 | 判断 |")
    print("|---|---|---|---|---|")
    for lo in range(0, 100, 10):
        hi = lo + 10
        b = [(p, o) for p, o in rows if lo / 100 <= p < hi / 100 or (hi == 100 and p == 1)]
        if not b:
            continue
        mp = sum(p for p, _ in b) / len(b)
        hr = sum(o for _, o in b) / len(b)
        if len(b) < 5:
            verdict = "样本太少"
        elif abs(hr - mp) <= 0.1:
            verdict = "校准良好"
        else:
            side = "报高了 (实际更少发生)" if hr < mp else "报低了 (实际更常发生)"
            conf = "过度自信" if abs(hr - 0.5) < abs(mp - 0.5) else "过于保守"
            verdict = f"{side}, {conf}"
        print(f"| {lo}-{hi}% | {len(b)} | {mp * 100:.0f}% | {hr * 100:.0f}% | {verdict} |")


def main():
    ap = argparse.ArgumentParser(description="概率校准小工具")
    sp = ap.add_subparsers(dest="cmd", required=True)
    u = sp.add_parser("update")
    u.add_argument("--prior", type=float, required=True)
    u.add_argument("--lr", type=float, action="append")
    u.add_argument("--note", action="append")
    c = sp.add_parser("combine")
    c.add_argument("probs", nargs="+")
    b = sp.add_parser("brier")
    b.add_argument("csv")
    a = ap.parse_args()
    {"update": cmd_update, "combine": cmd_combine, "brier": cmd_brier}[a.cmd](a)


if __name__ == "__main__":
    main()
