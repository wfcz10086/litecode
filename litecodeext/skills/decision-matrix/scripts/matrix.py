#!/usr/bin/env python3
"""加权决策矩阵 + 灵敏度分析 (只用标准库)。

输入 JSON (文件路径或 - 读 stdin):
{
  "criteria": [{"name": "薪资", "weight": 30}, {"name": "成长", "weight": 40, "veto": 2}, ...],
  "options":  {"A公司": {"薪资": 4, "成长": 3}, "B公司": {"薪资": 3, "成长": 5}},
  "scale": 5            # 可选, 打分上限, 默认 5
}
- weight 任意正数, 自动归一化成百分比。
- veto (可选): 一票否决线, 该项得分 < veto 的选项标记为"被否决", 不参与排名。

输出: 排名表、每个标准的贡献、灵敏度 (第一名在哪个权重变化下会被反超)、蒙特卡洛稳健度。
用法: python3 matrix.py input.json [--json]
"""
from __future__ import annotations

import json
import random
import sys


def load(argv: list[str]) -> tuple[dict, bool]:
    args = [a for a in argv if not a.startswith("--")]
    as_json = "--json" in argv
    if not args or args[0] == "-":
        return json.load(sys.stdin), as_json
    with open(args[0], encoding="utf-8") as f:
        return json.load(f), as_json


def normalize(criteria: list[dict]) -> dict[str, float]:
    total = sum(float(c["weight"]) for c in criteria)
    if total <= 0:
        raise SystemExit("权重之和必须 > 0")
    return {c["name"]: float(c["weight"]) / total for c in criteria}


def totals(weights: dict[str, float], options: dict, scale: float) -> dict[str, float]:
    return {o: sum(weights[c] * float(s.get(c, 0)) for c in weights) / scale * 100 for o, s in options.items()}


def vetoed(criteria: list[dict], options: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in criteria:
        if c.get("veto") is None:
            continue
        for o, s in options.items():
            if float(s.get(c["name"], 0)) < float(c["veto"]):
                out.setdefault(o, []).append(f"{c['name']}<{c['veto']}")
    return out


def flip_point(weights: dict[str, float], options: dict, scale: float, top: str, rival: str, crit: str):
    """把 crit 的权重从当前值扫到 0~1 (其余按比例缩放), 返回 rival 反超 top 时的 crit 权重; 不会反超返回 None。"""
    rest = {c: w for c, w in weights.items() if c != crit}
    rest_sum = sum(rest.values())
    cur = weights[crit]
    steps = [i / 200 for i in range(201)]
    # 从当前值向两侧找最近的翻转点
    for w in sorted(steps, key=lambda x: abs(x - cur)):
        if rest_sum > 0:
            nw = {c: v / rest_sum * (1 - w) for c, v in rest.items()}
        else:
            nw = {}
        nw[crit] = w
        t = totals(nw, {top: options[top], rival: options[rival]}, scale)
        if t[rival] > t[top]:
            return w
    return None


def monte_carlo(weights: dict[str, float], options: dict, scale: float, n: int = 2000, jitter: float = 0.3):
    """权重各自随机扰动 ±jitter (相对), 统计每个选项拿第一的比例。"""
    rng = random.Random(42)
    wins = {o: 0 for o in options}
    for _ in range(n):
        nw = {c: max(1e-9, w * (1 + rng.uniform(-jitter, jitter))) for c, w in weights.items()}
        s = sum(nw.values())
        nw = {c: w / s for c, w in nw.items()}
        t = totals(nw, options, scale)
        wins[max(t, key=t.get)] += 1
    return {o: v / n for o, v in wins.items()}


def main():
    data, as_json = load(sys.argv[1:])
    criteria, options = data["criteria"], data["options"]
    scale = float(data.get("scale", 5))
    weights = normalize(criteria)
    veto = vetoed(criteria, options)
    alive = {o: s for o, s in options.items() if o not in veto}
    if not alive:
        raise SystemExit("所有选项都被一票否决, 需要重新找选项或放宽否决线")
    tot = totals(weights, alive, scale)
    ranked = sorted(tot, key=tot.get, reverse=True)
    top = ranked[0]
    flips = []
    if len(ranked) > 1:
        rival = ranked[1]
        for c in weights:
            fp = flip_point(weights, alive, scale, top, rival, c)
            flips.append({"criterion": c, "weight_now": round(weights[c] * 100, 1),
                          "flip_at": None if fp is None else round(fp * 100, 1)})
    mc = monte_carlo(weights, alive, scale) if len(alive) > 1 else {top: 1.0}
    result = {"weights_pct": {c: round(w * 100, 1) for c, w in weights.items()},
              "ranking": [{"option": o, "score": round(tot[o], 1)} for o in ranked],
              "vetoed": veto, "flip_vs_runner_up": flips, "robustness": {o: round(v, 3) for o, v in mc.items()}}
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("## 权重 (归一化)")
    print(" | ".join(f"{c} {w}%" for c, w in result["weights_pct"].items()))
    print("\n## 排名 (满分 100)")
    print("| 名次 | 选项 | 得分 | " + " | ".join(weights) + " |")
    print("|---|---|---|" + "---|" * len(weights))
    for i, o in enumerate(ranked, 1):
        cells = " | ".join(f"{float(alive[o].get(c, 0)):g}" for c in weights)
        print(f"| {i} | {o} | {tot[o]:.1f} | {cells} |")
    if veto:
        print("\n## 被一票否决")
        for o, why in veto.items():
            print(f"- {o}: {', '.join(why)}")
    if flips:
        print(f"\n## 灵敏度: 「{ranked[1]}」在什么权重下反超「{top}」")
        for f in flips:
            if f["flip_at"] is None:
                print(f"- {f['criterion']}: 当前 {f['weight_now']}%, 怎么调都不翻转")
            else:
                print(f"- {f['criterion']}: 当前 {f['weight_now']}% → 调到 {f['flip_at']}% 时翻转")
        print(f"\n## 稳健度 (权重随机 ±30% 扰动 2000 次, 拿第一的比例)")
        for o, v in sorted(mc.items(), key=lambda x: -x[1]):
            print(f"- {o}: {v * 100:.1f}%")
        gap = tot[top] - tot[ranked[1]]
        verdict = "结论稳健" if mc.get(top, 0) >= 0.8 else ("结论偏弱, 取决于权重怎么定" if mc.get(top, 0) >= 0.55 else "基本是平手, 分数帮不了你, 回到价值取舍")
        print(f"\n第一名领先 {gap:.1f} 分; {verdict}。")


if __name__ == "__main__":
    main()
