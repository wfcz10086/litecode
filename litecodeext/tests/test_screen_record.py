"""录屏模式 (2026-10-21): 自动录屏只录 LiteCode 这个对话窗口, 每轮出 mp4; 手动录屏只录当前标签页, 转 mp4。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core"), str(ROOT / "lib")]

from lib import screen_record as R  # noqa: E402


def test_caption_from_task_exec_frame():
    from routers.chat_router import _caption_of
    line = "data: " + json.dumps({"choices": [{"delta": {"task_exec": {"status": "executing",
                                                                       "detail": "web_search: 北京天气"}}}]},
                                 ensure_ascii=False)
    assert _caption_of(line) == "▶ web_search: 北京天气"
    assert _caption_of("data: not-json") == ""


def test_caption_text_safe_for_cjk_font():
    assert R._esc("⚙ execute_shell:\n ls  -la") == "▶ execute_shell: ls -la"     # Noto CJK 没有 ⚙
    assert len(R._esc("x" * 500)) == 90


def test_recordings_dir_per_session(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "workspace", lambda: tmp_path)
    d = R.rec_dir("web-abc")
    assert d == tmp_path / "recordings" / "web-abc" and d.is_dir()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_transcode_webm_to_mp4(tmp_path):
    src = tmp_path / "user-1.webm"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=321x241:rate=10",
                    "-t", "1", "-c:v", "libvpx", str(src)], check=True)
    mp4 = R.transcode_to_mp4(src)
    assert mp4.suffix == ".mp4" and mp4.stat().st_size > 0
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height",
                          "-of", "csv=p=0", str(mp4)], capture_output=True, text=True).stdout
    assert out.startswith("h264,320,240")          # 奇数尺寸补成偶数, H.264 才能播


def test_producer_hooks_recording_and_appends_video_note():
    src = (ROOT / "routers" / "chat_router.py").read_text(encoding="utf-8")
    assert "_rec.is_on(w.load_session(sid))" in src and "await _rec.begin(sid)" in src
    i_end = src.index("mp4 = await _rec.end(sid)")
    assert i_end < src.index("w._save_assistant_msg, sid, dict(collected)")     # 视频路径进落库的回复
    assert "🎬 本轮录屏：" in src


def test_frontend_records_only_current_tab():
    js = (ROOT / "web_assets" / "screen_record.js").read_text(encoding="utf-8")
    assert "preferCurrentTab: true" in js and "monitorTypeSurfaces: 'exclude'" in js
    assert "surfaceSwitching: 'exclude'" in js
    assert "/api/screenrec/" in js and "/record`" in js
    html = (ROOT / "web_ui.html").read_text(encoding="utf-8")
    assert '/assets/screen_record.js' in html


def test_reimport_keeps_turn_usage(tmp_path):
    """server 历史比缓存新 → 整份重导时, 每轮底部的 token 统计不能丢 (2026-10-21 用户反馈"每轮 token 和耗时没了")。"""
    import web_ui
    old = {"messages": [
        {"role": "user", "content": "第一问"}, {"role": "assistant", "content": "", "tools": ["x"]},
        {"role": "assistant", "content": "答一", "usage": {"total_tokens": 100, "elapsed_seconds": 3.2}},
        {"role": "user", "content": "第二问"},
        {"role": "assistant", "content": "答二", "usage": {"total_tokens": 200}, "desktop_steps": ["<div>s</div>"]}]}
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps(old, ensure_ascii=False))
    new = {"messages": [
        {"role": "user", "content": "第一问"}, {"role": "assistant", "content": "", "_tool_results": []},
        {"role": "assistant", "content": "答一(重导)", "_tool_results": []},
        {"role": "user", "content": "第二问"}, {"role": "assistant", "content": "答二", "_tool_results": []},
        {"role": "user", "content": "第三问"}, {"role": "assistant", "content": "答三", "_tool_results": []}]}
    web_ui._merge_cached_reasoning(cache, new)
    m = new["messages"]
    assert m[2]["usage"]["total_tokens"] == 100 and "usage" not in m[1]      # 挂在该轮最后一条
    assert m[4]["usage"]["total_tokens"] == 200 and m[4]["desktop_steps"] == ["<div>s</div>"]
    assert "usage" not in m[6]                                                # 新一轮不乱挂
