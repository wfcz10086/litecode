"""skills/douyin-toolkit + video-studio + bgm-music: 离线单测 (链接解析 / 作品字段归一 / 字幕切分对时 / 配乐时长)。
真实下载、ASR、edge-tts、成片渲染依赖外网与 ffmpeg, 在容器里手动跑, 见各 SKILL.md。"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SKILLS = os.path.join(ROOT, "skills")
sys.path[:0] = [ROOT, os.path.join(ROOT, "core")]


def load(skill, mod, name=None):
    path = os.path.join(SKILLS, skill, "scripts", f"{mod}.py")
    d = os.path.dirname(path)
    if d not in sys.path:
        sys.path.insert(0, d)
    sys.modules.pop(mod, None)
    spec = importlib.util.spec_from_file_location(name or f"_t_{skill}_{mod}", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _item(**kw):
    it = {"aweme_id": "7697569156489359217", "desc": "#伦敦大本钟", "author": {"nickname": "奶奶"},
          "create_time": 1791380966, "statistics": {"digg_count": 5},
          "video": {"duration": 16903,
                    "bit_rate": [{"is_h265": 0, "bit_rate": 1, "gear_name": "480",
                                  "play_addr": {"width": 480, "height": 852, "url_list": ["https://a/480"]}},
                                 {"is_h265": 1, "bit_rate": 2, "gear_name": "720",
                                  "play_addr": {"width": 720, "height": 1280, "url_list": ["https://a/720"]}}],
                    "play_addr": {"uri": "v0d00abc", "url_list": ["https://a/play"]},
                    "download_addr": {"url_list": ["https://a/playwm"]}},
          "video_control": {"allow_download": True, "download_info": {"level": 0}}}
    it.update(kw)
    return it


class TestDouyin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dy = load("douyin-toolkit", "douyin")

    def test_extract_url_from_share_text(self):
        txt = "7.69 复制打开抖音，看看【某某的作品】# 测试 https://v.douyin.com/dAvdKyZ6EbA/ g@b.Nj 06/11 BTL:/"
        self.assertEqual(self.dy.extract_url(txt), "https://v.douyin.com/dAvdKyZ6EbA/")
        self.assertEqual(self.dy.extract_url("7693922664478623178"), "7693922664478623178")
        with self.assertRaises(self.dy.DouyinError):
            self.dy.extract_url("没有链接")

    def test_resolve_id_without_network_for_long_links(self):
        self.assertEqual(self.dy.resolve_id("https://www.douyin.com/video/7693922664478623178?x=1"),
                         "7693922664478623178")
        self.assertEqual(self.dy.resolve_id("https://www.douyin.com/jingxuan?modal_id=7693922664478623178"),
                         "7693922664478623178")

    def test_normalize_picks_highest_res_and_skips_watermark(self):
        n = self.dy.normalize(_item())
        self.assertEqual(n["type"], "video")
        self.assertEqual(n["video_urls"][0], "https://a/720")
        self.assertNotIn("https://a/playwm", n["video_urls"])
        self.assertTrue(n["allow_download"])
        self.assertEqual(n["duration_s"], 16.9)

    def test_normalize_respects_download_block(self):
        n = self.dy.normalize(_item(video_control={"allow_download": False, "download_info": {
            "fail_info": {"msg": "作品含AI生成内容暂不可下载", "reason": "aigc_forbidden"}}}))
        self.assertFalse(n["allow_download"])
        self.assertTrue(n["aigc"])
        self.assertIn("AI", n["download_block_reason"])

    def test_normalize_image_post(self):
        n = self.dy.normalize(_item(images=[{"url_list": ["https://p/1.webp", "https://p/1.jpeg"], "width": 1}]))
        self.assertEqual(n["type"], "images")
        self.assertEqual(n["images"][0]["urls"][0], "https://p/1.jpeg")


class TestDewatermark(unittest.TestCase):
    def test_box_and_filter(self):
        dw = load("douyin-toolkit", "dewatermark")
        self.assertEqual(dw.parse_box("10,20,30,40@1-2.5"), (10, 20, 30, 40, 1.0, 2.5))
        f = dw.build_filter([(0, 0, 50, 50), (700, 10, 100, 40, 0, 3)], 720, 1280)
        self.assertIn("delogo=x=1:y=1", f)                 # 贴边自动内缩
        self.assertIn("w=19", f)                           # 越界裁到画面内
        self.assertIn("between(t,0,3)", f)
        tl, br = dw.douyin_preset(1080, 1920, 10)
        self.assertEqual(tl[4:], (0, 3.0))
        self.assertEqual(br[4:], (3.0, 10))


class TestTimeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load("video-studio", "tts", "tts")
        cls.tl = load("video-studio", "timeline")
        cls.tts = sys.modules["tts"]

    def test_split_caption(self):
        parts = self.tl.split_caption("你想要的全在路上，别太慌张。不管天晴还是下雨，心有阳光就好！", 12)
        self.assertTrue(all(len(p) <= 12 for p in parts))
        self.assertEqual("".join(parts).replace(" ", ""), "你想要的全在路上别太慌张。不管天晴还是下雨心有阳光就好")

    def test_build_cues_keeps_sentence_bounds(self):
        cues = self.tl.build_cues([{"text": "心里带着阳光，走到哪里都发光。", "start": 0.2, "end": 2.97}], 10.0, 6)
        self.assertAlmostEqual(cues[0]["start"], 10.2)
        self.assertAlmostEqual(cues[-1]["end"], 12.97)
        self.assertTrue(cues[0]["sent_start"] and cues[-1]["sent_end"])
        for a, b in zip(cues, cues[1:]):
            self.assertAlmostEqual(a["end"], b["start"])

    def test_sentence_split_and_srt(self):
        s = self.tts.split_sentences("第一句。第二句！\n第三句没有标点")
        self.assertEqual(s, ["第一句。", "第二句！", "第三句没有标点"])
        srt = self.tts.to_srt([{"text": "a", "start": 0.2, "end": 61.005}])
        self.assertIn("00:00:00,200 --> 00:01:01,005", srt)


class TestBgm(unittest.TestCase):
    def test_render_length_and_loop(self):
        try:
            import numpy  # noqa
        except ImportError:
            self.skipTest("numpy 未安装")
        bg = load("bgm-music", "bgm_gen")
        for mood in bg.MOODS:
            audio, bar_n = bg.render(mood, 0 or bg.MOODS[mood]["bpm"], bg.MOODS[mood]["key"], 2, 1)
            self.assertEqual(len(audio), bar_n * 3)
        audio, bar_n = bg.render("upbeat", 120, "C", 2, 3)
        out = bg.finalize(audio, bar_n, 2, 3.3, False, 0.5, 1.0)
        self.assertEqual(len(out), int(round(3.3 * bg.SR)))
        self.assertLessEqual(float(abs(out).max()), 0.9)
        looped = bg.finalize(audio, bar_n, 2, 0, True, 0, 0)
        self.assertEqual(len(looped), bar_n * 2)


if __name__ == "__main__":
    unittest.main()
