"""orchestrator/dag.py — DAGOrchestrator 类装配 (mixin 组合 + __init__ + 拓扑/forwarder)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from blackboard import Blackboard

from .checkpoint_mixin import _CheckpointMixin
from .critic_mixin import _CriticMixin
from .execute_mixin import _ExecuteMixin
from .helpers import _sse_step_status
from .merge_mixin import _MergeMixin
from .step_mixin import _StepMixin
from .types import StepSpec


class DAGCycleError(ValueError):
    """DAG 存在循环依赖. 拓扑排序无法进行, 应在执行前 raise."""


class DAGOrchestrator(
    _ExecuteMixin,
    _StepMixin,
    _CriticMixin,
    _CheckpointMixin,
    _MergeMixin,
):
    """
    生产级 DAG 状态机编排器。

    特性:
    - 拓扑排序执行，同层步骤并行
    - 失败自动评估: retry / retry_alt / skip / abort
    - 共享黑板替代文本截断传递
    - Critic Agent 强制审查
    - 状态快照: 失败后恢复跳过已完成步骤

    使用:
        orch = DAGOrchestrator(run_subagent_fn)
        plan = DAGPlan(steps=[...])
        result = await orch.execute(plan, sse_emit)
    """

    def __init__(self, run_subagent_fn: Callable,
                 workspace: Optional[Path] = None,
                 checkpoint_dir: Optional[Path] = None,
                 parent_sid: Optional[str] = None,
                 tracer=None,
                 pause_check_fn=None,
                 step_done_cb=None,
                 step_begin_cb=None):
        """
        run_subagent_fn: async (task, agent_type, context, sse_emit, parent_sid=None) -> str
        parent_sid: 父会话 sid, 用于中断传播 (v1.0)
        tracer: itrace tracer, 用于 DAG_STEP / CRITIC / BLACKBOARD_WRITE 日志 (v1.1)
        pause_check_fn: async (node_id) -> None, 断点钩子 (v1.2 P46)
        step_done_cb: (StepResult) -> awaitable or None, 每步完成后回调 (v1.3 实时进度)
        """
        self._run = run_subagent_fn
        self._pause_check = pause_check_fn
        self._step_done_cb = step_done_cb
        self._step_begin_cb = step_begin_cb  # [2026-09-05] 步开始回调: 实时登记"运行中"步 + 刷心跳
        self._workspace = workspace or Path("/tmp/orchestrator")
        self._ckpt_dir = checkpoint_dir or (self._workspace / ".orchestrator_checkpoints")
        self._ckpt_dir.mkdir(parents=True, exist_ok=True)
        self.blackboard = Blackboard()
        self._parent_sid = parent_sid  # [v1.0.5] DAG 子代理继承父中断标记
        # [v1.1] tracer: 若未传入则用 NullTracer, 所有调用都 no-op, 不影响性能
        if tracer is None:
            try:
                from itrace import NullTracer
                tracer = NullTracer()
            except Exception:
                tracer = type("_N", (), {k: (lambda *a, **kw: None) for k in
                    ("dag_step_begin","dag_step_end","critic_event","blackboard_write")})()
        self._tracer = tracer
        self._remaining_budget = 0
        self._used_budget = 0
        self._per_step_budget = 0

    # ─────────────────────────────────────────────────────
    # DAG 拓扑排序 (无 self 依赖但保留为方法便于测试猴补)
    # ─────────────────────────────────────────────────────

    def _topological_sort(self, steps: List[StepSpec]) -> List[List[StepSpec]]:
        """
        拓扑排序，返回分层列表。同层步骤可并行执行。
        """
        step_map = {s.id: s for s in steps}
        in_degree = {s.id: 0 for s in steps}
        adj: Dict[str, Set[str]] = {s.id: set() for s in steps}

        for s in steps:
            for dep in s.depends_on:
                if dep in adj:
                    adj[dep].add(s.id)
                    in_degree[s.id] += 1

        layers = []
        remaining = set(in_degree.keys())

        while remaining:
            layer_ids = [nid for nid in remaining if in_degree[nid] == 0]
            if not layer_ids:
                cycle_nodes = sorted(remaining)
                raise DAGCycleError(
                    f"DAG cycle detected among nodes: {cycle_nodes}. "
                    f"Check `depends_on` fields for circular references."
                )

            layers.append([step_map[nid] for nid in layer_ids])
            for nid in layer_ids:
                remaining.discard(nid)
                for neighbor in adj.get(nid, set()):
                    in_degree[neighbor] -= 1

        return layers

    # ─────────────────────────────────────────────────────
    # SSE 转发 (test_node_stderr_stream.py 直接调用 _make_forwarder)
    # ─────────────────────────────────────────────────────

    def _make_forwarder(self, node_id: str, label: str,
                        sse_emit: Optional[Callable]) -> Optional[Callable]:
        """创建带 label 的 SSE 转发器，透传 chunk 并额外 emit 节点级增量。"""
        if not sse_emit:
            return None
        async def _forward(chunk: str):
            await sse_emit(chunk)
            try:
                raw = chunk.strip()
                if raw.startswith("data:"):
                    raw = raw[5:].strip()
                parsed = json.loads(raw)
                delta = parsed["choices"][0]["delta"]
                parts = []
                if delta.get("content"):
                    parts.append(delta["content"])
                if delta.get("reasoning"):
                    parts.append(delta["reasoning"])
                text = "".join(parts)
                if text:
                    await sse_emit(_sse_step_status(
                        node_id, label, "running", "",
                        phase="chunk", output_delta=text,
                    ))
                te = delta.get("task_exec") or {}
                if te.get("status") == "done":
                    d = te.get("detail", "")
                    if isinstance(d, str) and (d.startswith("ERROR:") or d.startswith("[STDERR")):
                        await sse_emit(_sse_step_status(
                            node_id, label, "running", "",
                            phase="chunk", output_delta=d, stream="stderr",
                        ))
            except Exception:
                pass
        return _forward


# uuid 保留导入以维持模块内 helper 兼容 (未来可能新增引用)
_ = uuid
