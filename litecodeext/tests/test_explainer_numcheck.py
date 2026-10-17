"""douyin-explainer-video: 画面数字 ↔ 旁白数字 对账 (scripts/numcheck.py, factcheck / run.py fact 步骤调用)。
来历: 样片画面大字「428 秒」, 同场旁白「要走约八分二十秒」(=500 秒, 真实约 499 秒), 事实闸没拦住。"""
import copy
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SK = os.path.join(ROOT, "skills", "douyin-explainer-video")
sys.path.insert(0, os.path.join(SK, "scripts"))
N = importlib.import_module("numcheck")
FC = importlib.import_module("factcheck")

# 样片《光一秒能绕地球几圈》(deepsea) 的 script.json, 只留对账用到的字段
LIGHT = {
    "title": "光一秒能绕地球几圈",
    "facts": [{"fact": "真空中光速约每秒三十万公里, 约等于绕地球赤道七圈半", "source": "国际单位制 c=299792458 m/s"},
              {"fact": "太阳光到达地球约需八分二十秒 (约 499 秒)", "source": "NASA"},
              {"fact": "常温空气中声速约每秒三百四十米", "source": "物理教材"}],
    "scenes": [
        {"id": "s0", "act": "光速", "type": "hook",
         "params": {"number": 30, "suffix": "万公里/秒", "title": "一秒绕地七圈半", "tape": "SPEED OF LIGHT"},
         "text": "光一秒钟能跑约三十万公里，足够绕地球七圈半。"},
        {"id": "s1", "act": "日光", "type": "counter",
         "params": {"label": "阳光到达地球", "from": 0, "to": 499, "suffix": " 秒", "note": "约八分二十秒"},
         "text": "阳光从太阳出发，要走约八分二十秒，才照到你脸上。"},
        {"id": "s2", "act": "声速", "type": "versus",
         "params": {"left": {"name": "光", "value": "约30万公里/秒"}, "right": {"name": "声音", "value": "约340米/秒"}},
         "text": "然而声音慢得多，每秒只走约三百四十米，所以先看见闪电，再听到雷声。"},
        {"id": "s3", "act": "星光", "type": "quote",
         "params": {"quote": "你看到的星光，早已在路上走了很久。", "endcard": "关注我 · 下期讲黑洞"},
         "text": "你今晚看到的星光，可能很久以前就出发了。"},
    ]}


def one(text):
    qs = N.parse(text)
    assert len(qs) == 1, (text, [(q.dim, q.value, q.text) for q in qs])
    return qs[0]


