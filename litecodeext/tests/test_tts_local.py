"""TTS 可插拔引擎链: 本地纯 CPU 引擎的路由、语速换算、失败自动换引擎; 有模型时做一次真实合成。"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]
from core import tts  # noqa: E402  (别用扁平 import: 技能测试会把 skills/*/scripts/tts.py 注册成 sys.modules['tts'])
from core import tts_local  # noqa: E402


def test_plan_routes():
    p = lambda v, e="auto", loc=True: tts.plan(v, e, has_ds_key=True, local_ok=loc)  # noqa: E731
    assert p("")[0] == ("local", "3", "")                       # 默认本地
    assert p("晓晓")[0][0] == "local" and p("晓晓")[1][0] == "edge"   # 常用名映射到本地, edge 兜底
    assert p("男声2")[0] == ("local", "74", "")
    assert p("粤语")[0] == ("edge", "zh-HK-HiuGaaiNeural", "")     # 本地没有的走在线
    assert p("四川-晴儿")[0] == ("dashscope", "Sunny", "") and p("四川-晴儿")[-1][0] == "edge"
    assert p("", loc=False)[0][0] == "edge"                      # 没装模型自动跳过
    only = p("", "local", loc=False)
    assert only[0][0] == "edge" and "本地引擎未安装" in only[0][2]
    assert [e for e, _, _ in p("Jenny", "edge")] == ["edge"]
    assert [e for e, _, _ in p("", "edge")] == ["edge", "local"]  # 指定引擎失败也有兜底


def test_engine_order_from_config(monkeypatch):
    import lib.config as cfg
    monkeypatch.setattr(cfg, "load_cfg", lambda: {"tts": {"engines": ["edge", "local", "bogus"]}})
    assert tts.engine_order() == ["edge", "local"]
    assert [e for e, _, _ in tts.plan("", "auto", True, True)] == ["edge", "local"]


def test_speed_and_resolve():
    assert tts_local._speed(None) == 1.0 and tts_local._speed("+20%") == 1.2
    assert tts_local._speed("-50%") == 0.5 and tts_local._speed("1.5") == 1.5 and tts_local._speed("9") == 1.09
    assert tts_local.resolve("女声3") == (42, "") and tts_local.resolve("sid:7") == (7, "")
    assert tts_local.resolve("Jenny")[0] is None


def test_fallback_when_local_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_local_ok", lambda: True)
    monkeypatch.setattr(tts, "dashscope_key", lambda: "")

    async def boom(*a, **k):
        raise RuntimeError("模型坏了")

    async def fake_edge(text, voice, pros, out):
        out.write_bytes(b"ID3fake")

    monkeypatch.setattr(tts, "_local_one", boom)
    monkeypatch.setattr(tts, "_edge_one", fake_edge)
    monkeypatch.setattr(tts, "concat", lambda parts, out, fmt: out.write_bytes(parts[0].read_bytes()))
    monkeypatch.setattr(tts, "duration", lambda p: 1.0)
    r = asyncio.run(tts.synthesize("你好。", tmp_path))
    assert r["engine"] == "edge" and "本地引擎 合成失败" in r["note"]


@pytest.mark.skipif(not tts_local.available(), reason="没装 sherpa-onnx 或本地语音模型")
def test_real_local_synthesis(tmp_path):
    r = asyncio.run(tts.synthesize("你好, 欢迎使用 LiteCode。", tmp_path, engine="local", fmt="wav"))
    assert r["engine"] == "local" and r["voice"] == "女声1" and Path(r["path"]).stat().st_size > 10000
