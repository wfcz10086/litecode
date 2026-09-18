"""core/orchestrator — 生产级 DAG 状态机编排器 (#52 T-52b 拆包).

对外 API 与拆分前 core/orchestrator.py 完全一致:
  DAGOrchestrator, DAGPlan, StepSpec, StepResult, StepStatus, StepAction,
  OrchestrationResult, PlanTemplates,
  _sse_event, _sse_content, _sse_step_status  (测试直接引用)

模块布局 (全部 ≤ 500 行硬顶):
  types.py            — 枚举 + dataclass
  helpers.py          — SSE 事件 + 占位符替换 + 原生 tool step 执行器
  execute_mixin.py    — _ExecuteMixin: execute + execute_streaming
  step_mixin.py       — _StepMixin: _execute_step + when + build_context + failure eval + fire_step_done
  critic_mixin.py     — _CriticMixin: 抽取 CRITICAL + 执行审查
  checkpoint_mixin.py — _CheckpointMixin: save / load / clear
  merge_mixin.py      — _MergeMixin: merge_results + detailed + summary
  dag.py              — DAGOrchestrator 装配 + __init__ + topological_sort + make_forwarder
  templates.py        — PlanTemplates
"""
from .dag import DAGOrchestrator
from .helpers import _sse_content, _sse_event, _sse_step_status
from .templates import PlanTemplates
from .types import (
    DAGPlan,
    OrchestrationResult,
    StepAction,
    StepResult,
    StepSpec,
    StepStatus,
)

__all__ = [
    "DAGOrchestrator",
    "DAGPlan",
    "OrchestrationResult",
    "PlanTemplates",
    "StepAction",
    "StepResult",
    "StepSpec",
    "StepStatus",
    "_sse_content",
    "_sse_event",
    "_sse_step_status",
]
