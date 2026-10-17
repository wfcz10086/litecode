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


def scene(params, text="", typ="counter", **kw):
    return {"facts": [], "scenes": [dict({"id": "x", "type": typ, "params": params, "text": text}, **kw)]}


class TestRelativeQuantities(unittest.TestCase):
    """旁白的相对 / 口语量词: 三成 / 翻一番 / 一半 / 打八折 …"""

    def test_parse_relative(self):
        cases = [("三成的人", "percent", 30), ("七成五", "percent", 75), ("九成九", "percent", 99), ("两成", "percent", 20),
                 ("百分之三十", "percent", 30), ("一半的人", "percent", 50), ("半数", "percent", 50),
                 ("翻了一番", "times", 2), ("翻一番", "times", 2), ("翻两番", "times", 4), ("翻了三番", "times", 8),
                 ("翻番", "times", 2), ("翻倍", "times", 2), ("增长一倍", "times", 2), ("增加了一倍", "times", 2),
                 ("打八折", "percent", 80), ("八五折", "percent", 85), ("8.5折", "percent", 85), ("打对折", "percent", 50)]
        for s, dim, v in cases:
            q = one(s)
            self.assertEqual((q.dim, q.value), (dim, v), s)

    def test_relative_ranges(self):
        self.assertEqual(one("五成以上").range(), (50, 100))         # 以上 = 下限
        self.assertEqual(one("过半").range(), (50, 100))
        self.assertEqual(one("超过一半").range(), (50, 100))
        self.assertEqual(one("七八成").range(), (70, 80))
        lo, hi = one("几乎翻倍").range()
        self.assertEqual(hi, 2); self.assertLess(lo, 2)
        lo, hi = one("不到三成").range()
        self.assertEqual(hi, 30); self.assertLess(lo, 30)
        self.assertEqual(one("增长了两倍").range(), (2, 3))          # 口语歧义: 2~3 倍都算
        q = one("打八折")
        self.assertEqual(q.alts, [20])                               # 也可读成 "降 20%"

    def test_ambiguous_not_relative(self):
        for s in ("这件事十分重要", "他八成是忘了", "一成不变", "你的另一半", "一波三折", "把纸对折一下",
                  "公司成员", "第三成", "七成员工"):
            self.assertFalse([q for q in N.parse(s) if q.dim in ("percent", "times")], s)

    def test_relative_vs_screen(self):
        ok = [({"left": {"name": "原价", "value": "原价"}, "right": {"name": "降幅", "value": "-30%"}}, "这一波直接降了三成。"),
              ({"left": {"name": "十年前", "value": "基准"}, "right": {"name": "现在", "value": "4倍"}}, "十年里产量翻了两番。"),
              ({"left": {"name": "去年", "value": "基准"}, "right": {"name": "今年", "value": "2 倍"}}, "今年销量增长一倍。"),
              ({"left": {"name": "A", "value": "62%"}, "right": {"name": "B", "value": "其他"}}, "五成以上的人选了 A。"),
              ({"left": {"name": "原价", "value": "原价"}, "right": {"name": "降价", "value": "-20%"}}, "双十一打八折。"),
              ({"left": {"name": "现价", "value": "八折"}, "right": {"name": "B", "value": "其他"}}, "现在降了百分之二十。"),
              ({"left": {"name": "过半", "value": "56%"}, "right": {"name": "B", "value": "其他"}}, "过半的网友支持。"),
              ({"left": {"name": "A", "value": "1.9倍"}, "right": {"name": "B", "value": "其他"}}, "几乎翻倍。")]
        for params, text in ok:
            self.assertEqual(N.check(scene(params, text, "versus"))[0], [], text)
        bad = [({"left": {"name": "原价", "value": "原价"}, "right": {"name": "降幅", "value": "-30%"}}, "这一波直接降了两成。", "百分比矛盾"),
               ({"left": {"name": "十年前", "value": "基准"}, "right": {"name": "现在", "value": "2倍"}}, "十年里产量翻了两番。", "倍数矛盾"),
               ({"left": {"name": "原价", "value": "原价"}, "right": {"name": "降价", "value": "-30%"}}, "双十一打八折。", "百分比矛盾"),
               ({"left": {"name": "A", "value": "40%"}, "right": {"name": "B", "value": "其他"}}, "过半的网友支持。", "百分比矛盾"),
               ({"left": {"name": "A", "value": "3倍"}, "right": {"name": "B", "value": "其他"}}, "几乎翻倍。", "倍数矛盾")]
        for params, text, kind in bad:
            errs = N.check(scene(params, text, "versus"))[0]
            self.assertEqual(len(errs), 1, (text, errs)); self.assertIn(kind, errs[0])

    def test_counter_big_number_vs_relative(self):
        self.assertEqual(N.check(scene({"label": "支持率", "to": 30, "suffix": "%"}, "大约三成的人支持。"))[0], [])
        errs = N.check(scene({"label": "支持率", "to": 30, "suffix": "%"}, "七成的人支持。"))[0]
        self.assertEqual(len(errs), 1); self.assertIn("百分比矛盾", errs[0])
        self.assertEqual(N.check(scene({"label": "产量", "to": 4, "suffix": " 倍"}, "十年翻了两番。"))[0], [])


