"""text_to_speech: 音色映射 / 参数换算 / 长文本切分 / 产物落在会话 artifacts 目录 (合成 mock) + 一次真实小合成 (网络不通 skip)。"""
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import pytest  # noqa: E402

from core import tts  # noqa: E402


@pytest.mark.parametrize("voice,want", [
    ("晓晓", "zh-CN-XiaoxiaoNeural"), ("云希", "zh-CN-YunxiNeural"), ("云扬", "zh-CN-YunyangNeural"),
    ("晓伊", "zh-CN-XiaoyiNeural"), ("云健", "zh-CN-YunjianNeural"), ("HiuGaai", "zh-HK-HiuGaaiNeural"),
    ("粤语", "zh-HK-HiuGaaiNeural"), ("HsiaoChen", "zh-TW-HsiaoChenNeural"), ("jenny", "en-US-JennyNeural"),
    ("Guy", "en-US-GuyNeural"), ("zh-CN-YunxiaNeural", "zh-CN-YunxiaNeural"), ("", "zh-CN-XiaoxiaoNeural"),
    (None, "zh-CN-XiaoxiaoNeural"),
])
def test_voice_names_map_to_edge_ids(voice, want):
    eng, vid, note = tts.resolve_voice(voice, "auto", has_ds_key=False)
    assert (eng, vid, note) == ("edge", want, "")


def test_unavailable_and_unknown_voices_fall_back_with_note():
    eng, vid, note = tts.resolve_voice("晓萱", has_ds_key=False)
    assert vid == "zh-CN-XiaoxiaoNeural" and "晓萱" in note
    eng, vid, note = tts.resolve_voice("不存在的人", has_ds_key=False)
    assert vid == "zh-CN-XiaoxiaoNeural" and "voices" in note


def test_dashscope_voices_need_key():
    assert tts.resolve_voice("芊悦", has_ds_key=True)[:2] == ("dashscope", "Cherry")
    assert tts.resolve_voice("Cherry", "auto", has_ds_key=True)[:2] == ("dashscope", "Cherry")
    assert tts.resolve_voice("北京-晓东", has_ds_key=True)[:2] == ("dashscope", "Dylan")
    eng, vid, note = tts.resolve_voice("Cherry", has_ds_key=False)
    assert eng == "edge" and "key" in note
    eng, vid, note = tts.resolve_voice("晓晓", "dashscope", has_ds_key=False)
    assert eng == "edge" and vid == "zh-CN-XiaoxiaoNeural" and "key" in note
    assert tts.resolve_voice("晓晓", "dashscope", has_ds_key=True)[:2] == ("dashscope", "Cherry")


def test_prosody_normalization():
    assert tts.prosody() == {"rate": "+0%", "volume": "+0%", "pitch": "+0Hz"}
    assert tts.prosody("+20%", "-3Hz", "10") == {"rate": "+20%", "volume": "+10%", "pitch": "-3Hz"}
    assert tts.prosody("1.2")["rate"] == "+20%"          # 倍速写法
    assert tts.prosody("0.8")["rate"] == "-20%"
    assert tts.prosody("-500%")["rate"] == "-90%"        # 夹到合法范围
    assert tts.prosody("快一点")["rate"] == "+0%"        # 看不懂就不调


