"""douyin-explainer-video 2026-10-19 优化: 文案闸 (风格/深度/废话/不外包下期) + 逐句韵律 + 精确字幕时间。
来历: 「2027 经济预测」成片用户评"没啥深度, 声音没感觉"。全部离线。"""
import importlib
import json
import os
import re
import sys
import types

ROOT = os.path.join(os.path.dirname(__file__), "..")
SK = os.path.join(ROOT, "skills", "douyin-explainer-video")
sys.path.insert(0, os.path.join(SK, "scripts"))
sys.modules.setdefault("edge_tts", types.ModuleType("edge_tts"))

S = importlib.import_module("stylecheck")
V = importlib.import_module("voices")
T = importlib.import_module("tts")

# 原片 (念表版) 的关键几场
TABLE = {"style": "chaizhang", "scenes": [
    {"id": "s0", "text": "2027年，世界经济会变成什么样？全球增长回升到百分之三点四，听起来是个好数字。"},
    {"id": "s1", "text": "先说总盘子。去年全球增长百分之三点五，今年只有百分之三点一，明年回升到三点四。"},
    {"id": "s2", "text": "高盛把中国明年增速上调到百分之四点七，印度已经用4.18万亿美元超过日本。"},
    {"id": "s3", "text": "美国今年明年都只有百分之二点三。欧元区今年一点一，明年才爬到一点二。"},
    {"id": "s6", "text": "关注我，下期拆这份报告里，跟你钱包最近的三条。"}]}


def _copy_example():
    """copywriting.md 里的拆账示例 → script"""
    md = open(os.path.join(SK, "copywriting.md"), encoding="utf-8").read()
    rows = re.findall(r"^> (s\d) (.+)$", md, re.M)
    assert len(rows) == 7
    return {"style": "chaizhang", "scenes": [{"id": i, "text": t} for i, t in rows]}


def test_table_reading_script_fails_depth_and_defer():
    errs, tips = S.check(TABLE)
    assert any("深度不够" in e for e in errs)
    assert any("下期" in e for e in errs)
    assert any(t.startswith("s1") for t in tips)            # 一场三个数没因果 → 提示


def test_style_required_and_fluff_blocked():
    errs, _ = S.check({"scenes": [{"id": "s0", "text": "因为油价涨了，所以让我们拭目以待。"}]})
    assert any("style" in e for e in errs)
    assert any("拭目以待" in e for e in errs)


def test_rewritten_example_and_story_example_pass():
    assert S.check(_copy_example())[0] == []
    lv = json.load(open(os.path.join(SK, "examples", "livermore", "script.json"), encoding="utf-8"))
    assert lv["style"] == "beiju" and S.check(lv)[0] == []   # 故事类不查因果密度


def test_sentence_prosody():
    ss = V.sentences("今年为什么最慢？因为油。霍尔木兹海峡一出事，今年油价均价涨了百分之四十一。全球增长放缓。",
                     {"gold": [], "red": ["放缓"]})
    assert [s["text"] for s in ss][:2] == ["今年为什么最慢？", "因为油。"]
    q, short, num, last = ss
    assert q["dp"] > 0 and q["pause"] >= .35                # 问句上扬 + 停顿
    assert short["dr"] < 0                                  # 短句放慢加重
    assert num["dr"] < 0                                    # 带数字放慢
    assert last["dr"] < 0 and last["pause"] == 0            # 高亮词放慢; 最后一句不留尾巴
    flat = V.sentences("你好？我很好。", auto=False)
    assert {(s["dr"], s["dp"], s["dv"]) for s in flat} == {(0, 0, 0)}


def test_default_rate_slower():
    assert V._num(V.DEFAULT_RATE, "%") <= 8
    assert T.DEFAULTS["rate"] == V.DEFAULT_RATE


def test_subs_follow_real_sentence_timing():
    sents = [{"text": "今年为什么最慢？"}, {"text": "因为油，油价涨了。"}]
    subs = T.subs_for(sents, [(0.0, 2.0), (2.6, 5.0)], 5.0)
    assert [x["p"] for x in subs] == ["今年为什么最慢？", "因为油，", "油价涨了。"]
    assert subs[0]["t1"] == 0.4 and subs[1]["t0"] == 0.52   # 停顿 2.0-2.6s 不显示下一句
    assert subs[-1]["t1"] == 1.0
    assert all(a["t1"] <= b["t0"] + 1e-9 for a, b in zip(subs, subs[1:]))


def test_engine_prefers_subs():
    js = open(os.path.join(SK, "templates", "engine.js"), encoding="utf-8").read()
    assert "s.subs" in js


def test_numcheck_custom_layout_honors_decimals():
    """gauge {to: 4.7, decimals: 1} 画面是 4.7%, 不能取整成 5% 再跟旁白"百分之四点七"判矛盾"""
    N = importlib.import_module("numcheck")
    sc = {"id": "s2", "type": "gauge", "text": "全球通胀被推到百分之四点七。",
          "params": {"label": "全球通胀", "to": 4.7, "decimals": 1, "suffix": "%"}}
    shown = [v for k, v, isnum in N.screen_fields(sc) if isnum]
    assert "4.7%" in shown
    assert N.render_num(4.7, {"suffix": "%"}) == "5%"          # 内置版式 (engine num()) 仍取整
