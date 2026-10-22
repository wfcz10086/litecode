"""桌面版"操控本机" (2026-10-22 真机: 在桌面版里让模型往 Windows 桌面建文件, 它吐了一段节点注册命令让用户去运行)。

- 环境检测: LITECODE_DESKTOP 开关; /proc/version 含 microsoft 且能找到 Windows 的 powershell.exe 才算桌面版
- 系统提示: 只在桌面版注入一小段本机说明 (Windows 路径 ↔ /mnt/c 路径、powershell.exe、不要让用户跑注册命令), 进 {{ENV}}
- 本机节点接入: 生成别名 this-pc 的节点 (控制端 127.0.0.1:18790, 开 shell), 管理员脚本先装 OpenSSH 再跑标准接入
- 托盘/安装器/卸载器接线
"""
import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib import desktop_env as de  # noqa: E402
from lib import desktop_local_node as dn  # noqa: E402

WIN_JSON = json.dumps({"user": "litecode", "computer": "PC1", "profile": "C:\\Users\\litecode",
                       "desktop": "C:\\Users\\litecode\\Desktop", "documents": "D:\\我的文档",
                       "downloads": ""}, ensure_ascii=False)


@pytest.fixture
def fake_mnt(tmp_path, monkeypatch):
    ps = tmp_path / "c" / de.PS_REL
    ps.parent.mkdir(parents=True)
    ps.write_text("")
    monkeypatch.delenv("LITECODE_DESKTOP", raising=False)
    return str(tmp_path)


# ── 环境检测 ──

def test_detect_wsl_with_windows_powershell(fake_mnt):
    v = "Linux version 6.6.87.2-microsoft-standard-WSL2 (root@x) #1 SMP"
    assert de.is_desktop(v, mnt=fake_mnt) is True


def test_detect_requires_windows_mount(tmp_path, monkeypatch):
    monkeypatch.delenv("LITECODE_DESKTOP", raising=False)
    v = "Linux version 6.6.87.2-microsoft-standard-WSL2"
    assert de.is_desktop(v, mnt=str(tmp_path)) is False          # Docker Desktop 里的容器: 有 WSL 内核但没有 /mnt/c
    assert de.is_desktop("Linux version 6.8.0-139-generic (buildd@lcy02)", mnt=str(tmp_path)) is False


def test_env_flag_overrides(fake_mnt, monkeypatch):
    monkeypatch.setenv("LITECODE_DESKTOP", "0")
    assert de.is_desktop("microsoft-standard-WSL2", mnt=fake_mnt) is False
    monkeypatch.setenv("LITECODE_DESKTOP", "1")
    assert de.is_desktop("Linux generic", mnt="/nonexistent") is True


@pytest.mark.parametrize("win,wsl", [
    ("C:\\Users\\litecode\\Desktop", "/mnt/c/Users/litecode/Desktop"),
    ("D:\\我的文档\\", "/mnt/d/我的文档"),
    ("c:/Users/a b/Downloads", "/mnt/c/Users/a b/Downloads"),
    ("C:\\", "/mnt/c"),
    ("\\\\server\\share", ""), ("", ""),
])
def test_win_to_wsl(win, wsl):
    assert de.win_to_wsl(win) == wsl


def test_query_windows_parses_noisy_utf8_output(fake_mnt):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(stdout=("#< CLIXML\r\n" + WIN_JSON + "\r\n").encode("utf-8"), returncode=0)
    info = de.query_windows(runner, mnt=fake_mnt)
    assert calls[0][0] == f"{fake_mnt}/c/{de.PS_REL}" and "-NoProfile" in calls[0]
    assert "UTF8" in calls[0][-1]                                  # 中文路径按 UTF-8 输出
    assert info["user"] == "litecode" and info["documents"] == "D:\\我的文档"
    assert info["downloads"] == "C:\\Users\\litecode\\Downloads"   # 取不到时按用户目录补默认位置


