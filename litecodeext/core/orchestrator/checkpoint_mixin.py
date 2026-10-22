"""orchestrator/checkpoint_mixin.py — checkpoint 状态快照 mixin.

[P0-4 2026-10-21] 恢复一致性: checkpoint 原来只记"已完成步骤 id", 恢复时步骤输出被填成占位文字
"[restored from checkpoint]", 下游 context / ${step:x:output} / when contains / 最终合并拿到的都是占位文字。
现在 (version 2) 同时保存每个成功步骤的真实输出; 恢复时只恢复带真实输出的步骤, 其余 (含旧格式 checkpoint)
视为失效, 照常重跑。
"""
from __future__ import annotations

import json
import time
from typing import Dict, Set

from blackboard import Blackboard

CKPT_VERSION = 2
# 单步输出超过此长度不落盘 (避免 checkpoint 过大); 没落盘的步骤下次重跑, 不会用截断/占位文本冒充
MAX_SAVED_OUTPUT = 2_000_000


class _CheckpointMixin:
    """DAGOrchestrator 的 checkpoint 分片: save / load / clear."""

    def _save_checkpoint(self, plan_id: str, completed_ids: Set[str], results: Dict = None):
        """保存已完成步骤及其真实输出。results: {step_id: StepResult}。"""
        try:
            outputs = {}
            for sid in completed_ids:
                r = (results or {}).get(sid)
                out = getattr(r, "output", None)
                if isinstance(out, str) and len(out) <= MAX_SAVED_OUTPUT:
                    outputs[sid] = {"output": out, "label": getattr(r, "label", "") or sid,
                                    "elapsed": float(getattr(r, "elapsed", 0.0) or 0.0),
                                    "attempts": int(getattr(r, "attempts", 1) or 1)}
            ckpt = {
                "version": CKPT_VERSION,
                "plan_id": plan_id,
                "completed": sorted(outputs),
                "outputs": outputs,
                "blackboard": self.blackboard.to_dict(),  # type: ignore[attr-defined]
                "timestamp": time.time(),
            }
            p = self._ckpt_dir / f"{plan_id}.json"  # type: ignore[attr-defined]
            # [P0-#6] fsync 原子落盘, 崩溃/断电不留半文件
            from lib.atomic_io import atomic_write_json
            atomic_write_json(p, ckpt)
        except Exception:
            pass

    def _load_checkpoint_records(self, plan_id: str) -> Dict[str, dict]:
        """→ {step_id: {output, label, elapsed, attempts}}, 只含带真实输出的步骤; 无/旧格式 checkpoint 返回空。"""
        try:
            p = self._ckpt_dir / f"{plan_id}.json"  # type: ignore[attr-defined]
            if not p.exists():
                return {}
            data = json.loads(p.read_text())
            if data.get("plan_id") != plan_id or int(data.get("version", 1) or 1) < CKPT_VERSION:
                return {}
            outputs = data.get("outputs") or {}
            recs = {sid: rec for sid, rec in outputs.items()
                    if sid in set(data.get("completed") or []) and isinstance(rec, dict)
                    and isinstance(rec.get("output"), str)}
            if recs:
                bb_data = data.get("blackboard", {})
                if bb_data:
                    self.blackboard = Blackboard.from_dict(bb_data)  # type: ignore[attr-defined]
            return recs
        except Exception:
            return {}

    def _load_checkpoint(self, plan_id: str) -> Set[str]:
        """兼容旧接口: 可恢复 (带真实输出) 的步骤 id 集合。"""
        return set(self._load_checkpoint_records(plan_id))

    def clear_checkpoint(self, plan_id: str):
        """清除 checkpoint。"""
        try:
            p = self._ckpt_dir / f"{plan_id}.json"  # type: ignore[attr-defined]
            p.unlink(missing_ok=True)
        except Exception:
            pass
