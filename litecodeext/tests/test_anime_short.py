"""skills/anime-short: 纯逻辑 (不联网)。"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "anime-short" / "scripts" / "anime_short.py"
spec = importlib.util.spec_from_file_location("anime_short", SCRIPT)
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


def test_seg_len_matches_h3_frames():
    assert abs(A.SEG - 124 / 24) < 1e-9 and (124 - 5) % 17 == 0


def test_srt_time_and_write(tmp_path):
    assert A.srt_time(3723.456) == "01:02:03,456"
    p = A.write_srt([(0, 1.5, "第一句"), (1.5, 3, "第二句")], tmp_path / "a.srt")
    assert p.read_text(encoding="utf-8").startswith("1\n00:00:00,000 --> 00:00:01,500\n第一句")


def test_find_services_from_cards(monkeypatch, tmp_path):
    cfg = {"providers": [
        {"name": "Qwen-Image 生图 (gpu-a)", "kind": "image", "base_url": "http://h:1", "default": True},
        {"name": "MiniMax H3 视频 (gpu-a)", "kind": "video", "base_url": "http://h:2"},
        {"name": "AniSora 动漫视频 (gpu-a)", "kind": "video", "usage": "curl -s http://h:4/v1/videos -F x=1"},
        {"name": "IndexTTS 配音 (gpu-a)", "kind": "tts", "usage": "curl http://h:3/v1/audio/speech -d '{}'"},
        {"name": "关掉的", "kind": "image", "base_url": "http://x", "enabled": False}]}
    f = tmp_path / "config.json"
    f.write_text(__import__("json").dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(A, "CFG_PATHS", [str(f)])
    s = A.services()
    assert s["image"] == "http://h:1" and s["h3"] == "http://h:2"
    assert s["anisora"] == "http://h:4" and s["tts"] == "http://h:3/v1" and s["asr"] == ""


def test_look_switch():
    assert A.look_of({}) == "real" and A.look_of({"look": "真人"}) == "real" and A.look_of({"look": "动漫"}) == "anime"
    assert "真人实拍" in A.style_of({"look": "real"}) and A.style_of({"look": "anime", "style": "国漫"}) == "国漫"


def test_align_lyrics_uses_original_lines_only_where_sung():
    """2026-10-23 实测: 识别把"挺直腰"听成"停只有"; 前 26 秒是纯伴奏 (主歌没唱), 不能给伴奏配歌词。"""
    lines = ["风把灯吹熄的时候", "路还认得我", "掌心的擦伤开口说", "别怕 再走一遍", "骨头断了会长硬", "汗摔进土里开花",
             "把碎掉的自己拾起来", "挺直腰 往前走", "摔进泥里也抬头看", "天亮之前 山顶等我"]
    heard = ["嗯。", "", "", "", "", "把碎掉的自己拾起来，停只有往前走。", "摔尽迷离，也抬头看天亮之前。", "当我。"]
    out = A.align_lyrics(lines, heard)
    assert out[5] == "把碎掉的自己拾起来\n挺直腰 往前走" and out[6] == "摔进泥里也抬头看\n天亮之前 山顶等我"
    assert not any(out[:5]) and "停只有" not in "".join(out)
    sc = A.song_score(lines, heard)
    assert sc["intro_s"] > 8 and sc["coverage"] < 0.6          # 这首会被质检打回重写


def test_align_lyrics_fills_sung_gaps():
    lines = ["一一一一", "二二二二", "三三三三", "四四四四"]
    heard = ["一一一一", "听不清的唱词", "", "四四四四"]
    out = A.align_lyrics(lines, heard)
    assert out[0] == "一一一一" and out[1] == "二二二二\n三三三三" and out[2] == "" and out[3] == "四四四四"


def test_last_of_modes(tmp_path):
    imgs = [tmp_path / f"{i:02d}.png" for i in (1, 2, 3)]
    for p in imgs:
        p.write_bytes(b"x")
    (tmp_path / "02_last.png").write_bytes(b"x")
    plan = {"shots": [{"mode": "fl2v", "last": "next"}, {"last": "清晨的街道"}, {"mode": "r2v"}]}
    assert A._last_of(plan, 0, imgs) == imgs[1]
    assert A._last_of(plan, 1, imgs) == tmp_path / "02_last.png"     # 只写了描述, 没写 mode → fl2v
    assert A._last_of(plan, 2, imgs) is None


def test_assign_shots_follow_sung_lyrics():
    """镜头按"这段实际唱的是哪句"排: 26 秒纯伴奏前奏给 intro 镜头, 副歌段给演副歌歌词的镜头。"""
    lines = ["风把灯吹熄的时候", "路还认得我", "把碎掉的自己拾起来", "挺直腰 往前走", "摔进泥里也抬头看", "天亮之前 山顶等我"]
    heard = ["嗯。", "", "", "", "", "把碎掉的自己拾起来，停只有往前走。", "摔尽迷离，也抬头看天亮之前。", "当我。"]
    plan = {"shots": [
        {"image": "深夜独坐", "kind": "intro"},
        {"image": "长街独行", "kind": "intro"},
        {"image": "风吹灯灭", "lyric": "风把灯吹熄的时候"},
        {"image": "捡起扳手站直", "lyric": "把碎掉的自己拾起来 挺直腰 往前走"},
        {"image": "雨中抬头", "lyric": "摔进泥里也抬头看 天亮之前 山顶等我"},
        {"image": "山顶日出", "kind": "outro"}]}
    got = A.assign_shots(plan, heard, lines)
    assert got[5] == 3 and got[6] == 4                       # 副歌两段 → 对应歌词的镜头
    assert set(got[:5]) <= {0, 1} and got[0] != got[1]       # 前奏 5 段只用 intro 镜头, 轮换
    assert got[7] == 5                                       # 尾声 ("当我…", 唱了但没对上) → 尾奏镜头


def test_assign_shots_without_lyric_fields_keeps_order():
    plan = {"shots": [{"image": "a"}, {"image": "b"}]}
    assert A.assign_shots(plan, ["", "", ""], []) == [0, 1, 0]


def test_has_cast():
    plan = {"character": "五十岁东北大叔, 短寸头, 黑色墨镜, 红色唐装褂子"}
    assert A.has_cast(plan, {"image": "红衣大叔推着三轮车登场"})
    assert not A.has_cast(plan, {"image": "油条下锅特写, 金黄面团在油锅里翻滚冒泡"})   # 2026-10-23 实测被套了定妆照
    assert not A.has_cast(plan, {"image": "夜空烟花, 城市霓虹"})
    assert not A.has_cast(plan, {"image": "大叔的唢呐特写", "cast": False})


def test_assign_shots_no_adjacent_repeat():
    """2026-10-23 写实版: 结尾 3 段 (16/17/18) 全是同一个镜头。相邻段要换镜头。"""
    lines = ["一一一一", "二二二二", "三三三三"]
    heard = ["一一一一", "二二二二", "三三三三", "三三三三", "嗯嗯嗯嗯", "啦啦啦啦"]
    plan = {"shots": [{"image": "a", "lyric": "一一一一"}, {"image": "b", "lyric": "二二二二"},
                      {"image": "c", "lyric": "三三三三"}, {"image": "d", "kind": "outro"}]}
    got = A.assign_shots(plan, heard, lines)
    assert all(x != y for x, y in zip(got, got[1:])), got
    assert got[:3] == [0, 1, 2]
