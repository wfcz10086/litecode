#!/usr/bin/env python3
# TOOL-LOOP-GUARD 死循环熔断 (2026-08-26 SWE-bench 实测修复):
#   1. 封禁提示展示真实命令 (raw_display), 不是 compute_sig 的 <N> 归一化占位符
#   2. block_count 累计拦截次数, 供上层熔断判断
#   3. compute_sig 的数字归一化判重能力未退化 (sed 微调行号场景)
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))
from plugins.tools.guard import ToolLoopGuard, compute_sig, raw_display  # noqa: E402


def test_warn_shows_real_command_not_placeholder():
    g = ToolLoopGuard()
    cmds = ["head -n 10 astropy/io/ascii/core.py", "head -n 12 astropy/io/ascii/core.py",
            "head -n 15 astropy/io/ascii/core.py", "head -n 18 astropy/io/ascii/core.py"]
    warns = []
    for cmd in cmds:
        blocked, warn, _ = g.check("execute_shell", {"command": cmd})
        if blocked:
            warns.append((cmd, warn))
    assert warns, "digit-wiggle commands should collapse into same sig and get blocked"
    for cmd, warn in warns:
        assert "<N>" not in warn, f"warn leaked normalized placeholder: {warn}"
        # 模型应该能在提示里认出自己刚打的那条真实命令 (至少前缀一致)
        assert "head -n" in warn and "astropy/io/ascii/core.py" in warn
    print("[OK] ban message shows real command, no <N> placeholder leak")


def test_raw_display_matches_actual_args():
    assert raw_display("execute_shell", {"command": "sed -n '218,225p' x.py"}) == "sed -n '218,225p' x.py"
    assert raw_display("web_fetch", {"url": "https://x.com/a"}) == "https://x.com/a"
    print("[OK] raw_display returns unnormalized real args")


def test_block_count_accumulates_to_threshold():
    g = ToolLoopGuard()
    # 模拟 SWE-bench 实测场景: 反复 head -n <不同数字> 同一个文件
    for n in range(1, 20):
        g.check("execute_shell", {"command": f"head -n {n} same_file.py"})
    # 前 2 次不拦, 第 3 次起每次都拦 → 19 次调用里拦截 17 次
    assert g.block_count >= 8, f"block_count should reach break threshold, got {g.block_count}"
    print(f"[OK] block_count={g.block_count} accumulates across whole turn (not reset per-iter)")


def test_block_count_zero_when_no_repeats():
    g = ToolLoopGuard()
    dirs = ["src", "tests", "docs", "scripts", "lib", "bin", "config", "assets", "tools", "vendor"]
    for d in dirs:
        g.check("execute_shell", {"command": f"ls {d}"})
    assert g.block_count == 0, "genuinely distinct commands should never trip block_count"
    print("[OK] block_count stays 0 for genuinely distinct calls")


def test_sig_normalization_not_regressed():
    # 硬约束: 归一化判重能力不能因为这次修复被弄丢
    a = compute_sig("execute_shell", {"command": "sed -n '218,225p' file.py"})
    b = compute_sig("execute_shell", {"command": "sed -n '219,226p' file.py"})
    assert a == b, "digit-wiggle normalization must still collapse to same sig"
    print("[OK] compute_sig digit normalization untouched")


if __name__ == "__main__":
    test_warn_shows_real_command_not_placeholder()
    test_raw_display_matches_actual_args()
    test_block_count_accumulates_to_threshold()
    test_block_count_zero_when_no_repeats()
    test_sig_normalization_not_regressed()
    print("\n[PASS] TOOL-LOOP-GUARD break-threshold suite")
