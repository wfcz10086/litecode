"""桌面版"操控本机": 把用户这台 Windows 电脑接入成 LiteCode 自己的 Windows 节点 (别名 this-pc)。

托盘/安装程序 (packaging/windows/localcontrol.go) 以 root 在子系统里调本模块:
  prepare  生成节点 (控制端 http://127.0.0.1:18790, 开启 shell 能力), 输出一段要以管理员运行的 PowerShell:
           先装 OpenSSH Server 并设开机自启 (lib/desktop_local_setup.ps1), 再跑标准节点接入
           (windows_node_bootstrap.ps1: 桌面代理 + SSH 公钥), 最后清掉上一次接入的旧代理。
  finish   等节点上线, 登记 SSH 别名 this-pc-ssh (Windows 用户 + 节点私钥), 实测一次 SSH。
不经过 Web 管理接口 (那要登录凭据): 只有能以 root 进子系统的本机用户才能调用, 与托盘本身同权限。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if __name__ == "__main__":   # 作为脚本运行: 补齐 /opt/litecode 与 core 的导入路径
    sys.path[:0] = [str(_LIB.parent), str(_LIB.parent / "core")]

from lib.desktop_env import LOCAL_NODE_ALIAS, LOCAL_SSH_ALIAS  # noqa: E402

LOCAL_SERVER = "http://127.0.0.1:18790"   # 桌面版 Web 只监听本机; Windows 侧经 WSL localhost 转发访问
ZIP_NAME = "OpenSSH-Win64-v10.0.0.0p2.zip"
# 管理员脚本退出码: 0 全部成功; 10 OpenSSH 没就绪; 20 节点接入失败; 30 都失败; 40 提权成了别的 Windows 账户
EXIT_SSH, EXIT_NODE, EXIT_OTHER_USER = 10, 20, 40


def _ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _compact(ps: str) -> str:
    """去掉整行注释与空行, 缩短 -EncodedCommand (命令行上限 32767 字符)。"""
    return "\n".join(l for l in ps.splitlines() if l.strip() and not l.lstrip().startswith("#"))


def zip_sha256() -> str:
    """离线 OpenSSH 包的 sha256 以 bootstrap 里写死的为准 (同一份包, 测试校验两者一致)。"""
    boot = (_LIB / "windows_node_bootstrap.ps1").read_text(encoding="utf-8-sig")
    return re.search(r"\$OpenSshZipSha256 = '([0-9a-f]{64})'", boot).group(1)


def zip_path() -> str:
    return str(_LIB / "windows_vendor" / ZIP_NAME)


class _SshStore:
    """与 core/tools/handlers/ssh_hosts.py 同一个文件、同样的 0600 原子写。不直接 import 它:
    导入 core.tools.handlers 包会把全部工具 handler 拉进来, 服务启动探测环境时不该有这个副作用。"""

    @staticmethod
    def path() -> Path:
        return Path(os.environ.get("LITECODE_SSH_HOSTS", "/tmp/litecode_workspace/.secrets/ssh_hosts.json"))

    def _load(self) -> dict:
        try:
            return json.loads(self.path().read_text())
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, path)


def _ssh_store() -> _SshStore:
    return _SshStore()


def _nodes(workspace):
    from lib.windows_nodes import Nodes
    return Nodes(workspace)


def _install_body(command: str) -> str:
    """Nodes.create 只返回编码后的安装命令; 解出其中的 PowerShell 正文。"""
    return base64.b64decode(command.rsplit(" ", 1)[-1]).decode("utf-16le")


def build_script(install_body: str, *, zip_win: str = "", log_win: str = "", expected_user: str = "",
                 old_ids=(), sha: str | None = None) -> str:
    setup = _compact((_LIB / "desktop_local_setup.ps1").read_text(encoding="utf-8-sig"))
    olds = ",".join(_ps_quote(i) for i in old_ids if re.fullmatch(r"[a-f0-9]{32}", i))
    lines = [
        "$ErrorActionPreference='Stop'; $lcCode=0",
        f"try {{ Start-Transcript -Path {_ps_quote(log_win)} -Force | Out-Null }} catch {{}}" if log_win else "",
        "$lcMe=[Security.Principal.WindowsIdentity]::GetCurrent().Name; Write-Host ('LiteCode 本机操控: 当前账户 '+$lcMe)",
        "try { & {\n" + setup + "\n} -ZipPath " + _ps_quote(zip_win) + " -ZipSha256 " + _ps_quote(sha or zip_sha256())
        + " } catch { Write-Warning ('OpenSSH 没有就绪: '+$_.Exception.Message); $lcCode+=" + str(EXIT_SSH) + " }",
    ]
    node = ("try { & {\n" + install_body + "\n}\n"
            # 重新接入: 新代理装好后, 本机卸载上一次的旧代理 (新公钥已在, 旧代理的卸载不会动共享的 sshd)
            "foreach ($old in @(" + olds + ")) { foreach ($d in @(Get-ChildItem (Join-Path $env:LOCALAPPDATA 'LiteCodeNodes') -Directory -ErrorAction SilentlyContinue)) {"
            " $f=Join-Path $d.FullName 'node.json'; $u=Join-Path $d.FullName 'uninstall.ps1';"
            " if ((Test-Path $f) -and (Test-Path $u) -and ((Get-Content $f -Raw | ConvertFrom-Json).id -eq $old)) {"
            " try { & $u -Root $d.FullName -Schedule } catch { Write-Warning ('旧代理清理失败: '+$_.Exception.Message) } } } }\n"
            "} catch { Write-Warning ('本机节点接入失败: '+$_.Exception.Message); $lcCode+=" + str(EXIT_NODE) + " }")
    if expected_user:
        # UAC 里填了另一个管理员账户: 桌面代理会装到那个账户下, 操控不到当前用户的桌面, 不接入
        lines.append("if ($lcMe -ne " + _ps_quote(expected_user) + ") { Write-Warning ('提权后的账户 '+$lcMe+' 不是当前用户 "
                     + expected_user.replace("'", "''") + "，桌面代理没有安装。请用当前账户（需是管理员）重新接入。'); $lcCode="
                     + str(EXIT_OTHER_USER) + " } else {\n" + node + "\n}")
    else:
        lines.append(node)
    lines += ["try { Stop-Transcript | Out-Null } catch {}" if log_win else "", "exit $lcCode"]
    return "\n".join(l for l in lines if l)


def prepare(workspace, *, expected_user="", zip_win="", log_win="", base_url=LOCAL_SERVER) -> dict:
    """新建本机节点 (旧的 this-pc 先撤销并删除记录), 返回要以管理员运行的脚本。"""
    nodes = _nodes(workspace)
    old_ids = []
    for node in nodes.list():
        if node["alias"] == LOCAL_NODE_ALIAS:
            if node["state"] not in ("revoked", "uninstalled"):
                nodes.revoke(node["id"])   # 旧代理收到 403 (revoked) 后自行停用; 管理员脚本再在本机卸载它
            nodes.remove(node["id"])
            old_ids.append(node["id"])
    created = nodes.create(LOCAL_NODE_ALIAS, base_url, tuple(_ssh_store()._load()), enable_shell=True)
    script = build_script(_install_body(created["command"]), zip_win=zip_win, log_win=log_win,
                          expected_user=expected_user, old_ids=old_ids)
    return {"id": created["id"], "alias": LOCAL_NODE_ALIAS, "old_ids": old_ids, "script": script}


def windows_host(runner=subprocess.run) -> str:
    """子系统里访问 Windows 本机的地址: 镜像网络 = 127.0.0.1, NAT (默认) = 默认网关 (Windows 的 vEthernet)。"""
    try:
        mode = runner(["wslinfo", "--networking-mode"], capture_output=True, text=True, timeout=5).stdout.strip()
        if mode == "mirrored":
            return "127.0.0.1"
    except Exception:
        pass
    try:
        out = runner(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def ssh_check(entry: dict, runner=subprocess.run) -> bool:
    try:
        r = runner(["ssh", "-i", entry["key_file"], "-p", str(entry.get("port", 22)), "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=8",
                    f"{entry['user']}@{entry['host']}", "cmd /c echo LITECODE_SSH_OK"],
                   capture_output=True, text=True, timeout=30)
        return "LITECODE_SSH_OK" in (r.stdout or "")
    except Exception:
        return False


def save_ssh_host(workspace, node: dict, host: str) -> dict | None:
    """登记 this-pc-ssh: Windows 用户 + 节点接入时生成的私钥 (公钥已由接入脚本写进 administrators_authorized_keys)。"""
    key = _nodes(workspace).root / "ssh" / node["id"]
    user = str(node.get("user") or "").split("\\")[-1].strip()
    if not (host and user and key.exists()):
        return None
    entry = {"host": host, "port": 22, "user": user, "password": "", "key_file": str(key), "jump": "",
             "note": "本机 Windows (桌面版自动接入; 地址随 WSL 启动刷新, 默认 shell 为 cmd, 用 powershell -Command 执行 PowerShell)"}
    store = _ssh_store()
    hosts = store._load()
    if hosts.get(LOCAL_SSH_ALIAS) != entry:
        hosts[LOCAL_SSH_ALIAS] = entry
        store._save(hosts)
    return entry


def finish(workspace, node_id: str, wait: int = 180) -> dict:
    """等新节点上线 (代理首次回连), 登记 SSH 别名并实测。"""
    deadline = time.time() + wait
    node = None
    while True:
        node = next((n for n in _nodes(workspace).list() if n["id"] == node_id), None)
        if not node or node["state"] in ("online", "error", "revoked") or time.time() > deadline:
            break
        time.sleep(3)
    result = {"id": node_id, "state": node["state"] if node else "missing", "error": (node or {}).get("error", ""),
              "ssh_alias": "", "ssh_ok": False}
    if node:
        entry = save_ssh_host(workspace, node, windows_host())
        if entry:
            result.update(ssh_alias=LOCAL_SSH_ALIAS, ssh_ok=ssh_check(entry))
    return result


def refresh_ssh_host(workspace=None) -> None:
    """NAT 网络下 Windows 网关地址每次 WSL 启动可能变化: 服务启动时把 this-pc-ssh 的地址刷新为当前值。"""
    store = _ssh_store()
    hosts = store._load()
    entry = hosts.get(LOCAL_SSH_ALIAS)
    if not entry:
        return
    host = windows_host()
    if host and entry.get("host") != host:
        entry["host"] = host
        store._save(hosts)


def main(argv=None):
    from lib.config import WORKSPACE
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("prepare")
    # wsl.exe -- 会经 shell 转发参数, Windows 路径里的反斜杠会被吃掉 (2026-10-22 真机): 参数整体 base64(JSON) 传入
    a.add_argument("--params", default="", help='base64(JSON {"user": 期望的 DOMAIN\\user, "zip": 离线 OpenSSH 包, '
                                                '"log": 管理员脚本日志}), 路径均为 Windows 路径; 提权后账户不一致就不装代理')
    b = sub.add_parser("finish")
    b.add_argument("--id", required=True)
    b.add_argument("--wait", type=int, default=180)
    sub.add_parser("zip-path")
    args = p.parse_args(argv)
    if args.cmd == "prepare":
        params = json.loads(base64.b64decode(args.params).decode("utf-8")) if args.params else {}
        out = prepare(WORKSPACE, expected_user=str(params.get("user", "")), zip_win=str(params.get("zip", "")),
                      log_win=str(params.get("log", "")))
    elif args.cmd == "finish":
        out = finish(WORKSPACE, args.id, args.wait)
    else:
        out = {"path": zip_path()}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
