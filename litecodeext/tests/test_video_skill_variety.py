"""skills/douyin-explainer-video 多样化: 配色预设 + 对比度校验 / 自动选主题 / 旧颜色写法兼容 /
声音白名单 / 多角色 / 韵律默认 / BGM 参数确定性。全部离线, 不合成整段音频、不起浏览器。"""
import importlib
import os
import re
import subprocess
import sys
import types
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SK = os.path.join(ROOT, "skills", "douyin-explainer-video")
SCRIPTS = os.path.join(SK, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
sys.modules.setdefault("edge_tts", types.ModuleType("edge_tts"))   # tts.py 顶层 import, 测试不联网

TH = importlib.import_module("theme")
V = importlib.import_module("voices")
BGM = importlib.import_module("bgm")


class TestThemes(unittest.TestCase):
    def test_presets_count_and_light(self):
        self.assertGreaterEqual(len(TH.PRESETS), 10)
        light = [k for k, p in TH.PRESETS.items() if p["light"]]
        self.assertGreaterEqual(len(light), 3, light)
        for k, p in TH.PRESETS.items():
            for f in ("bg", "deep", "paper", "accent", "alert", "bg_style", "mood", "name", "suits"):
                self.assertIn(f, p, k)
            self.assertIn(p["bg_style"], TH.BG_STYLES, k)
            self.assertIn(p["mood"], BGM.MOODS, k)
            self.assertEqual(TH.is_light(p["bg"]), p["light"], k)        # light 标记与实际亮度一致

    def test_all_presets_pass_contrast(self):
        for k, p in TH.PRESETS.items():
            t = TH.derive(p)
            self.assertEqual(TH.validate(t), [], k)
            self.assertGreaterEqual(TH.contrast(t["paper"], t["bg"]), 4.5, k)
            self.assertGreaterEqual(TH.contrast(t["ink"], t["bg"]), 4.5, k)
            self.assertGreaterEqual(TH.contrast(t["accent"], t["bg"]), 3.0, k)

    def test_no_gold_default(self):
        html = open(os.path.join(SK, "templates", "video.html"), encoding="utf-8").read()
        self.assertNotIn("f5c451", html.lower())
        md = open(os.path.join(SK, "SKILL.md"), encoding="utf-8").read()
        self.assertRegex(md, r'"theme": "\w+"')                           # 示例用预设名
        self.assertIn("不同视频不要总用同一套", md)

    def test_template_has_no_hardcoded_colors_outside_root(self):
        html = open(os.path.join(SK, "templates", "video.html"), encoding="utf-8").read()
        css = html[html.index("<style>"):html.index("</style>")]
        css = re.sub(r":root\{[^}]*\}", "", css)                          # :root 默认值允许
        css = re.sub(r'url\("data:[^"]*"\)', "", css)                      # 噪点 SVG 砖 (单色遮罩)
        css = re.sub(r"-webkit-mask-image:[^;}]*", "", css)               # 遮罩只用 alpha
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", css), [])
        for style in TH.BG_STYLES:
            if style != "radial":
                self.assertIn(f"[data-bg={style}]", css, style)

    def test_contrast_math(self):
        self.assertAlmostEqual(TH.contrast("#000", "#fff"), 21.0, places=2)
        self.assertAlmostEqual(TH.contrast("#777777", "#ffffff"), 4.48, places=2)
        self.assertEqual(TH.parse_color("rgb(1, 2, 3)"), (1, 2, 3))
        with self.assertRaises(ValueError):
            TH.parse_color("gold")

    def test_validate_reports_low_contrast(self):
        r = TH.resolve({"title": "x", "theme": {"bg": "#ffffff", "paper": "#cccccc", "accent": "#eeeeee", "alert": "#e63946"}})
        msg = "\n".join(r["errors"])
        self.assertIn("正文 paper 对 背景 bg", msg)
        self.assertIn("强调色 accent", msg)
        self.assertRegex(msg, r"\d\.\d\d:1 < 4\.5:1")

    def test_auto_pick_by_keywords_and_stable(self):
        a = TH.auto_pick({"title": "AI 芯片战争"})[0]
        b = TH.auto_pick({"title": "明朝历史上最离奇的皇帝"})[0]
        c = TH.auto_pick({"title": "家常美食红烧肉"})[0]
        self.assertIn(a, ["deepsea", "cyber", "cloud"])
        self.assertIn(b, ["newspaper", "burgundy", "midnight"])
        self.assertIn(c, ["sunset", "mint", "sakura"])
        self.assertEqual(len({a, b, c}), 3)
        for t in ("AI 芯片战争", "随便一个标题"):
            self.assertEqual(TH.auto_pick({"title": t}), TH.auto_pick({"title": t}))
        # 选不出关键词时按标题哈希: 多个标题应分散到多套预设
        picks = {TH.auto_pick({"title": f"第{i}期"})[0] for i in range(30)}
        self.assertGreaterEqual(len(picks), 5)
        # mood 兜底
        self.assertIn(TH.auto_pick({"title": "无关", "mood": "calm"})[0], TH.MOOD_THEMES["calm"])

    def test_resolve_preset_name_and_cn_name(self):
        r = TH.resolve({"title": "x", "theme": "sakura"})
        self.assertEqual((r["name"], r["source"], r["bg_style"], r["errors"]), ("sakura", "preset", "bokeh", []))
        self.assertEqual(r["theme"]["light"], "1")
        self.assertEqual(TH.resolve({"theme": "复古报纸米色"})["name"], "newspaper")
        bad = TH.resolve({"title": "x", "theme": "gold"})
        self.assertTrue(bad["errors"] and "不存在" in bad["errors"][0])
        self.assertEqual(TH.resolve({"theme": "mint", "bg_style": "mesh"})["bg_style"], "mesh")
        self.assertTrue(TH.resolve({"theme": "mint", "bg_style": "lava"})["errors"])

    def test_legacy_color_object_still_works(self):
        import json
        sj = json.load(open(os.path.join(SK, "examples", "livermore", "script.json"), encoding="utf-8"))
        r = TH.resolve(sj)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["source"], "custom")
        t = r["theme"]
        self.assertEqual((t["bg"], t["accent"], t["alert"]), ("#0a1226", "#f5c451", "#e63946"))
        self.assertEqual((t["deep"], t["paper"]), ("#05070f", "#f2e8d5"))   # 与改版前模板默认值一致
        self.assertEqual((r["bg_style"], r["mood"]), ("radial", "dark"))
        self.assertEqual(t["on_accent"], "#000000")                          # 结尾卡仍是黑字
        # 预设 + 微调
        r2 = TH.resolve({"theme": {"preset": "mint", "alert": "#c0392b"}})
        self.assertEqual((r2["name"], r2["theme"]["alert"], r2["theme"]["bg"]), ("mint", "#c0392b", TH.PRESETS["mint"]["bg"]))

    def test_apply_tl_rereads_script_visuals(self):
        tl = {"title": "t", "theme": "mint", "scenes": [], "total": 1}
        out, r = TH.apply_tl(tl, {"title": "t", "theme": "cyber"})
        self.assertEqual((out["theme_name"], out["bg_style"]), ("cyber", "mesh"))
        self.assertIsInstance(out["theme"], dict)
        out2, _ = TH.apply_tl(out)                                           # 解析结果再解析也稳定
        self.assertEqual(out2["theme"]["accent"], out["theme"]["accent"])