def test_split_long_text_into_bounded_sentence_chunks():
    text = "第一句话。" * 120 + "这是一个没有标点的超长句子" * 40
    chunks = tts.split_text(text, limit=300)
    assert len(chunks) >= 3
    assert all(0 < len(c) <= 300 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")   # 不丢字
    assert chunks[0].endswith("。")                                     # 按句边界合并
    assert tts.split_text("  。。！！  ") == []
    assert tts.split_text("Hello world. Second one!") == ["Hello world. Second one!"]


def _fake_edge(calls):
    async def fake(text, voice, pros, out):
        calls.append((text, voice, pros))
        # 生成 0.3 秒静音 mp3, 让真实 ffmpeg 拼接/转码路径也被覆盖
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "anullsrc=r=24000:cl=mono", "-t", "0.3", "-c:a", "libmp3lame", str(out)], check=True)
    return fake


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_handler_writes_artifact_into_session_dir(tmp_path, monkeypatch):
    import lib.config as cfg
    from tools.handlers import REGISTRY
    import tool_dispatch
    monkeypatch.setattr(cfg, "SESSIONS_DISK", tmp_path)
    monkeypatch.setattr(tts, "dashscope_key", lambda: "")
    calls, arts = [], []
    monkeypatch.setattr(tts, "_edge_one", _fake_edge(calls))
    monkeypatch.setattr(tool_dispatch, "_add_artifact", lambda sid, rec: arts.append((sid, rec)))
    text = "你好，欢迎使用 LiteCode。" + "这是第二段很长的文字。" * 40
    out, _ = asyncio.run(REGISTRY["text_to_speech"]("web-tts1", {"text": text, "voice": "云希", "rate": "+10%"}))
    assert out.startswith("[text_to_speech OK]"), out
    path = Path(out.splitlines()[0].split("] ", 1)[1])
    assert path.parent == tmp_path / "web-tts1" / "artifacts" and path.suffix == ".mp3" and path.stat().st_size > 0
    assert len(calls) >= 2 and {c[1] for c in calls} == {"zh-CN-YunxiNeural"} and calls[0][2]["rate"] == "+10%"
    assert "时长" in out and str(path) in out
    assert arts and arts[0][0] == "web-tts1" and arts[0][1]["filepath"] == str(path)
    assert tts.duration(path) >= 0.3 * len(calls) - 0.2


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_wav_and_m4a_formats(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "dashscope_key", lambda: "")
    monkeypatch.setattr(tts, "_edge_one", _fake_edge([]))
    for fmt in ("wav", "m4a"):
        r = asyncio.run(tts.synthesize("测试一下。", tmp_path, fmt=fmt, stem=f"x_{fmt}"))
        assert r["path"].endswith(f".{fmt}") and Path(r["path"]).stat().st_size > 0


def test_bad_inputs_are_errors_not_crashes(tmp_path, monkeypatch):
    from tools.handlers import REGISTRY
    import lib.config as cfg
    monkeypatch.setattr(cfg, "SESSIONS_DISK", tmp_path)
    h = REGISTRY["text_to_speech"]
    assert asyncio.run(h("s", {"text": ""}))[0].startswith("ERROR")
    assert asyncio.run(h("s", {"text": "x" * (tts.MAX_TEXT_CHARS + 1)}))[0].startswith("ERROR")
    assert asyncio.run(h("s", {"text": "你好", "format": "flac"}))[0].startswith("ERROR")
    listing = asyncio.run(h("s", {"action": "voices"}))[0]
    assert "晓晓" in listing and "zh-CN-XiaoxiaoNeural" in listing and "Cherry" in listing


def _edge_reachable() -> bool:
    try:
        import edge_tts  # noqa: F401
        import socket
        socket.create_connection(("speech.platform.bing.com", 443), timeout=5).close()
        return bool(shutil.which("ffmpeg"))
    except Exception:
        return False


@pytest.mark.skipif(not _edge_reachable(), reason="edge-tts 服务不可达 (离线环境)")
def test_real_edge_tts_small_synthesis(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "dashscope_key", lambda: "")
    try:
        r = asyncio.run(tts.synthesize("你好，测试。", tmp_path, voice="晓晓", stem="real"))
    except RuntimeError as e:   # 微软服务偶发限流/断连: 不让外部服务抖动卡发布门禁
        pytest.skip(f"edge-tts 暂不可用: {e}")
    assert r["engine"] == "edge" and r["voice"] == "zh-CN-XiaoxiaoNeural"
    assert Path(r["path"]).stat().st_size > 2000 and r["duration"] > 0.5