@pytest.mark.parametrize("runner", [
    lambda cmd, **kw: (_ for _ in ()).throw(OSError("interop disabled")),
    lambda cmd, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, 20)),
    lambda cmd, **kw: SimpleNamespace(stdout=b"not json", returncode=1),
])
def test_query_windows_failure_returns_empty(runner, fake_mnt):
    assert de.query_windows(runner, mnt=fake_mnt) == {}


# ── 系统提示 ──

def _info():
    d = {"user": "litecode", "desktop": "C:\\Users\\litecode\\Desktop", "documents": "C:\\Users\\litecode\\Documents",
         "downloads": "C:\\Users\\litecode\\Downloads"}
    for k in ("desktop", "documents", "downloads"):
        d[k + "_wsl"] = de.win_to_wsl(d[k])
    return d


def test_prompt_block_content_and_size():
    text = de.prompt_block(_info())
    assert "WSL" in text and "litecode" in text
    assert "C:\\Users\\litecode\\Desktop = /mnt/c/Users/litecode/Desktop" in text
    assert "/mnt/c/Users/litecode/Documents" in text and "/mnt/c/Users/litecode/Downloads" in text
    assert "powershell.exe -NoProfile -Command" in text and "Start-Process" in text and "UTF8" in text
    assert "优先命令行" in text and "this-pc" in text
    assert "不要让用户运行节点注册" in text
    assert "都指 **Windows**" in text and "别拿子系统的 Python/apt 当用户环境" in text and "用 Windows 的 git/python/node 跑" in text
    body = text.split("\n", 1)[1]
    # 2026-10-22 上限 400 → 700: 加了"这台电脑=Windows 不是子系统"两句 (真机: 检查/装了子系统里的 Python; GitHub 项目跑在子系统里)
    assert len(body) - sum(len(_info()[k]) + len(_info()[k + "_wsl"]) for k in ("desktop", "documents", "downloads")) <= 700


def test_prompt_block_without_paths_and_off_desktop():
    assert de.prompt_block({}) == ""
    text = de.prompt_block({"user": "", "desktop": "", "documents": "", "downloads": "",
                            "desktop_wsl": "", "documents_wsl": "", "downloads_wsl": ""})
    assert "/mnt/c/Users/<用户名>/" in text and " = /mnt" not in text


def test_info_off_desktop_is_empty_and_cached(monkeypatch):
    monkeypatch.setattr(de, "_cache", None)
    monkeypatch.setattr(de, "is_desktop", lambda *a, **k: False)
    assert de.info() == {} and de.prompt_block() == ""


def test_info_desktop_queries_once(monkeypatch):
    n = []
    monkeypatch.setattr(de, "_cache", None)
    monkeypatch.setattr(de, "is_desktop", lambda *a, **k: True)
    monkeypatch.setattr(de, "ensure_path", lambda *a, **k: None)
    monkeypatch.setattr(dn, "refresh_ssh_host", lambda *a, **k: None)
    monkeypatch.setattr(de, "query_windows", lambda *a, **k: n.append(1) or json.loads(WIN_JSON))
    first, second = de.prompt_block(), de.prompt_block()
    assert first == second and len(n) == 1                     # 启动后固定, 不破坏 KV 前缀缓存
    assert "D:\\我的文档 = /mnt/d/我的文档" in first
    monkeypatch.setattr(de, "_cache", None)


def test_probe_env_includes_desktop_block(monkeypatch):
    from lib import prompt
    monkeypatch.setattr(de, "prompt_block", lambda *a: "## 本机 (Windows 桌面版)\nXYZ")
    assert "## 本机 (Windows 桌面版)\nXYZ" in prompt._probe_env()
    monkeypatch.setattr(de, "prompt_block", lambda *a: "")
    assert "本机 (Windows 桌面版)" not in prompt._probe_env()


