"""截图失败的会话诊断: 断开 / 锁屏 / 远程桌面最小化 / 控制台, 以及"LogonUI ≠ 本会话锁屏"的提醒。"""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent)]
from lib import windows_system_actions as W  # noqa: E402


def test_hints_by_state():
    h = W.capture_failure_hint({"session": 2, "connect": 4, "protocol": 2, "flags": 1})
    assert "已断开" in h and "tscon 2 /dest:console" in h
    assert "锁屏状态" in W.capture_failure_hint({"session": 1, "connect": 0, "protocol": 0, "flags": 0})
    assert "tscon 2 /dest:console" in W.capture_failure_hint({"session": 2, "connect": 0, "protocol": 2, "flags": 0})
    h = W.capture_failure_hint({"session": 2, "connect": 0, "protocol": 2, "flags": 1})
    assert "被最小化" in h and "远程桌面 · 活动 · 未锁定" in h
    assert "显示器休眠" in W.capture_failure_hint({"session": 1, "connect": 0, "protocol": 0, "flags": 1})
    for h in (W.capture_failure_hint({"session": 2, "connect": 0, "protocol": 2, "flags": 1}),):
        assert "LogonUI 不代表本会话锁屏" in h
    assert W.capture_failure_hint({}) == ""


def test_is_capture_failure():
    assert W.is_capture_failure("Windows graphics function failed (no error provided): BitBlt")
    assert W.is_capture_failure("ScreenShotError: gdi32.GetDIBits() failed")
    assert not W.is_capture_failure("超时")
