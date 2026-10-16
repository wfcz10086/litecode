"""plugins/tools/guard.py: 真死循环要拦, 正常读代码 / 改完重跑测试不能误拦。

误拦实例 (2026-10-13 SWE-bench qwen36-v1): 分段读同一文件、sed 读不同区域、
"改 → 跑测试" 循环都被当成重复, 累计 8 次拦截直接终止本轮, 3/20 题因此交白卷。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from plugins.tools.guard import ToolLoopGuard  # noqa: E402

F = "/tmp/litecode_workspace/swe/x/django/db/models/expressions.py"


def shell(cmd):
    return "execute_shell", {"command": cmd}


class TestToolLoopGuard(unittest.TestCase):
    def blocked(self, g, fn, args):
        return g.check(fn, args)[0]

    def test_same_call_blocked_on_third(self):
        g = ToolLoopGuard()
        for _ in range(2):
            self.assertFalse(self.blocked(g, *shell("curl http://a.example/x")))
        self.assertTrue(self.blocked(g, *shell("curl http://a.example/x")))
        self.assertEqual(g.block_count, 1)

    def test_read_file_different_ranges_ok(self):
        g = ToolLoopGuard()
        for rng in ("1:200", "200:400", "1319:1400", "1377:1420"):
            self.assertFalse(self.blocked(g, "read_file", {"filepath": F, "lines": rng}))
        for _ in range(2):
            g.check("read_file", {"filepath": F, "lines": "1:200"})
        self.assertTrue(self.blocked(g, "read_file", {"filepath": F, "lines": "1:200"}))

    def test_sed_tweak_still_blocked_far_regions_ok(self):
        g = ToolLoopGuard()
        self.assertFalse(self.blocked(g, *shell(f"sed -n '218,225p' {F}")))
        self.assertFalse(self.blocked(g, *shell(f"sed -n '219,223p' {F}")))
        self.assertTrue(self.blocked(g, *shell(f"sed -n '220,226p' {F}")))   # 微调行号: 钓鱼游戏案例
        g2 = ToolLoopGuard()
        for r in ("1245,1320", "1340,1385", "1577,1620", "980,1100"):
            self.assertFalse(self.blocked(g2, *shell(f"sed -n '{r}p' {F}")))

    def test_write_resets_history(self):
        g = ToolLoopGuard()
        test = shell("python3 swe_dexec.py swe-gen-x 'pytest tests/test_a.py -x'")
        for _ in range(4):
            self.assertFalse(self.blocked(g, *test))
            self.assertFalse(self.blocked(g, *test))
            g.check("patch_file", {"filepath": F})
        self.assertEqual(g.block_count, 0)

    def test_shell_write_resets_but_same_write_still_blocked(self):
        g = ToolLoopGuard()
        test = shell("python3 swe_dexec.py swe-gen-x 'cd tests && python runtests.py modeladmin'")
        for name in ("alpha", "beta", "gamma", "delta"):
            self.assertFalse(self.blocked(g, *test))
            self.assertFalse(self.blocked(g, *test))
            g.check(*shell(f"sed -i 's/{name}/new/' {F}"))     # 用 sed 改代码, 不走 patch_file
        self.assertEqual(g.block_count, 0)
        g2 = ToolLoopGuard()
        w = shell(f"sed -i 's/a/b/' {F}")
        self.assertFalse(self.blocked(g2, *w))
        self.assertFalse(self.blocked(g2, *w))
        self.assertTrue(self.blocked(g2, *w))                    # 同一条改文件命令连打 3 次照拦

    def test_shell_write_detection(self):
        from plugins.tools.guard import is_shell_write
        for c in ("sed -i 's/a/b/' x.py", "cat > a.py <<EOF", "echo x >> log.txt", "git checkout -- f.py",
                  "python - <<'EOF'\nopen('f.py','w').write(s)\nEOF", "mv a.bak a.py"):
            self.assertTrue(is_shell_write("execute_shell", {"command": c}), c)
        for c in ("grep -n foo x.py", "python runtests.py 2>&1 | tail", "ls > /dev/null", "sed -n '1,20p' x.py"):
            self.assertFalse(is_shell_write("execute_shell", {"command": c}), c)


if __name__ == "__main__":
    unittest.main()