def test_ensure_path_appends_windows_dirs_last(tmp_path, monkeypatch):
    for d in ("c/Windows/System32/WindowsPowerShell/v1.0", "c/Windows/System32"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    de.ensure_path(str(tmp_path))
    de.ensure_path(str(tmp_path))
    parts = __import__("os").environ["PATH"].split(":")
    assert parts[:2] == ["/usr/local/bin", "/usr/bin"]          # Linux 自己的 python/node 仍优先
    assert parts.count(f"{tmp_path}/c/Windows/System32") == 1


# ── 本机节点接入 ──

def test_zip_sha_matches_bundled_package():
    assert hashlib.sha256(Path(dn.zip_path()).read_bytes()).hexdigest() == dn.zip_sha256()


def test_build_script_structure():
    body = "Write-Host 'BOOTSTRAP'"
    s = dn.build_script(body, zip_win="C:\\Users\\a\\LiteCode\\payload\\openssh.zip", log_win="C:\\x\\l.log",
                        expected_user="PC1\\o'neil", old_ids=["a" * 32, "bad;id"])
    assert s.index("Start-Transcript") < s.index("Set-Service -Name sshd -StartupType Automatic") < s.index("BOOTSTRAP")
    assert f"-ZipSha256 '{dn.zip_sha256()}'" in s and "-ZipPath 'C:\\Users\\a\\LiteCode\\payload\\openssh.zip'" in s
    assert "if ($lcMe -ne 'PC1\\o''neil')" in s                 # 提权成了别的账户就不装代理
    assert "'" + "a" * 32 + "'" in s and "bad;id" not in s       # 只接受合法节点 ID
    assert "-Schedule" in s and "$lcCode+=10" in s and "$lcCode+=20" in s and "$lcCode=40" in s
    assert s.rstrip().endswith("exit $lcCode")
    assert not re.search(r"(?m)^\s*#", s)                        # 注释已去掉, 缩短编码后的命令行


def test_setup_ps1_policy():
    ps = (ROOT / "lib/desktop_local_setup.ps1").read_text(encoding="utf-8")
    assert "Set-Service -Name sshd -StartupType Automatic" in ps
    assert "-RemoteAddress LocalSubnet" in ps and "Disable-NetFirewallRule" in ps
    assert "PasswordAuthentication no" in ps
    assert "Get-AuthenticodeSignature" in ps and "O=Microsoft Corporation" in ps
    # 离线包优先, 可选功能兜底
    assert ps.index("$ZipPath -and") < ps.index("Add-WindowsCapability")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("LITECODE_SSH_HOSTS", str(tmp_path / "ssh_hosts.json"))
    return tmp_path / "ws"


def test_prepare_creates_local_node_and_rejoin_replaces_it(ws):
    from lib.windows_nodes import Nodes
    first = dn.prepare(ws, expected_user="PC1\\litecode", zip_win="C:\\z.zip", log_win="C:\\l.log")
    node = next(n for n in Nodes(ws).list() if n["alias"] == "this-pc")
    assert node["id"] == first["id"] and node["state"] == "awaiting_join"
    assert node["server_url"] == "http://127.0.0.1:18790" and node["capabilities"] == {"shell": True}
    assert re.search(r"-Code '[^']+'", first["script"]) and "/api/windows/bootstrap.ps1" in first["script"]
    assert "-SshPublicKey 'ssh-ed25519 " in first["script"] and first["old_ids"] == []
    encoded = base64.b64encode(first["script"].encode("utf-16le")).decode()
    assert len("-NoProfile -ExecutionPolicy Bypass -EncodedCommand " + encoded) < 32000   # Windows 命令行上限
    second = dn.prepare(ws)
    nodes = [n for n in Nodes(ws).list() if n["alias"] == "this-pc"]
    assert len(nodes) == 1 and nodes[0]["id"] == second["id"] != first["id"]
    assert second["old_ids"] == [first["id"]] and first["id"] in second["script"]


def test_main_prepare_params_survive_backslashes(ws, monkeypatch, capsys):
    import lib.config
    monkeypatch.setattr(lib.config, "WORKSPACE", ws)
    p = {"user": "PC1\\litecode", "zip": "C:\\Users\\litecode\\AppData\\Local\\LiteCode\\payload\\openssh.zip",
         "log": "C:\\Users\\litecode\\AppData\\Local\\LiteCode\\local-control.log"}
    dn.main(["prepare", "--params", base64.b64encode(json.dumps(p).encode()).decode()])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "'PC1\\litecode'" in out["script"] and p["zip"] in out["script"] and p["log"] in out["script"]


@pytest.mark.parametrize("mode,route,host", [
    ("mirrored", "", "127.0.0.1"),
    ("nat", "default via 172.25.64.1 dev eth0 proto kernel", "172.25.64.1"),
    ("nat", "", ""),
])
def test_windows_host(mode, route, host):
    def runner(cmd, **kw):
        return SimpleNamespace(stdout=mode if cmd[0] == "wslinfo" else route)
    assert dn.windows_host(runner) == host


def test_finish_saves_ssh_alias_and_refresh_updates_gateway(ws, monkeypatch):
    from lib.windows_nodes import Nodes
    created = dn.prepare(ws)
    nodes = Nodes(ws)
    code = re.search(r"-Code '([^']+)'", created["script"])[1]
    device = nodes.redeem(code, {"computer": "PC1", "user": "PC1\\litecode"})
    nodes.heartbeat(device["id"], device["token"], "online")
    checked = []
    monkeypatch.setattr(dn, "windows_host", lambda *a: "172.25.64.1")
    monkeypatch.setattr(dn, "ssh_check", lambda e, *a: checked.append(e) or True)
    res = dn.finish(ws, created["id"], wait=1)
    assert res == {"id": created["id"], "state": "online", "error": "", "ssh_alias": "this-pc-ssh", "ssh_ok": True}
    entry = json.loads((ws.parent / "ssh_hosts.json").read_text())["this-pc-ssh"]
    assert entry["host"] == "172.25.64.1" and entry["user"] == "litecode" and entry["password"] == ""
    assert entry["key_file"].endswith("/ssh/" + created["id"]) and Path(entry["key_file"]).exists()
    assert oct((ws.parent / "ssh_hosts.json").stat().st_mode & 0o777) == "0o600"
    monkeypatch.setattr(dn, "windows_host", lambda *a: "172.30.16.1")   # WSL 重启后网关变了
    dn.refresh_ssh_host()
    assert json.loads((ws.parent / "ssh_hosts.json").read_text())["this-pc-ssh"]["host"] == "172.30.16.1"


def test_finish_reports_offline_node(ws, monkeypatch):
    created = dn.prepare(ws)
    monkeypatch.setattr(dn, "windows_host", lambda *a: "172.25.64.1")
    res = dn.finish(ws, created["id"], wait=0)
    assert res["state"] == "awaiting_join" and res["ssh_alias"] == "" and res["ssh_ok"] is False


# ── 托盘 / 安装器接线 ──

WIN = REPO / "packaging" / "windows"


def test_go_exit_codes_match_python():
    go = (WIN / "localcontrol.go").read_text(encoding="utf-8")
    for code in (dn.EXIT_SSH, dn.EXIT_NODE, dn.EXIT_SSH + dn.EXIT_NODE, dn.EXIT_OTHER_USER):
        assert f"case {code}:" in go
    assert "localNodeScript = \"/opt/litecode/lib/desktop_local_node.py\"" in go
    assert '"--params", base64.StdEncoding' in go               # 不经 wsl.exe 命令行传反斜杠路径


def test_tray_setup_and_uninstaller_wired():
    assert "addLocalControlMenu()" in (WIN / "tray.go").read_text(encoding="utf-8")
    assert "setupStepLocalControl(say)" in (WIN / "setup.go").read_text(encoding="utf-8")
    main = (WIN / "main.go").read_text(encoding="utf-8")
    assert '"no-local-control"' in main and '"remove-local-control"' in main and '"local-control"' in main
    nsi = (WIN / "installer.nsi").read_text(encoding="utf-8")
    assert "--remove-local-control" in nsi and "OpenSSH" in nsi
    assert '"LITECODE_DESKTOP=1"' in (WIN / "wsl.go").read_text(encoding="utf-8")
