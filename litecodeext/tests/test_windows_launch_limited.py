"""节点代理是管理员时, 给用户打开的 GUI 程序要以普通权限启动 (2026-10-22 真机: VS Code 窗口带 [Administrator])。"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT)]
from lib import windows_desktop_actions as A  # noqa: E402


def test_launch_limited_builds_task_and_script(monkeypatch, tmp_path):
    monkeypatch.setattr(A.tempfile if hasattr(A, "tempfile") else __import__("tempfile"), "gettempdir", lambda: str(tmp_path))
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["script"] = next(tmp_path.glob("LiteCode-Open-*.ps1")).read_text(encoding="utf-8-sig")
        return SimpleNamespace(returncode=0)

    ok = A.launch_limited([r"C:\Program Files\Microsoft VS Code\Code.exe", r"C:\Users\me\Desktop\张三's 项目"],
                          cwd=r"C:\Users\me\Documents", run=fake_run)
    assert ok
    reg = seen["cmd"][-1]
    assert "-RunLevel Limited" in reg and "-LogonType Interactive" in reg and "Unregister-ScheduledTask" in reg
    assert "WindowsIdentity]::GetCurrent().Name" in reg
    sc = seen["script"]
    assert "'C:\\Program Files\\Microsoft VS Code\\Code.exe'" in sc
    assert "'C:\\Users\\me\\Desktop\\张三''s 项目'" in sc            # 单引号转义, 中文路径原样
    assert "WorkingDirectory = 'C:\\Users\\me\\Documents'" in sc and "Start-Process @p" in sc


def test_launch_limited_failure_returns_false(monkeypatch, tmp_path):
    monkeypatch.setattr(__import__("tempfile"), "gettempdir", lambda: str(tmp_path))
    assert A.launch_limited(["notepad.exe"], run=lambda *a, **k: SimpleNamespace(returncode=3)) is False

    def boom(*a, **k):
        raise OSError("no powershell")
    assert A.launch_limited(["notepad.exe"], run=boom) is False


def test_launch_gui_paths(monkeypatch):
    calls = []
    monkeypatch.setattr(A, "detached_popen", lambda c, cwd=None, popen=None: calls.append(("popen", c)))
    monkeypatch.setattr(A, "_elevated", lambda: True)
    monkeypatch.setattr(A, "launch_limited", lambda c, cwd=None: True)
    assert A.launch_gui(["notepad.exe"]) is False and calls == []          # 管理员 → 降权成功, 不直接起
    monkeypatch.setattr(A, "launch_limited", lambda c, cwd=None: False)
    assert A.launch_gui(["notepad.exe"]) is True and calls == [("popen", ["notepad.exe"])]   # 降权失败 → 退回, 标记管理员
    calls.clear()
    monkeypatch.setattr(A, "_elevated", lambda: False)
    assert A.launch_gui(["notepad.exe"]) is False and calls == [("popen", ["notepad.exe"])]  # 普通代理 → 直接起
