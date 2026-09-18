"""orchestrator/checkpoint_mixin.py — checkpoint 状态快照 mixin."""
from __future__ import annotations

import json
import time
from typing import Set

from blackboard import Blackboard


class _CheckpointMixin:
    """DAGOrchestrator 的 checkpoint 分片: save / load / clear."""

    def _save_checkpoint(self, plan_id: str, completed_ids: Set[str]):
        """保存已完成步骤的 ID 集合。"""
        try:
            ckpt = {
                "plan_id": plan_id,
                "completed": list(completed_ids),
                "blackboard": self.blackboard.to_dict(),  # type: ignore[attr-defined]
                "timestamp": time.time(),
            }
            p = self._ckpt_dir / f"{plan_id}.json"  # type: ignore[attr-defined]
            # [P0-#6] fsync 原子落盘, 崩溃/断电不留半文件
            from lib.atomic_io import atomic_write_json
            atomic_write_json(p, ckpt)
        except Exception:
            pass

    def _load_checkpoint(self, plan_id: str) -> Set[str]:
        """加载已完成步骤。返回空 set 表示无 checkpoint。"""
        try:
            p = self._ckpt_dir / f"{plan_id}.json"  # type: ignore[attr-defined]
            if p.exists():
                data = json.loads(p.read_text())
                # 恢复黑板
                bb_data = data.get("blackboard", {})
                if bb_data:
                    self.blackboard = Blackboard.from_dict(bb_data)  # type: ignore[attr-defined]
                return set(data.get("completed", []))
        except Exception:
            pass
        return set()

    def clear_checkpoint(self, plan_id: str):
        """清除 checkpoint。"""
        try:
            p = self._ckpt_dir / f"{plan_id}.json"  # type: ignore[attr-defined]
            p.unlink(missing_ok=True)
        except Exception:
            pass
