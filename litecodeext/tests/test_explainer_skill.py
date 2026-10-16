"""skills/douyin-explainer-video: 脚本校验 / 样例与版式对得上 / 技能能被自然说法触发。
完整出片 (tts → render → bgm → merge) 依赖 edge-tts 联网 + chromium, 在容器里手动跑, 见 SKILL.md。"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import types
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SK = os.path.join(ROOT, "skills", "douyin-explainer-video")
sys.path[:0] = [ROOT, os.path.join(ROOT, "core")]


def load_tts():
    sys.modules.setdefault("edge_tts", types.ModuleType("edge_tts"))   # 只测校验, 不联网
    spec = importlib.util.spec_from_file_location("tts", os.path.join(SK, "scripts", "tts.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


class TestExplainerSkill(unittest.TestCase):
    def test_validate_rejects_english_and_dup_ids(self):
        v = load_tts().validate
        ok = {"scenes": [{"id": "s0", "type": "hook", "text": "一九二九年，他一天赚走一亿美元。"}]}
        v(ok)
        with self.assertRaises(AssertionError):
            v({"scenes": [{"id": "s0", "type": "hook", "text": "他 repeatedly 破产"}]})
        with self.assertRaises(AssertionError):
            v({"scenes": ok["scenes"] * 2})
        with self.assertRaises(AssertionError):
            v({"scenes": [{"id": "s0", "text": "缺 type"}]})

    def test_example_uses_only_known_layouts(self):
        eng = open(os.path.join(SK, "templates", "engine.js"), encoding="utf-8").read()
        block = eng[eng.index("const LAYOUTS"):eng.index("window.LAYOUTS_EXT || {}")]
        known = set(re.findall(r"^  (\w+): \{", block, re.M))
        self.assertGreaterEqual(known, {"hook", "headline", "stamps", "counter", "versus", "quote", "image"})
        sj = json.load(open(os.path.join(SK, "examples", "livermore", "script.json"), encoding="utf-8"))
        load_tts().validate(sj)
        self.assertTrue({s["type"] for s in sj["scenes"]} <= known)

    def test_engine_js_parses(self):
        r = subprocess.run(["node", "--check", os.path.join(SK, "templates", "engine.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_scripts_compile(self):
        for f in ("tts.py", "render.py", "grid.py", "shot.py", "bgm.py", "merge.py", "factcheck.py", "qa_final.py", "run.py"):
            subprocess.run([sys.executable, "-m", "py_compile", os.path.join(SK, "scripts", f)], check=True)

    def _mod(self, name):
        sys.path.insert(0, os.path.join(SK, "scripts"))
        return importlib.import_module(name)

    def test_factcheck_catches_relative_year_slip(self):
        fc = self._mod("factcheck")
        sj = {"facts": [{"year": 1929, "fact": "股灾期间做空净赚约一亿美元"}, {"year": 1940, "fact": "去世"}],
              "scenes": [{"id": "s0", "text": "一九二九年，他赚走了约一亿美元。十四年后，他死在纽约一家酒店。"}]}
        errs = fc.check(sj)
        self.assertEqual(len(errs), 1); self.assertIn("1943", errs[0]); self.assertIn("11 年后", errs[0])
        sj["scenes"][0]["text"] = sj["scenes"][0]["text"].replace("十四年后", "十一年后")
        self.assertEqual(fc.check(sj), [])
        sj["scenes"][0]["text"] += "一九三四年他第五次破产，负债五百万美元。"
        self.assertEqual(len(fc.check(sj)), 3)                     # 年份 / 次数 / 金额 各一条
        self.assertTrue(fc.check({"scenes": sj["scenes"]}))        # 没有 facts 直接不过
        self.assertEqual([fc.cn_int(x) for x in ("一九零七", "十一", "三百万", "3.5亿")], [1907, 11, 3e6, 3.5e8])

    def test_layout_issue_rules(self):
        iss = self._mod("render")._issues
        box = lambda t, x, y, w, h, **k: {"text": t, "x": x, "y": y, "r": x + w, "b": y + h, "overlay": False, "sub": False, **k}
        self.assertTrue(iss([box("破产·1908", 430, 760, 300, 120), box("破产·1911", 610, 740, 300, 120)]))   # 印章叠印章
        self.assertFalse(iss([box("一亿", 60, 235, 256, 156), box("整个人生", 60, 362, 512, 156)]))       # 行距细缝不算
        self.assertFalse(iss([box("战胜", 0, 0, 600, 300, rel=[1]), box("自己", 0, 150, 600, 150)]))     # 父子文字不算
        self.assertFalse(iss([box("$", 600, 3000, 80, 80, overlay=True)]))                               # 故意叠放 / 掉出屏外
        self.assertTrue(iss([box("标题", 900, 100, 400, 90)]))                                           # 出屏
        self.assertTrue(iss([box("关注", 100, 100, 500, 90, spill=True)]))                               # 撑破框

    def test_pipeline_order_has_gates_first(self):
        steps = self._mod("run").NAMES
        self.assertEqual(steps[:3], ["fact", "tts", "check"])
        self.assertLess(steps.index("check"), steps.index("render"))
        self.assertEqual(steps[-1], "qa")

    def test_example_passes_factcheck(self):
        fc = self._mod("factcheck")
        sj = json.load(open(os.path.join(SK, "examples", "livermore", "script.json"), encoding="utf-8"))
        self.assertEqual(fc.check(sj), [])

    def test_natural_requests_load_skill(self):
        from lib import skills as S
        S._build_skill_index()
        for msg in ("帮我做个抖音科普视频, 讲讲巴菲特的故事", "像天才交易员那样做一个讲乔布斯的"):
            self.assertIn("douyin-explainer-video", S.auto_load_skills(msg), msg)
        self.assertNotIn("douyin-explainer-video", S.auto_load_skills("帮我写个python爬虫抓取网页"))


if __name__ == "__main__":
    unittest.main()
