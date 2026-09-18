"""handlers/snapshot_ops.py — 框架级快照的列出与回滚工具。

区别于 git_ops (那是 agent 直接操作项目自己的 git): 这里操作的是框架在每个
task 边界自动打的旁路快照 (lib/snapshot.py), agent 或人可以用它回到某个绿点。
"""
from __future__ import annotations

from typing import Any

from . import register

try:
    import tool_dispatch as _td
    _WORKSPACE = _td.WORKSPACE
except Exception:  # pragma: no cover
    from pathlib import Path
    _WORKSPACE = Path("/tmp/litecode_workspace")

from lib.snapshot import list_snapshots as _list, restore as _restore


@register("snapshot_list")
async def h_snapshot_list(sid: str, args: dict) -> tuple[str, Any]:
    """列出某项目的自动快照 (最新在前)。

    args: project (项目根绝对路径, 必填), limit (默认 20)
    """
    proj = (args.get("project") or "").strip()
    if not proj:
        return "ERROR: snapshot_list 需要 project (项目根绝对路径)", None
    rows = _list(_WORKSPACE, proj, int(args.get("limit") or 20))
    if not rows:
        return f"[snapshot] {proj} 还没有任何快照 (需先跑过至少一个 task 边界)", None
    lines = [f"{proj} 的快照 (最新在前):"]
    for r in rows:
        lines.append(f"  {r['hash']}  {r['time']}  {r['label']}")
    lines.append("\n回滚: snapshot_restore(project=..., hash=...)")
    return "\n".join(lines), None


@register("snapshot_restore")
async def h_snapshot_restore(sid: str, args: dict) -> tuple[str, Any]:
    """把项目回滚到某个快照。回滚前会自动保存当前状态, 时间线只增不减。

    args: project (项目根绝对路径, 必填), hash (目标快照短 hash, 必填)
    """
    proj = (args.get("project") or "").strip()
    sha = (args.get("hash") or args.get("sha") or "").strip()
    if not proj or not sha:
        return "ERROR: snapshot_restore 需要 project + hash", None
    ok, msg = _restore(_WORKSPACE, proj, sha)
    return (("[snapshot] " + msg) if ok else ("ERROR: " + msg)), None
