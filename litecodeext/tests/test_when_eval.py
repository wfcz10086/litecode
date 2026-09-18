#!/usr/bin/env python3
"""test_when_eval.py — DAG step.when v2 DSL 单元测试.

跑法: python3 test_when_eval.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from _when_eval import evaluate_when  # noqa: E402


_CTX = {
    "steps": {
        "fetch": {
            "status": "success",
            "output": "fetched 40 coins: BTC ETH DOT RVN",
            "json": {"top": [{"sym": "BTC", "price": 67890.5}, {"sym": "ETH", "price": 3200}],
                     "total_volume": 1234567.89},
            "tool_count": 4,
            "duration_ms": 12000,
        },
        "analyze": {
            "status": "failure",
            "output": "no BTC trend data",
            "tool_count": 1,
            "duration_ms": 800,
        },
        "report": {
            "status": "skipped",
            "output": "",
        },
    },
    "env": {"DRY_RUN": "1", "REGION": "us-east"},
    "cfg": {"web_ui": {"port": 18790}, "agent": {"max_iterations": 9999}},
}


cases = [
    # (描述, when, 期望 passed, 解释)

    # ── 空/无 ──
    ("空 None 通过", None, True),
    ("空 list 通过", [], True),
    ("空 dict 通过", {}, True),
    ("空 str 通过", "", True),

    # ── v1.3 兼容 ──
    ("v1: success:fetch", "success:fetch", True),
    ("v1: failure:fetch", "failure:fetch", False),
    ("v1: failure:analyze", "failure:analyze", True),
    ("v1: !success:fetch", "!success:fetch", False),
    ("v1: contains:fetch:BTC", "contains:fetch:BTC", True),
    ("v1: contains:fetch:NOPE", "contains:fetch:NOPE", False),
    ("v1: !contains:fetch:NOPE", "!contains:fetch:NOPE", True),

    # ── v2 字符串中缀 ──
    ("v2 infix eq", "$step.fetch.status eq success", True),
    ("v2 infix ne", "$step.fetch.status ne failure", True),
    ("v2 infix is", "$step.fetch.status is success", True),
    ("v2 infix contains", '$step.fetch.output contains BTC', True),
    ("v2 infix startswith", '$step.fetch.output startswith "fetched"', True),
    ("v2 infix endswith", '$step.fetch.output endswith RVN', True),
    ("v2 infix gt 数值", "$step.fetch.tool_count gt 3", True),
    ("v2 infix lt 数值", "$step.fetch.tool_count lt 3", False),
    ("v2 infix ge 边界", "$step.fetch.tool_count ge 4", True),

    # ── v2 dict ──
    ("dict eq", {"eq": ["$step.fetch.status", "success"]}, True),
    ("dict ne", {"ne": ["$step.fetch.status", "failure"]}, True),
    ("dict gt 数值", {"gt": ["$step.fetch.tool_count", 3]}, True),
    ("dict gt 数值 false", {"gt": ["$step.fetch.tool_count", 100]}, False),
    ("dict regex", {"regex": ["$step.fetch.output", "BTC|ETH"]}, True),
    ("dict in 集合", {"in": ["BTC", ["BTC", "ETH", "DOT"]]}, True),
    ("dict not_in", {"not_in": ["XYZ", ["BTC", "ETH"]]}, True),
    ("dict exists", {"exists": ["$step.fetch.output"]}, True),
    ("dict not_exists", {"not_exists": ["$step.report.json"]}, True),

    # ── json path 取值 ──
    ("json 简单字段", {"eq": ["$step.fetch.json.total_volume", 1234567.89]}, True),
    ("json 数组+字段", {"eq": ["$step.fetch.json.top[0].sym", "BTC"]}, True),
    ("json 数值比较", {"gt": ["$step.fetch.json.top[0].price", 60000]}, True),
    ("json 不存在 → not_exists 通过",
     {"not_exists": ["$step.fetch.json.missing_field"]}, True),

    # ── env / cfg / time ──
    ("env eq", {"eq": ["$env.DRY_RUN", "1"]}, True),
    ("env exists", {"exists": ["$env.REGION"]}, True),
    ("env not_exists 不存在 var", {"not_exists": ["$env.NEVER_SET_XX"]}, True),
    ("cfg eq", {"eq": ["$cfg.web_ui.port", 18790]}, True),
    ("cfg gt", {"gt": ["$cfg.agent.max_iterations", 100]}, True),
    ("time.now > 2026 时间戳", {"gt": ["$time.now", 1700000000]}, True),

    # ── 逻辑组合 ──
    ("list AND 全过",
     [{"eq": ["$step.fetch.status", "success"]},
      {"contains": ["$step.fetch.output", "BTC"]}], True),
    ("list AND 一个 false → false",
     [{"eq": ["$step.fetch.status", "success"]},
      {"eq": ["$step.fetch.tool_count", 99]}], False),
    ("any OR 一个过即可",
     {"any": [{"eq": ["$step.fetch.status", "FAIL"]},
              {"gt": ["$step.fetch.tool_count", 2]}]}, True),
    ("any OR 全失败",
     {"any": [{"eq": ["$step.fetch.status", "FAIL"]},
              {"gt": ["$step.fetch.tool_count", 1000]}]}, False),
    ("all 显式 AND",
     {"all": [{"eq": ["$step.fetch.status", "success"]},
              {"eq": ["$env.DRY_RUN", "1"]}]}, True),
    ("not 取反",
     {"not": {"eq": ["$step.fetch.status", "failure"]}}, True),
    ("嵌套: not(any(a,b))",
     {"not": {"any": [{"eq": ["$step.fetch.status", "X"]},
                      {"eq": ["$env.DRY_RUN", "0"]}]}}, True),
    ("嵌套: any(all(a,b), c)",
     {"any": [
        {"all": [{"eq": ["$step.fetch.status", "success"]},
                 {"contains": ["$step.fetch.output", "DOT"]}]},
        {"eq": ["$env.UNKNOWN", "x"]},
     ]}, True),

    # ── 错误兜底 ──
    ("未知 step → False", "success:never_exist", False),
    ("非法 token → False", {"eq": ["$bad.x", "y"]}, False),
    ("非法运算符 dict 多 key", {"eq": [1, 1], "ne": [1, 2]}, False),
]


def main():
    passed, failed = 0, 0
    for desc, when, want in cases:
        got, reason = evaluate_when(when, _CTX)
        ok = got == want
        if ok:
            passed += 1
            print(f"  ✅ {desc}")
        else:
            failed += 1
            print(f"  ❌ {desc}: want={want} got={got} ({reason})")
            print(f"     when = {when!r}")

    total = passed + failed
    print(f"\n{'=' * 50}")
    print(f"  PASSED: {passed} / {total}")
    if failed:
        print(f"  FAILED: {failed}")
    else:
        print(f"  🎉 ALL GREEN")
    print("=" * 50)
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
