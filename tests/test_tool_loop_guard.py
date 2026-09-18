#!/usr/bin/env python3
# S1-D 冒烟: ToolLoopGuard 抽出后行为不变.
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))
from plugins.tools.guard import ToolLoopGuard, compute_sig, EXEMPT_FROM_LOOP_GUARD  # noqa: E402


def test_sig_url_normalize():
    a = compute_sig("web_fetch", {"url": "https://x.com/a"})
    b = compute_sig("web_fetch", {"url": "https://x.com/a"})
    c = compute_sig("web_fetch", {"url": "https://x.com/b"})
    assert a == b and a != c
    print("[OK] sig folds identical URLs")


def test_sig_shell_digit_norm():
    a = compute_sig("execute_shell", {"command": "sed -n '218,225p' file.py"})
    b = compute_sig("execute_shell", {"command": "sed -n '219,226p' file.py"})
    assert a == b, "line-number wiggle should collapse via <N> normalization"
    print("[OK] shell sig normalizes digit-wiggle attacks")


def test_third_call_blocked():
    g = ToolLoopGuard()
    args = {"url": "https://x.com/dead"}
    b1, _, _ = g.check("web_fetch", args); assert not b1
    b2, _, _ = g.check("web_fetch", args); assert not b2
    b3, w3, _ = g.check("web_fetch", args); assert b3 and "SYSTEM-LOOP-GUARD" in w3
    print("[OK] 3rd identical call blocked with detailed warn")


def test_fourth_call_gets_short_warn():
    g = ToolLoopGuard()
    args = {"query": "abc"}
    for _ in range(2):
        g.check("web_search", args)
    b3, w3, _ = g.check("web_search", args)
    b4, w4, _ = g.check("web_search", args)
    assert b3 and b4
    assert "二选一" in w3, "first block should have long explanation"
    assert "已被永久封禁" in w4, "second+ block should have short brutal warn"
    print("[OK] subsequent blocks get shorter permanent-ban warn")


def test_write_file_exempt():
    g = ToolLoopGuard()
    args = {"filepath": "foo.py", "content": "x"}
    for _ in range(5):
        b, _, _ = g.check("write_file", args)
        assert not b, "write_file should never be blocked"
    print("[OK] write_file / patch_file / create_file / spawn_agent exempt")
    for fn in EXEMPT_FROM_LOOP_GUARD:
        b, _, _ = g.check(fn, {"x": 1}); assert not b
    print("[OK] full exempt frozenset honored")


def test_different_args_dont_trigger():
    g = ToolLoopGuard()
    for i in range(6):
        b, _, _ = g.check("web_fetch", {"url": f"https://x.com/{i}"})
        assert not b, f"different URLs should not accumulate on iteration {i}"
    print("[OK] different args stay independent")


if __name__ == "__main__":
    test_sig_url_normalize()
    test_sig_shell_digit_norm()
    test_third_call_blocked()
    test_fourth_call_gets_short_warn()
    test_write_file_exempt()
    test_different_args_dont_trigger()
    print("\n[PASS] S1-D tool_loop_guard smoke")
