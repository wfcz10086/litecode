"""
file_transaction.py — 多文件原子事务编辑 (P5-e)
================================================
基于 apply_blocks (P3-e) 提供事务保证: 全部成功才提交, 任一失败回滚所有改动.

不同于 apply_blocks 的"per-block atomic"(部分成功), 这里是"all-or-nothing".

API:
  begin_transaction(file_paths) → snapshot_id
  commit_transaction(snapshot_id)  → True (提交, 删快照)
  rollback_transaction(snapshot_id) → True (恢复所有原文)

实现: 在 transaction 开始前对每个文件做 snapshot, 失败时按 snapshot 还原.
"""
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import List, Dict, Optional


def _snap_dir(workspace) -> Path:
    p = Path(workspace) / "transactions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def begin_transaction(workspace, file_paths: List[str]) -> str:
    """对所有文件做 snapshot, 返回 snapshot_id"""
    sid = f"tx_{uuid.uuid4().hex[:10]}"
    sd = _snap_dir(workspace) / sid
    sd.mkdir(parents=True, exist_ok=True)
    manifest = {"snapshot_id": sid, "started": time.time(), "files": []}
    for i, fp in enumerate(file_paths):
        src = Path(fp)
        if not src.exists():
            continue
        backup = sd / f"f_{i}_{src.name}"
        try:
            shutil.copy2(src, backup)
            manifest["files"].append({"original": str(src), "backup": str(backup)})
        except Exception as e:
            manifest["files"].append({"original": str(src), "error": str(e)})
    (sd / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return sid


def commit_transaction(workspace, snapshot_id: str) -> bool:
    """成功 → 删快照"""
    sd = _snap_dir(workspace) / snapshot_id
    if not sd.exists():
        return False
    try:
        shutil.rmtree(sd)
        return True
    except Exception:
        return False


def rollback_transaction(workspace, snapshot_id: str) -> bool:
    """回滚: 把 backup 复制回 original"""
    sd = _snap_dir(workspace) / snapshot_id
    mf = sd / "manifest.json"
    if not mf.exists():
        return False
    try:
        manifest = json.loads(mf.read_text(errors="replace"))
    except Exception:
        return False
    ok = True
    for entry in manifest.get("files", []):
        orig = entry.get("original")
        backup = entry.get("backup")
        if orig and backup and Path(backup).exists():
            try:
                shutil.copy2(backup, orig)
            except Exception:
                ok = False
    # 不论部分失败, 删快照目录
    try:
        shutil.rmtree(sd)
    except Exception:
        pass
    return ok


def list_open_transactions(workspace) -> List[str]:
    p = _snap_dir(workspace)
    return [d.name for d in p.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
