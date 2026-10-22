"""桌面版 (Windows WSL 子系统) 本机环境感知。

桌面版 LiteCode 跑在用户这台 Windows 电脑的 WSL 子系统里, 能经 /mnt/c 直接读写 Windows 文件、
经 WSL 互操作调用 powershell.exe。模型原本不知道这一点 (2026-10-22 真机: 让它在桌面建文件,
它吐了一段节点注册命令让用户去运行)。这里在启动时探测一次, 生成一小段固定的系统提示 (进 {{ENV}},
启动后不再变化, 不破坏 KV 前缀缓存)。

只在确实是桌面版时生效: LITECODE_DESKTOP=1, 或 /proc/version 含 microsoft 且能找到 Windows 的
powershell.exe (/mnt/c 已挂载)。服务器版 docker、普通 Linux 一律返回空串。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path

log = logging.getLogger("openclaw")

MNT = os.environ.get("LITECODE_WIN_MNT", "/mnt")
PS_REL = "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
# 本机节点 / SSH 别名 (节点别名只允许英文, 见 windows_nodes.create)
LOCAL_NODE_ALIAS = "this-pc"
LOCAL_SSH_ALIAS = "this-pc-ssh"

_PS_QUERY = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
             "$d=(New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path; "
             "@{user=$env:USERNAME; computer=$env:COMPUTERNAME; profile=$env:USERPROFILE; "
             "desktop=[Environment]::GetFolderPath('Desktop'); documents=[Environment]::GetFolderPath('MyDocuments'); "
             "downloads=$d} | ConvertTo-Json -Compress")

_cache: dict | None = None


def powershell_path(mnt: str = MNT) -> str:
    return f"{mnt}/c/{PS_REL}"


def is_desktop(proc_version: str | None = None, mnt: str = MNT) -> bool:
    """桌面版 WSL 子系统: 显式环境变量, 或 WSL 内核 + 能找到 Windows 的 powershell.exe。"""
    flag = os.environ.get("LITECODE_DESKTOP", "").strip()
    if flag == "0":
        return False
    if flag == "1":
        return True
    if proc_version is None:
        try:
            proc_version = Path("/proc/version").read_text(errors="replace")
        except OSError:
            return False
    return "microsoft" in proc_version.lower() and os.path.isfile(powershell_path(mnt))


def win_to_wsl(path: str, mnt: str = MNT) -> str:
    """C:\\Users\\a\\Desktop → /mnt/c/Users/a/Desktop (不认识的格式返回空串)。"""
    m = re.fullmatch(r"([A-Za-z]):[\\/]*(.*)", (path or "").strip())
    if not m:
        return ""
    rest = m.group(2).replace("\\", "/").strip("/")
    return f"{mnt}/{m.group(1).lower()}" + (f"/{rest}" if rest else "")


def query_windows(runner=subprocess.run, mnt: str = MNT) -> dict:
    """调一次 powershell.exe 取 Windows 用户名与 桌面/文档/下载 路径; 失败返回 {}。"""
    try:
        r = runner([powershell_path(mnt), "-NoProfile", "-NonInteractive", "-Command", _PS_QUERY],
                   capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
        out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else (r.stdout or "")
        start = out.find("{")
        data = json.loads(out[start:out.rfind("}") + 1]) if start >= 0 else {}
    except Exception as e:   # 互操作关闭 / 超时 / 输出不是 JSON
        log.warning(f"  [desktop-env] 查询 Windows 用户目录失败: {e}")
        return {}
    info = {k: str(data.get(k) or "").strip() for k in
            ("user", "computer", "profile", "desktop", "documents", "downloads")}
    profile = info["profile"]
    # 文档/下载取不到 (极少数精简系统) 时按用户目录补默认位置
    for key, sub in (("desktop", "Desktop"), ("documents", "Documents"), ("downloads", "Downloads")):
        if not info[key] and profile:
            info[key] = profile.rstrip("\\") + "\\" + sub
    return info if info["user"] else {}


def ensure_path(mnt: str = MNT) -> None:
    """把 Windows PowerShell / System32 追加到 PATH 末尾 (wsl.conf 关了 appendWindowsPath):
    模型可直接写 powershell.exe / explorer.exe; 追加在最后, 不会抢 Linux 自己的 python/node。"""
    extra = [f"{mnt}/c/Windows/System32/WindowsPowerShell/v1.0", f"{mnt}/c/Windows/System32", f"{mnt}/c/Windows"]
    parts = os.environ.get("PATH", "").split(":")
    add = [d for d in extra if d not in parts and os.path.isdir(d)]
    if add:
        os.environ["PATH"] = ":".join([p for p in parts if p] + add)


def info(force: bool = False) -> dict:
    """桌面版环境信息 (进程内缓存, 只探测一次); 非桌面版返回 {}。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    if not is_desktop():
        _cache = {}
        return _cache
    ensure_path()
    try:   # 已接入本机 SSH 时刷新 Windows 网关地址 (NAT 下每次 WSL 启动可能变)
        from .desktop_local_node import refresh_ssh_host
        refresh_ssh_host()
    except Exception as e:
        log.warning(f"  [desktop-env] 刷新本机 SSH 地址失败: {e}")
    data = query_windows()
    if not data:   # 查不到也要告诉模型它在 WSL 里, 路径让它自己查
        data = {"user": "", "desktop": "", "documents": "", "downloads": ""}
    for key in ("desktop", "documents", "downloads"):
        data[key + "_wsl"] = win_to_wsl(data.get(key, ""))
    _cache = data
    return _cache


def prompt_block(data: dict | None = None) -> str:
    """注入系统提示 {{ENV}} 段的本机说明 (约 400 字, 启动后固定)。"""
    d = info() if data is None else data
    if not d:
        return ""
    lines = ["## 本机 (Windows 桌面版)",
             f"你运行在用户这台 Windows 电脑{('（用户 ' + d['user'] + '）') if d.get('user') else ''}的 WSL 子系统里，"
             "可直接读写 Windows 文件："]
    for key, name in (("desktop", "桌面"), ("documents", "文档"), ("downloads", "下载")):
        if d.get(key) and d.get(key + "_wsl"):
            lines.append(f"- {name} {d[key]} = {d[key + '_wsl']}")
    if not d.get("desktop"):
        lines.append("- Windows 用户目录在 /mnt/c/Users/<用户名>/ 下（用 powershell.exe 查 $env:USERPROFILE）")
    lines += [
        "**重要**：用户说的「这台电脑 / 我的 Python / 装软件 / 浏览器 / VS Code / 开机启动」都指 **Windows**，"
        "不是你所在的 Linux 子系统。检查、安装、配置一律在 Windows 上做（powershell.exe、winget、py）；"
        "别拿子系统的 Python/apt 当用户环境，也别为用户的需求往子系统里装东西。"
        "给用户下载/克隆/运行的项目放 Windows（桌面或文档），用 Windows 的 git/python/node 跑，要常驻就给开机启动或快捷方式。",
        "用户要的文件（pptx/pdf/xlsx/txt 等）直接写到这些目录，回复给 Windows 路径（wslpath -w 可转换）。",
        "Windows 命令用 `powershell.exe -NoProfile -Command \"...\"`（中文输出先设 "
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8）；打开文件/文件夹/程序用 Start-Process。",
        f"优先命令行；截图/OCR/点鼠标只做必须看界面的事（windows_desktop alias={LOCAL_NODE_ALIAS}，未接入时提示用户在托盘点"
        "「接入本机操控」）。不要让用户运行节点注册/接入命令来操控这台电脑。",
    ]
    return "\n".join(lines)