class TestNumberParsing(unittest.TestCase):
    def test_chinese_numbers(self):
        cases = {"八": 8, "二十": 20, "十四": 14, "三百四十": 340, "一万二千": 12000, "一万二": 12000, "三千五": 3500,
                 "两": 2, "零点五": .5, "半": .5, "一九二九": 1929, "三点五亿": 3.5e8, "一亿二千万": 1.2e8, "两百五": 250}
        for s, v in cases.items():
            self.assertAlmostEqual(N.cn_number(s), v, msg=s)

    def test_durations_to_seconds(self):
        cases = {"八分二十秒": 500, "要走约八分二十秒": 500, "1 小时 20 分": 4800, "1小时20分钟30秒": 4830, "两分半": 150,
                 "一个半小时": 5400, "半小时": 1800, "428 秒": 428, "3天": 3 * 86400, "两分半钟": 150}
        for s, v in cases.items():
            q = one(s)
            self.assertEqual(q.dim, "time", s)
            self.assertAlmostEqual(q.value, v, msg=s)
        self.assertEqual(one("约八分二十秒").mode, "approx")

    def test_ambiguous_fen_is_not_minutes(self):
        for s in ("这件事十分重要", "三分之一的人", "考了99分", "小时候", "九十年代"):
            self.assertFalse([q for q in N.parse(s) if q.dim == "time"], s)

    def test_units_and_magnitudes(self):
        cases = [("1,299 元", "money:CNY", 1299), ("一千三百块买了手机", "money:CNY", 1300), ("两千块钱", "money:CNY", 2000),
                 ("+$3M", "money:USD", 3e6), ("$100,000,000", "money:USD", 1e8), ("3.5亿美元", "money:USD", 3.5e8),
                 ("30%", "percent", 30), ("百分之三十", "percent", 30), ("12.5%", "percent", 12.5),
                 ("三百四十米", "length", 340), ("5千米", "length", 5000), ("2.5公里", "length", 2500),
                 ("约30万公里/秒", "speed", 3e8), ("340m/s", "speed", 340), ("每秒只走约三百四十米", "speed", 340),
                 ("光一秒钟能跑约三十万公里", "speed", 3e8), ("时速 360 公里", "speed", 100),
                 ("一九二九年", "year", 1929), ("1929年", "year", 1929), ("BLACK THURSDAY · 1929.10.24", "year", 1929),
                 ("第二幕 · 1907", "year", 1907), ("十四岁", "age", 14), ("七圈半", "laps", 7.5), ("十一年后", "time", 11 * 365.25 * 86400)]
        for s, dim, v in cases:
            qs = [q for q in N.parse(s) if q.dim == dim]
            self.assertTrue(qs, (s, [(q.dim, q.value) for q in N.parse(s)]))
            self.assertAlmostEqual(qs[-1].value / v, 1, places=6, msg=s)
        self.assertFalse([q for q in N.parse("一块泥巴改变了印刷术") if q.dim.startswith("money")])
        self.assertFalse(N.parse("第五次破产"))

    def test_ranges(self):
        q = one("三十多万公里")
        self.assertEqual(q.range(), (3e8, 4e8))
        lo, hi = one("不到两小时").range()
        self.assertEqual(hi, 7200); self.assertLess(lo, 7200)
        lo, hi = one("约八分二十秒").range()
        self.assertTrue(lo <= 499 <= hi and not lo <= 428 <= hi)

    def test_render_num_matches_engine(self):
        self.assertEqual(N.render_num(100000000, {"prefix": "$"}), "$100,000,000")
        self.assertEqual(N.render_num(428, {"suffix": " 秒"}), "428 秒")
        self.assertEqual(N.render_num(-2.5, {"suffix": "%"}), "-3%")


