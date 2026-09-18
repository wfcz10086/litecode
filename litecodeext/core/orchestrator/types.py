"""orchestrator/types.py — 状态枚举 + 步骤/计划/结果 dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class StepStatus(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    SUCCESS    = "success"
    FAILED     = "failed"
    RETRYING   = "retrying"
    SKIPPED    = "skipped"
    CANCELLED  = "cancelled"


class StepAction(str, Enum):
    """Supervisor 对失败步骤的决策"""
    RETRY       = "retry"        # 原参数重试
    RETRY_ALT   = "retry_alt"    # 换策略重试
    SKIP        = "skip"         # 跳过此步
    ABORT       = "abort"        # 终止整个 DAG
    CONTINUE    = "continue"     # 忽略错误继续


@dataclass
class StepSpec:
    """DAG 中的一个执行步骤"""
    id: str                              # 唯一 ID（如 "step_1"）
    task: str                            # 任务描述
    agent_type: str = "coder"            # explorer/researcher/coder/analyst/tester/shell/writer/critic/tool
    label: str = ""                      # 显示标签
    depends_on: List[str] = field(default_factory=list)  # 依赖的 step ID
    timeout: int = 300
    max_retries: int = 2                 # 最大重试次数
    context: str = ""
    critical: bool = True                # 是否关键步骤（失败是否阻塞后续）
    retry_strategy: str = ""             # 重试时的替代策略描述
    # [Round 4 · 2026-07-24] 原生工具步 — agent_type=="tool" 时不走 LLM,
    # 直接 plugins.registry.call(tool_name, tool_args) 拿结果.
    # tool_args 里的 str 值可含 ${step:<id>:output} 占位符, 由 orchestrator 用前
    # 步 output 填充 (最简 substitution, 不解析 JSONPath).
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    # [#2 2026-09-05] 子 DAG 步: agent_type=="dag" 时, 加载并运行 dag_name 指向的另一个
    # 已保存 DAG, 把它的 final_output 当本步 output。用于流水线复用/模块化。带递归深度上限。
    dag_name: str = ""
    # [v1.3] 多条件判断: 多条 AND 关系, 任一不满足 → 本步跳过 (status=SKIPPED)
    # 支持:
    #   success:<sid>              前步 sid 成功
    #   failure:<sid>              前步 sid 失败
    #   contains:<sid>:<keyword>   前步 sid.output 含 keyword
    #   !contains:<sid>:<keyword>  前步 sid.output 不含 keyword
    #   !success:<sid> / !failure:<sid> 取反
    when: List[str] = field(default_factory=list)


@dataclass
class StepResult:
    """步骤执行结果"""
    step_id: str
    label: str
    agent_type: str
    task: str
    status: StepStatus
    output: str = ""
    error: str = ""
    elapsed: float = 0.0
    attempts: int = 1
    error_stack: List[str] = field(default_factory=list)  # 每次重试的错误记录


@dataclass
class DAGPlan:
    """DAG 执行计划"""
    steps: List[StepSpec]
    auto_critic: bool = True        # 最后自动插入 Critic
    critic_checklist: str = ""      # Critic 审查清单
    total_token_budget: int = 0     # 0 = 不限制 (向后兼容)
    max_parallel_steps: int = 4     # [P0-#9] 同层并行上限, 0/负 → 无限制


@dataclass
class OrchestrationResult:
    """编排结果"""
    plan_id: str
    steps: List[StepResult]
    final_output: str
    total_elapsed: float
    success: bool
    blackboard_snapshot: dict = field(default_factory=dict)
    tokens_used: int = 0            # P3-c
    tokens_remaining: int = 0       # P3-c
