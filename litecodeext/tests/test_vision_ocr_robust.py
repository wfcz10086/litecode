"""vision_ocr 两个生产问题 (2026-10-19 视频成片终检):
1. 思考把 max_tokens 吃光 (finish=length, 正文空) → 关思考重问一次, 不返回"空内容"
2. 竖长拼图 (930x3360) 按长边 1280 缩会缩到 352 宽看不清字 → 改按总像素限
"""
import asyncio
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from PIL import Image  # noqa: E402

import core.tools.vision as V  # noqa: E402


class _Resp:
    def __init__(self, data):
        self.status_code, self._d, self.text = 200, data, ""

    def json(self):
        return self._d


def _fake_client(answers, seen):
    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            import copy
            seen.append(copy.deepcopy(json))
            return _Resp(answers.pop(0))
    return C


def _ans(content, finish="stop", reasoning=""):
    return {"choices": [{"finish_reason": finish,
                         "message": {"content": content, "reasoning": reasoning}}]}


def _img(tmp_path, w, h, big=False):
    p = tmp_path / "g.png"
    im = Image.effect_noise((w, h), 80).convert("RGB") if big else Image.new("RGB", (w, h), "white")
    im.save(p)
    return p


def test_thinking_exhausted_retries_without_thinking(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(V.httpx, "AsyncClient", _fake_client(
        [_ans("", "length", "想了很多" * 100), _ans("行1 正常")], seen))
    out = asyncio.run(V._do_vision_ocr(str(_img(tmp_path, 64, 64)), "看图"))
    assert out == "行1 正常"
    assert len(seen) == 2 and seen[1]["chat_template_kwargs"] == {"enable_thinking": False}


def test_reasoning_field_fallback(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(V.httpx, "AsyncClient", _fake_client([_ans("", "stop", "结论: 无重叠")], seen))
    out = asyncio.run(V._do_vision_ocr(str(_img(tmp_path, 64, 64)), "看图"))
    assert out == "结论: 无重叠" and len(seen) == 1


def test_tall_grid_keeps_resolution(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(V.httpx, "AsyncClient", _fake_client([_ans("ok")], seen))
    p = _img(tmp_path, 930, 3360, big=True)
    assert p.stat().st_size > 2 * 1024 * 1024
    asyncio.run(V._do_vision_ocr(str(p), "看图"))
    import base64
    url = seen[0]["messages"][0]["content"][0]["image_url"]["url"]
    w, _ = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size
    assert w >= 700, w          # 原来按长边 1280 缩只剩 354 宽
