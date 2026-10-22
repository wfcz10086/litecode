"""ssh_exec sudo (2026-10-21): 主机库已存密码经标准输入交给 sudo, 不拼进命令, 输出里也遮掉。
原来模型自己写 `echo 密码 | sudo -S`, 密码进了命令行、日志和工具历史。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core"), str(ROOT / "lib")]

from core.tools.handlers import remote as R  # noqa: E402


def _run(monkeypatch, args, conn):
    seen = {}

    async def fake_ssh(host, port, user, password, cmd, timeout, key_file="", jump=None, stdin_data=None, **kw):
        seen.update(cmd=cmd, stdin=stdin_data)
        return 0, "ok s3cret-pw done", ""
    monkeypatch.setattr(R, "_ssh", fake_ssh)
    monkeypatch.setattr(R, "_conn", lambda a: (dict(conn), ""))
    out, _ = asyncio.run(R.h_ssh_exec("t", args))
    return out, seen


def test_sudo_password_via_stdin_not_command(monkeypatch):
    conn = {"host": "h", "port": 22, "user": "u", "password": "s3cret-pw", "key_file": "/k", "jump": None}
    out, seen = _run(monkeypatch, {"alias": "x", "cmd": "docker ps -a", "sudo": True}, conn)
    assert seen["cmd"] == "sudo -S -p '' bash -c 'docker ps -a'"
    assert "s3cret-pw" not in seen["cmd"] and seen["stdin"] == b"s3cret-pw\n"
    assert "s3cret-pw" not in out and "***" in out


def test_sudo_requires_saved_password_and_linux(monkeypatch):
    conn = {"host": "h", "port": 22, "user": "u", "password": "", "key_file": "/k", "jump": None}
    out, _ = _run(monkeypatch, {"alias": "x", "cmd": "id", "sudo": True}, conn)
    assert out.startswith("ERROR") and "sudo" in out
    conn["password"] = "p"
    out, _ = _run(monkeypatch, {"alias": "x", "cmd": "id", "sudo": True, "shell": "powershell"}, conn)
    assert out.startswith("ERROR")


def test_no_sudo_unchanged(monkeypatch):
    conn = {"host": "h", "port": 22, "user": "u", "password": "p", "key_file": "", "jump": None}
    _, seen = _run(monkeypatch, {"alias": "x", "cmd": "uptime"}, conn)
    assert seen["cmd"] == "uptime" and seen["stdin"] is None


def test_install_command_bypasses_dead_system_proxy():
    """2026-10-21 测试机残留 Clash 代理 127.0.0.1:7890 (程序没跑), 接入命令只报"无法连接到远程服务器"。
    命令开头先探测系统代理, 连不上就本次直连 (5.1 用 DefaultWebProxy, 6+ 用 -NoProxy 默认值)。"""
    from lib.windows_nodes import install_body
    body = install_body("https://example.com", "CODE")
    i_guard, i_get = body.index("GetSystemWebProxy"), body.index("LcGetBytes ($s+")
    assert i_guard < i_get                                         # 先探测再下载引导脚本
    assert "DefaultWebProxy=New-Object Net.WebProxy" in body and "Invoke-*:NoProxy" in body
    assert ".Wait(2000)" in body                                   # 只在代理真连不上时才直连