class TestScreenVsScreen(unittest.TestCase):
    """同一场的大数字 (counter.to / hook.number) ↔ 同场 note / caption / 副标题里写出的同量纲数字"""

    def test_counter_to_vs_note(self):
        errs = N.check(scene({"label": "阳光到达地球", "to": 428, "suffix": " 秒", "note": "约 500 秒"}, "阳光要走很久才到。"))[0]
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("counter.to", errs[0]); self.assertIn("counter.note", errs[0]); self.assertIn("自相时长矛盾", errs[0])
        errs = N.check(scene({"label": "阳光到达地球", "to": 428, "suffix": " 秒", "note": "约八分二十秒"}, "阳光要走很久。"))[0]
        self.assertEqual(len(errs), 1, errs)
        self.assertEqual(N.check(scene({"label": "阳光", "to": 499, "suffix": " 秒", "note": "约八分二十秒"}, "很久。"))[0], [])

    def test_hint_points_at_unsourced_side(self):
        sj = scene({"label": "阳光", "to": 499, "suffix": " 秒", "note": "约 600 秒"}, "阳光要走很久。")
        sj["facts"] = [{"fact": "日光到地球约 499 秒", "source": "NASA"}]
        errs = N.check(sj)[0]
        self.assertEqual(len(errs), 1, errs); self.assertIn("改文字", errs[0])
        sj["scenes"][0]["params"].update(to=600, note="约 499 秒")
        errs = N.check(sj)[0]
        self.assertEqual(len(errs), 1, errs); self.assertIn("改大数字", errs[0])
        # 旁白念对了, note 写错: 旁白闸报 note, 互相对账不重复报
        errs = N.check(scene({"label": "阳光", "to": 499, "suffix": " 秒", "note": "约 600 秒"}, "阳光要走四百九十九秒。"))[0]
        self.assertEqual(len(errs), 1, errs); self.assertIn("counter.note", errs[0])
        errs = N.check(scene({"label": "阳光", "to": 428, "suffix": " 秒", "note": "约 500 秒"}, "阳光要走约五百秒。"))[0]
        self.assertEqual(len(errs), 1, errs)                         # 旁白已经报了 counter.to, 不重复报
        self.assertIn("本场旁白", errs[0])

    def test_hook_number_vs_title_relative(self):
        errs = N.check(scene({"number": 2, "suffix": "倍", "title": "十年翻两番"}, "", "hook"))[0]
        self.assertEqual(len(errs), 1, errs); self.assertIn("倍数矛盾", errs[0]); self.assertIn("hook.title", errs[0])
        self.assertEqual(N.check(scene({"number": 4, "suffix": "倍", "title": "十年翻两番"}, "", "hook"))[0], [])
        self.assertEqual(N.check(scene({"to": 30, "suffix": "%", "note": "三成用户"}, ""))[0], [])
        self.assertEqual(len(N.check(scene({"to": 30, "suffix": "%", "note": "七成用户"}, ""))[0]), 1)

    def test_no_false_positive(self):
        cases = [{"to": 428, "suffix": " 秒", "note": "比去年多 120 秒"},          # 差值不对账
                 {"to": 30, "suffix": "%", "note": "降 20%"},
                 {"to": 428, "note": "约 500 秒"},                                  # 大数字没单位 → 量纲不明不判
                 {"to": 428, "suffix": " 秒", "note": "约 500 米"},                 # 不同量纲
                 {"to": 7.5, "suffix": " 圈", "note": "七圈半"},                     # 画面四舍五入成 8 圈
                 {"from": 600, "to": 0, "suffix": " 秒", "note": "约 500 秒"},     # from 不是终值
                 {"to": 1929, "suffix": "", "label": "1929 年"}]
        for p in cases:
            self.assertEqual(N.check(scene(p, ""))[0], [], p)

    def test_numcheck_ignore_applies(self):
        p = {"to": 428, "suffix": " 秒", "note": "约 500 秒"}
        self.assertEqual(N.check(scene(p, "", numcheck_ignore=["约 500 秒"]))[0], [])
        self.assertEqual(N.check(scene(p, "", numcheck_ignore=["428"]))[0], [])
        self.assertEqual(N.check(scene({"left": {"name": "A", "value": "-30%"}, "right": {"name": "B", "value": "x"}},
                                       "打八折。", "versus", numcheck_ignore=["-30%"]))[0], [])

    def test_custom_layout_top_level_only(self):
        self.assertEqual(len(N.check(scene({"value": 428, "suffix": " 秒", "caption": "约 500 秒"}, "", "sunbeam"))[0]), 1)
        self.assertEqual(N.check(scene({"value": 428, "suffix": " 秒", "items": [{"caption": "约 500 秒"}]}, "", "sunbeam"))[0], [])

    def test_other_samples_still_clean(self):
        # 样片 b_newspaper / c_sakura (没有数字的版式) + deepsea + livermore: 新规则下仍零误报
        news = {"facts": [], "scenes": [
            {"id": "s0", "type": "hook", "params": {"title": "泥巴印书", "tape": "活字印刷 · 梦溪笔谈"}, "text": "一小团泥巴，曾经让印书变得又快又省。"},
            {"id": "s1", "type": "headline", "params": {"kicker": "北宋", "title": "平民工匠毕昇", "lines": ["胶泥刻字", "火烧变硬", "按需排版"]},
             "text": "北宋时，平民工匠毕昇用胶泥刻字，再用火烧硬，这就是泥活字。"},
            {"id": "s2", "type": "stamps", "params": {"title": "活字三步", "stamps": ["刻字", "排版", "拆版"]},
             "text": "要印什么，就把字排在铁板上，印完拆下来，还能反复使用。"}]}
        cat = {"facts": [], "scenes": [
            {"id": "s0", "type": "hook", "params": {"title": "猫咪踩奶之谜", "tape": "踩奶警报 · 踩奶警报"}, "text": "你家猫是不是总在你身上踩来踩去，还一脸陶醉？"},
            {"id": "s1", "type": "versus", "params": {"left": {"name": "踩奶", "value": "放松 信任"}, "right": {"name": "哈气", "value": "别惹我"}},
             "text": "长大了还踩，说明它觉得老安全了，把你当亲妈了。"}]}
        livermore = json.load(open(os.path.join(SK, "examples", "livermore", "script.json"), encoding="utf-8"))
        for sj in (news, cat, LIGHT, livermore):
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
