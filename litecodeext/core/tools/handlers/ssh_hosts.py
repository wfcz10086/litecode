"""handlers/ssh_hosts.py — SSH 主机配置持久化存储.

ssh_save_host: 保存主机凭据到磁盘 (alias → {host,port,user,password,...})
ssh_list_hosts: 列出所有已存主机
ssh_remove_host: 删除一个主机配置
get_ssh_creds: 内部工具函数, 供其他 handler 按 alias 查凭据
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import register

# 凭据别放代码目录 (data/ 在 git 里、容器里也没挂载): 放工作区 (宿主机持久化, gitignore + 公开版排除)
_STORE_PATH = Path(os.environ.get("LITECODE_SSH_HOSTS", "/tmp/litecode_workspace/.secrets/ssh_hosts.json"))


def _load() -> dict:
    try:
        return json.loads(_STORE_PATH.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    # [sec 2026-10-19] 凭据文件 0600 / 目录 0700; 先建 0600 临时文件再原子替换, 不存在"先宽后收"窗口
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_STORE_PATH.parent, 0o700)
    except OSError:
        pass
    tmp = _STORE_PATH.with_name(f".{_STORE_PATH.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        os.replace(tmp, _STORE_PATH)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def get_ssh_creds(alias: str) -> dict | None:
    """按 alias 返回主机配置 dict, 不存在则返回 None."""
    return _load().get(alias)


def resolve_ssh_args(args: dict) -> dict:
    """如果 args 里有 alias, 从存储里补全 host/port/user/password (显式传入优先)."""
    alias = (args.get("alias") or "").strip()
    if not alias:
        return args
    saved = get_ssh_creds(alias)
    if not saved:
        return args
    merged = dict(saved)
    merged.update({k: v for k, v in args.items() if v not in (None, "", 0)})
    return merged


@register("ssh_save_host")
async def h_ssh_save_host(sid: str, args: dict) -> tuple[str, Any]:
    alias = (args.get("alias") or "").strip()
    host = (args.get("host") or "").strip()
    if not alias or not host:
        return "ERROR: ssh_save_host 需要 alias + host", None
    data = _load()
    entry = {
        "host": host,
        "port": int(args.get("port") or 22),
        "user": (args.get("user") or "root").strip(),
        "password": args.get("password") or "",
        "key_file": (args.get("key_file") or "").strip(),
        "jump": (args.get("jump") or "").strip(),
        "note": args.get("note") or "",
    }
    data[alias] = entry
    _save(data)
    masked = "(saved)" if entry["password"] else "(none)"
    return f"[ssh_save_host] 已保存: {alias} → {entry['user']}@{entry['host']}:{entry['port']} pwd={masked}", None


@register("ssh_list_hosts")
async def h_ssh_list_hosts(sid: str, args: dict) -> tuple[str, Any]:
    data = _load()
    if not data:
        return "[ssh_list_hosts] 无已存主机", None
    lines = []
    for alias, cfg in data.items():
        auth = []
        if cfg.get("key_file"):
            auth.append("密钥")
        if cfg.get("password"):
            auth.append("密码已保存")
        via = f" 经跳板 {cfg['jump']}" if cfg.get("jump") else ""
        note = f"  # {cfg['note']}" if cfg.get("note") else ""
        lines.append(f"  {alias}: {cfg.get('user','root')}@{cfg.get('host','')}:{cfg.get('port',22)}"
                     f"{via} [{' + '.join(auth) or '无凭据'}]{note}")
    return "[ssh_list_hosts]\n" + "\n".join(lines), None


@register("ssh_remove_host")
async def h_ssh_remove_host(sid: str, args: dict) -> tuple[str, Any]:
    alias = (args.get("alias") or "").strip()
    if not alias:
        return "ERROR: ssh_remove_host 需要 alias", None
    data = _load()
    if alias not in data:
        return f"ERROR: alias '{alias}' 不存在", None
    del data[alias]
    _save(data)
    return f"[ssh_remove_host] 已删除: {alias}", None