class TestScreenVsNarration(unittest.TestCase):
    def test_sample_428_vs_eight_min_twenty_is_caught(self):
        sj = copy.deepcopy(LIGHT)
        sj["scenes"][1]["params"]["to"] = 428                       # 画面 428 秒, 旁白 约八分二十秒
        errs, _ = N.check(sj)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("s1", errs[0]); self.assertIn("428", errs[0]); self.assertIn("500 秒", errs[0]); self.assertIn("时长矛盾", errs[0])

    def test_sample_with_true_value_passes(self):
        self.assertEqual(N.check(LIGHT), ([], []))

    def test_big_number_hook_scene(self):
        sj = copy.deepcopy(LIGHT)
        sj["scenes"][1] = {"id": "s1", "type": "hook", "params": {"number": 428, "suffix": " 秒", "title": "阳光到地球"},
                           "text": "阳光从太阳出发，要走约八分二十秒，才照到你脸上。"}
        self.assertEqual(len(N.check(sj)[0]), 1)
        sj["scenes"][1]["params"]["suffix"] = ""                    # 没单位的大数字也拦
        errs = N.check(sj)[0]
        self.assertEqual(len(errs), 1); self.assertIn("大数字 428", errs[0])
        sj["scenes"][1]["params"]["number"] = 500
        self.assertEqual(N.check(sj)[0], [])

    def test_versus_other_side_supported_elsewhere(self):
        # s2 旁白只念了声速, 左卡光速在 s0 讲过且 facts 有 → 不算矛盾; 右卡改成 34 米/秒 → 矛盾
        sj = copy.deepcopy(LIGHT)
        sj["scenes"][2]["params"]["right"]["value"] = "约34米/秒"
        errs = N.check(sj)[0]
        self.assertEqual(len(errs), 1); self.assertIn("right.value", errs[0]); self.assertIn("速度矛盾", errs[0])

    def test_money_and_percent(self):
        base = {"facts": [{"fact": "售价 1299 元"}], "scenes": [
            {"id": "a", "type": "versus", "params": {"left": {"name": "旧款", "value": "1299 元"}, "right": {"name": "降幅", "value": "-30%"}},
             "text": "旧款卖一千三百块，现在直接降了百分之三十。"}]}
        self.assertEqual(N.check(base)[0], [])
        bad = copy.deepcopy(base)
        bad["scenes"][0]["text"] = "旧款卖九百九十九块，现在直接降了百分之二十。"
        errs = N.check(bad)[0]
        self.assertEqual(len(errs), 2, errs)                        # 1299 元 有 facts 出处但……
        self.assertTrue(any("百分比矛盾" in e for e in errs))

    def test_year_on_screen(self):
        sj = {"facts": [{"year": 1907, "fact": "恐慌"}], "scenes": [
            {"id": "s", "act": "第二幕 · 1908", "type": "headline", "params": {"title": "x"}, "text": "一九零七年，他重仓做空。"}]}
        errs = N.check(sj)[0]
        self.assertEqual(len(errs), 1); self.assertIn("年份矛盾", errs[0])

    def test_unsourced_screen_number_only_warns(self):
        sj = {"facts": [], "scenes": [{"id": "s", "type": "counter", "params": {"label": "粉丝", "to": 120000, "suffix": " 人"},
                                       "text": "他从零开始做账号。"}]}
        errs, warns = N.check(sj)
        self.assertEqual(errs, []); self.assertEqual(len(warns), 1); self.assertIn("120,000", warns[0])

    def test_numcheck_ignore(self):
        sj = copy.deepcopy(LIGHT)
        sj["scenes"][1]["params"]["to"] = 428
        sj["scenes"][1]["numcheck_ignore"] = ["428"]
        self.assertEqual(N.check(sj)[0], [])

    def test_custom_layout_params_scanned(self):
        sj = copy.deepcopy(LIGHT)
        sj["scenes"][1]["type"] = "sunbeam"                          # custom.js 版式: 扫字符串 + number/value 等键
        sj["scenes"][1]["params"] = {"caption": "阳光走了 428 秒", "color": "#ffcc00", "x": 300}
        self.assertEqual(len(N.check(sj)[0]), 1)
        sj["scenes"][1]["params"] = {"value": 428, "suffix": "秒", "top": 500}
        self.assertEqual(len(N.check(sj)[0]), 1)

    def test_examples_pass(self):
        sj = json.load(open(os.path.join(SK, "examples", "livermore", "script.json"), encoding="utf-8"))
        self.assertEqual(N.check(sj), ([], []))


class TestWiring(unittest.TestCase):
    def test_factcheck_includes_numcheck(self):
        sj = copy.deepcopy(LIGHT)
        self.assertEqual(FC.check(sj), [])
        sj["scenes"][1]["params"]["to"] = 428
        errs = FC.check(sj)
        self.assertEqual(len(errs), 1); self.assertIn("428", errs[0])

    def test_cli_exit_codes_and_pipeline(self):
        sj = copy.deepcopy(LIGHT)
        sj["scenes"][1]["params"]["to"] = 428
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "script.json")
            json.dump(sj, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            for script in ("numcheck.py", "factcheck.py"):
                r = subprocess.run([sys.executable, os.path.join(SK, "scripts", script), p], capture_output=True, text=True)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr); self.assertIn("428", r.stdout)
            # 一键流水线第一步 fact 就停 (在项目目录里跑, 不会走到 tts)
            r = subprocess.run([sys.executable, os.path.join(SK, "scripts", "run.py"), "--only", "fact"], cwd=d, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0); self.assertIn("停在 [fact]", r.stdout)
            sj["scenes"][1]["params"]["to"] = 499
            json.dump(sj, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            r = subprocess.run([sys.executable, os.path.join(SK, "scripts", "run.py"), "--only", "fact"], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
