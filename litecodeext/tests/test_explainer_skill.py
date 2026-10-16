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
        for f in ("tts.py", "render.py", "grid.py", "shot.py", "bgm.py", "merge.py"):
            subprocess.run([sys.executable, "-m", "py_compile", os.path.join(SK, "scripts", f)], check=True)

    def test_natural_requests_load_skill(self):
        from lib import skills as S
        S._build_skill_index()
        for msg in ("帮我做个抖音科普视频, 讲讲巴菲特的故事", "像天才交易员那样做一个讲乔布斯的"):
            self.assertIn("douyin-explainer-video", S.auto_load_skills(msg), msg)
        self.assertNotIn("douyin-explainer-video", S.auto_load_skills("帮我写个python爬虫抓取网页"))


if __name__ == "__main__":
    unittest.main()