class TestVoices(unittest.TestCase):
    def test_catalog(self):
        need = {"zh-CN-YunxiNeural", "zh-CN-YunjianNeural", "zh-CN-YunyangNeural", "zh-CN-YunxiaNeural",
                "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-liaoning-XiaobeiNeural",
                "zh-CN-shaanxi-XiaoniNeural", "zh-HK-HiuGaaiNeural"}
        self.assertLessEqual(need, set(V.VOICES))

    def test_whitelist_alias_and_fallback(self):
        self.assertEqual(V.check_voice("zh-CN-YunjianNeural"), ("zh-CN-YunjianNeural", None))
        self.assertEqual(V.check_voice("晓晓")[0], "zh-CN-XiaoxiaoNeural")
        self.assertEqual(V.check_voice("yunyang")[0], "zh-CN-YunyangNeural")
        self.assertEqual(V.check_voice("东北话")[0], "zh-CN-liaoning-XiaobeiNeural")
        v, tip = V.check_voice("zh-CN-YunjianNeual")                        # 拼错
        self.assertEqual(v, V.DEFAULT_VOICE)
        self.assertIn("zh-CN-YunjianNeural", tip)
        self.assertIn("回退", tip)
        v, tip = V.check_voice("zh-CN-XiaochenNeural")                      # edge-tts 没有的声音
        self.assertEqual(v, V.DEFAULT_VOICE)
        self.assertIn("不在白名单", tip)
        self.assertEqual(V.check_voice(None), (V.DEFAULT_VOICE, None))

    def test_multi_role_resolution(self):
        sj = {"voices": {"narrator": "云健", "quote": "晓晓", "host": "zh-CN-YunyangNeural"},
              "scenes": [{"id": "a", "text": "开场"}, {"id": "b", "text": "他说", "role": "quote"},
                         {"id": "c", "text": "插话", "role": "host"}, {"id": "d", "text": "指定", "voice": "晓伊", "role": "quote"},
                         {"id": "e", "text": "未知角色", "role": "ghost"}, {"id": "f", "text": "结尾"}]}
        plan, tips = V.plan(sj)
        self.assertEqual([p["voice"] for p in plan],
                         ["zh-CN-YunjianNeural", "zh-CN-XiaoxiaoNeural", "zh-CN-YunyangNeural",
                          "zh-CN-XiaoyiNeural", "zh-CN-YunjianNeural", "zh-CN-YunjianNeural"])
        self.assertTrue(any("ghost" in t for t in tips))

    def test_legacy_single_voice(self):
        sj = {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+10%", "prosody": "off",
              "scenes": [{"id": "a", "text": "一"}, {"id": "b", "text": "二", "voice": "zh-CN-YunxiNeural", "rate": "+20%"}]}
        plan, tips = V.plan(sj)
        self.assertEqual(tips, [])
        self.assertEqual([(p["voice"], p["rate"], p["pitch"]) for p in plan],
                         [("zh-CN-XiaoxiaoNeural", "+10%", "+0Hz"), ("zh-CN-YunxiNeural", "+20%", "+0Hz")])

    def test_prosody_defaults(self):
        sj = {"rate": "+14%", "scenes": [{"id": "s0", "type": "hook", "text": "一亿美元"},
                                          {"id": "s1", "type": "headline", "text": "他十四岁离家"},
                                          {"id": "s2", "type": "headline", "text": "然而好景不长"},
                                          {"id": "s3", "type": "counter", "text": "数字", "beat": "twist", "pitch": "+5Hz"},
                                          {"id": "s4", "type": "quote", "text": "金句"}]}
        plan, _ = V.plan(sj)
        self.assertEqual([p["beat"] for p in plan], ["hook", "body", "twist", "twist", "ending"])
        self.assertEqual([p["rate"] for p in plan], ["+20%", "+14%", "+8%", "+8%", "+9%"])
        self.assertEqual([p["pitch"] for p in plan], ["+2Hz", "+0Hz", "-2Hz", "+5Hz", "-1Hz"])   # 场景显式值优先
        plan_off, _ = V.plan({**sj, "prosody": "off"})
        self.assertEqual({p["rate"] for p in plan_off}, {"+14%"})
        self.assertEqual([p["pitch"] for p in plan_off], ["+0Hz", "+0Hz", "+0Hz", "+5Hz", "+0Hz"])
        with self.assertRaises(ValueError):
            V.plan({"scenes": [{"id": "x", "text": "a", "rate": "fast"}]})

    def test_tts_module_uses_plan(self):
        spec = importlib.util.spec_from_file_location("tts_v", os.path.join(SCRIPTS, "tts.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        src = open(os.path.join(SCRIPTS, "tts.py"), encoding="utf-8").read()
        self.assertIn("V.plan(sj)", src)
        self.assertIn("V.sentences(", src)          # 默认逐句合成


class TestBgm(unittest.TestCase):
    def test_moods(self):
        self.assertLessEqual({"dark", "epic", "light", "tech", "warm", "calm", "upbeat"}, set(BGM.MOODS))

    def test_plan_deterministic_and_varied(self):
        a = BGM.plan("天才交易员的陨落", "tech")
        self.assertEqual(a, BGM.plan("天才交易员的陨落", "tech"))
        plans = [BGM.plan(f"标题{i}", "warm") for i in range(12)]
        keys = {(p["transpose"], p["tempo"], tuple(p["arp_pattern"])) for p in plans}
        self.assertGreaterEqual(len(keys), 10)
        self.assertGreaterEqual(len({tuple(p["chord"]) for p in plans}), 3)       # 调性确实在变
        self.assertNotEqual(BGM.plan("甲", "tech")["seed"], BGM.plan("乙", "tech")["seed"])
        for p in plans:
            self.assertTrue(.92 <= p["tempo"] <= 1.08)
            lo, hi = BGM.TRANSPOSE["warm"]
            self.assertTrue(lo <= p["transpose"] <= hi)
        with self.assertRaises(AssertionError):
            BGM.plan("x", "rock")

    def test_short_synth_level(self):
        import numpy as np
        tl = {"title": "测试", "total": 2.0, "scenes": [{"start": 0}, {"start": 1.0}]}
        for mood in BGM.MOODS:
            y, p = BGM.synth(tl, mood)
            self.assertAlmostEqual(float(np.max(np.abs(y))), .18, places=3)   # 峰值统一, merge.py 混音比例不变
            self.assertEqual(len(y), int(BGM.SR * 3.0))


class TestScripts(unittest.TestCase):
    def test_compile_and_engine(self):
        for f in ("theme.py", "voices.py", "tts.py", "render.py", "bgm.py"):
            subprocess.run([sys.executable, "-m", "py_compile", os.path.join(SCRIPTS, f)], check=True)
        r = subprocess.run(["node", "--check", os.path.join(SK, "templates", "engine.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_theme_cli_list_passes(self):
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "theme.py"), "--list"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
