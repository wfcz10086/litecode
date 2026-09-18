"""[SYSTEM-TEST] 源文件测试检测 — 回归用例

2026-08-31: 检测逻辑只认 test_ 前缀 / .test. 中缀, 不认 Go 原生的 {name}_test.go 后缀。
实测后果 (易经 Go 复刻, web-c48a8d03): agent 写了 data_test.go / iching_test.go /
scraper_test.go / client_test.go 共 40 个测试函数并全部通过, 检测器一个都认不出,
从 iter 3 误报到 iter 79, 每轮往上下文注入一条内容错误的警告。
"""
import os, sys, tempfile, unittest
from pathlib import Path


def has_test(sf: str, project_root: str, tested_mods=frozenset()) -> bool:
    """复刻 litecode_server.py 修复后的 _has_test 判定 (含跳过规则)。"""
    name = sf.split("/")[-1].rsplit(".", 1)[0]
    ext  = "." + sf.rsplit(".", 1)[-1] if "." in sf else ".py"
    d    = sf.rsplit("/", 1)[0]
    if (name.endswith("_test") or name.startswith("test_") or ".test" in name
            or ".spec" in name or name.endswith("Test")):
        return True                      # 本身是测试, 不算待测
    if name.split(".")[0].lower().startswith(("probe", "tmp", "scratch", "_")):
        return True                      # 一次性脚本, 不要求测试
    return (
        name in tested_mods
        or os.path.exists(f"{d}/{name}_test{ext}")
        or os.path.exists(f"{d}/test_{name}{ext}")
        or os.path.exists(f"{d}/{name}.test{ext}")
        or os.path.exists(f"{d}/{name}.spec{ext}")
        or os.path.exists(f"{d}/{name.capitalize()}Test.java")
        or os.path.exists(f"{project_root}/tests/test_{name}{ext}")
        or os.path.exists(f"{project_root}/tests/{name}_test{ext}")
    )


class TestUntestedDetect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        (Path(self.root) / "internal" / "hexagram").mkdir(parents=True)
        (Path(self.root) / "tests").mkdir()
    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, rel):
        p = Path(self.root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
        return str(p)

    # ── 回归: Go 后缀式约定 (本次 bug 的核心) ──
    def test_go_suffix_convention_recognized(self):
        src = self._touch("internal/hexagram/data.go")
        self.assertFalse(has_test(src, self.root), "写测试前应判定为无测试")
        self._touch("internal/hexagram/data_test.go")
        self.assertTrue(has_test(src, self.root),
                        "data_test.go 存在时必须认出 —— 这正是误报 79 轮的原因")

    def test_go_test_file_itself_not_flagged(self):
        t = self._touch("internal/hexagram/data_test.go")
        self.assertTrue(has_test(t, self.root), "测试文件自身不该被要求再写测试")

    # ── 其它语言的原生约定 ──
    def test_python_prefix(self):
        src = self._touch("pkg/util.py")
        self.assertFalse(has_test(src, self.root))
        self._touch("tests/test_util.py")
        self.assertTrue(has_test(src, self.root))

    def test_python_suffix(self):
        src = self._touch("pkg/calc.py")
        self._touch("pkg/calc_test.py")
        self.assertTrue(has_test(src, self.root))

    def test_js_dot_test(self):
        src = self._touch("web/app.js")
        self.assertFalse(has_test(src, self.root))
        self._touch("web/app.test.js")
        self.assertTrue(has_test(src, self.root))

    def test_ts_dot_spec(self):
        src = self._touch("web/store.ts")
        self._touch("web/store.spec.ts")
        self.assertTrue(has_test(src, self.root))

    # ── 一次性调研脚本不该计入 ──
    def test_probe_scripts_excluded(self):
        for n in ("tools/probe.py", "tools/probe2.py", "tools/probe3.py",
                  "tmp_check.py", "scratch.go", "_gen.py"):
            self.assertTrue(has_test(self._touch(n), self.root),
                            f"{n} 是一次性脚本, 不该要求测试")

    # ── 真实场景: 今天那个 Go 项目应判定为「全部已测」 ──
    def test_real_yijing_project_all_covered(self):
        pairs = [("internal/hexagram/data.go",  "internal/hexagram/data_test.go"),
                 ("internal/iching/iching.go",  "internal/iching/iching_test.go"),
                 ("internal/scraper/scraper.go","internal/scraper/scraper_test.go"),
                 ("internal/ai/client.go",      "internal/ai/client_test.go")]
        srcs = [self._touch(s) for s, _ in pairs]
        for _, t in pairs: self._touch(t)
        untested = [s for s in srcs if not has_test(s, self.root)]
        self.assertEqual(untested, [],
                         f"修复后不该再有误报, 实际仍报: {untested}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
