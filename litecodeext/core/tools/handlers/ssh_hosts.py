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

_STORE_PATH = Path(__file__).parent.parent.parent / "data" / "ssh_hosts.json"


def _load() -> dict:
    try:
        return json.loads(_STORE_PATH.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


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
        "note": args.get("note") or "",
    }
    data[alias] = entry
    _save(data)
    masked = entry["password"][:2] + "***" if entry["password"] else "(none)"
    return f"[ssh_save_host] 已保存: {alias} → {entry['user']}@{entry['host']}:{entry['port']} pwd={masked}", None


@register("ssh_list_hosts")
async def h_ssh_list_hosts(sid: str, args: dict) -> tuple[str, Any]:
    data = _load()
    if not data:
        return "[ssh_list_hosts] 无已存主机", None
    lines = []
    for alias, cfg in data.items():
        masked = cfg.get("password", "")[:2] + "***" if cfg.get("password") else ""
        note = f"  # {cfg['note']}" if cfg.get("note") else ""
        lines.append(f"  {alias}: {cfg.get('user','root')}@{cfg.get('host','')}:{cfg.get('port',22)} {masked}{note}")
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
