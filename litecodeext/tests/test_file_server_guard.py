"""2026-10-21: 模型自己起 python -m http.server 8891 (绑 0.0.0.0, 无认证) 把测试报告挂到公网。
对外文件服务一律拒绝, 只绑本机的调试服务放行; 拒绝规则前台和 background 都要生效。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from core.executor_v4 import public_file_server_violation, shell_policy_violation  # noqa: E402


def test_public_file_servers_blocked():
    for c in ("python3 -m http.server 8891 --bind 0.0.0.0",
              "cd out && python3 -m http.server 8891",
              "nohup python -m SimpleHTTPServer 8000 &",
              "npx -y serve out -l 9000",
              "http-server ./out -p 8080",
              "php -S 0.0.0.0:8000"):
        assert public_file_server_violation(c), c


def test_loopback_and_unrelated_allowed():
    for c in ("python3 -m http.server 8891 --bind 127.0.0.1",
              "python3 -m http.server -b localhost 9000",
              "http-server ./out -a 127.0.0.1",
              "php -S localhost:8000",
              "uvicorn app:app --port 8000",
              "grep -r http.server docs/",
              "pip install http-server-utils",
              "python3 econ2027/serve.py"):
        assert not public_file_server_violation(c), c


def test_policy_message_points_to_absolute_paths():
    msg = shell_policy_violation("python3 -m http.server 8891") or ""
    assert msg.startswith("ERROR[PERMANENT]") and "绝对路径" in msg
    assert (shell_policy_violation("cat .secrets/x") or "").startswith("ERROR[PERMANENT]")
    assert shell_policy_violation("ls -la") is None


def test_background_path_is_checked(monkeypatch):
    import core.tool_dispatch as TD
    called = []

    async def fake_bg(*a, **k):
        called.append(a)
        return "started"
    monkeypatch.setattr(TD, "_exec_bg", fake_bg)
    for c in ("python3 -m http.server 8891", "cat ~/.litecode/keys/x"):
        out, _ = asyncio.run(TD._execute_tool_impl("execute_shell", {"command": c, "background": True}, sid="t"))
        assert out.startswith("ERROR[PERMANENT]"), c
    assert not called                                       # 原来 background 直接绕过拒绝规则
    out, _ = asyncio.run(TD._execute_tool_impl(
        "execute_shell", {"command": "python3 -m http.server 9 --bind 127.0.0.1", "background": True}, sid="t"))
    assert called and out == "started"
