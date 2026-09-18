"""
multi_agent.py — v1.0 多代理编排器（升级版）
=============================================
在 v1.0 基础上整合:
  - DAG 状态机编排 (orchestrator.py)
  - 共享黑板 (blackboard.py)
  - Critic Agent 审查
  - 状态快照恢复

向后兼容: 原有 AgentMode.PIPELINE/PARALLEL/COMPETITIVE 接口不变。
新增: AgentMode.DAG 模式，使用 DAGOrchestrator。

SSE 流式输出格式完全不变。
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

# v1.0 新模块: lazy import, 缺失时 DAG 模式不可用但不影响其他模式
try:
    from blackboard import Blackboard, EntryType
    HAS_BLACKBOARD = True
except ImportError:
    HAS_BLACKBOARD = False
    class Blackboard:
        def __init__(self): self._s = {}
        def put(self, *a, **kw): pass
        def to_context_string(self, **kw): return ""
        def snapshot(self): return {}
    class EntryType:
        RESULT = "result"

try:
    from orchestrator import (
        DAGOrchestrator, DAGPlan, StepSpec, StepStatus,
        OrchestrationResult as DAGResult, PlanTemplates,
    )
    HAS_ORCHESTRATOR = True
except ImportError:
    HAS_ORCHESTRATOR = False


# ═══════════════════════════════════════════════════════
# 类型（保持 v1.0 兼容）
# ═══════════════════════════════════════════════════════

class AgentMode(str, Enum):
    PIPELINE    = "pipeline"
    PARALLEL    = "parallel"
    COMPETITIVE = "competitive"
    DAG         = "dag"           # v1.0 新增


@dataclass
class AgentSpec:
    """单个 Agent 的规格（兼容 v1.0）"""
    task: str
    agent_type: str = "coder"
    context: str = ""
    label: str = ""
    timeout: int = 300
    ingest_previous: bool = True
    eval_criteria: str = ""
    # v1.0 新增
    depends_on: List[str] = field(default_factory=list)
    max_retries: int = 2
    critical: bool = True
    step_id: str = ""
    # [v1.3] 多条件判断: 透传到 StepSpec.when, 见 orchestrator.StepSpec docstring
    when: List[str] = field(default_factory=list)
    # [Round 4 · 2026-07-24] 原生工具步 — agent_type=="tool" 时直接透传给 StepSpec
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    dag_name: str = ""  # [#2 2026-09-05] 子 DAG 步: 要运行的子 DAG 名


@dataclass
class AgentResult:
    """单个 Agent 的执行结果"""
    label: str
    agent_type: str
    task: str
    output: str
    elapsed: float
    success: bool
    error: str = ""
    score: float = 0.0
    # v1.0 新增
    attempts: int = 1
    error_stack: List[str] = field(default_factory=list)


@dataclass
class OrchestrationResult:
    """整体编排结果"""
    mode: AgentMode
    agents: List[AgentResult]
    final_output: str
    total_elapsed: float
    success: bool
    # v1.0 新增
    blackboard_snapshot: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════
# SSE 事件（完全不变）
# ═══════════════════════════════════════════════════════

def _sse_agent_status(label: str, status: str, detail: str = "",
                      model: str = "openclaw") -> str:
    d = {
        "id": f"ma-{uuid.uuid4().hex[:6]}",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {
                "agent_status": {
                    "label": label, "status": status, "detail": detail,
                }
            },
            "finish_reason": None
        }]
    }
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def _sse_agent_output(label: str, text: str, model: str = "openclaw") -> str:
    d = {
        "id": f"ma-{uuid.uuid4().hex[:6]}",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"content": text, "agent_label": label},
            "finish_reason": None
        }]
    }
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def _sse_content(text: str, model: str = "openclaw") -> str:
    d = {
        "id": f"ma-{uuid.uuid4().hex[:6]}",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]
    }
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


# ═══════════════════════════════════════════════════════
# MultiAgentOrchestrator v1.0
# ═══════════════════════════════════════════════════════

class MultiAgentOrchestrator:
    """
    多代理编排器 v1.0。

    新增 DAG 模式，同时保持 Pipeline/Parallel/Competitive 向后兼容。
    """

    def __init__(self, run_subagent_fn: Callable, workspace=None, parent_sid: str = None,
                 tracer=None, pause_check_fn=None, step_done_cb=None, step_begin_cb=None):
        self._run = run_subagent_fn
        self._workspace = workspace
        self.blackboard = Blackboard()
        self._parent_sid = parent_sid  # [v1.0.5] 传递给 DAGOrchestrator 做中断传播
        self._tracer = tracer  # [v1.1] DAG/Critic 事件写入 iteration_full.log
        self._pause_check_fn = pause_check_fn  # [P46] 节点断点钩子
        self._step_done_cb = step_done_cb  # [v1.3] DAG 步骤完成回调 → 给上层实时进度
        self._step_begin_cb = step_begin_cb  # [2026-09-05] 步开始回调 → 实时登记运行中步

    # ── 统一入口 ──────────────────────────────────────

    async def run(
        self,
        mode: AgentMode,
        specs: List[AgentSpec],
        sse_emit: Optional[Callable] = None,
        auto_critic: bool = False,
    ) -> OrchestrationResult:
        t0 = time.time()

        if mode == AgentMode.DAG:
            result = await self._run_dag(specs, sse_emit, auto_critic)
        elif mode == AgentMode.PIPELINE:
            results = await self._run_pipeline(specs, sse_emit)
            if auto_critic and any(r.success for r in results):
                critic_result = await self._run_inline_critic(results, sse_emit)
                if critic_result:
                    results.append(critic_result)
            result = self._to_orchestration_result(mode, results, t0)
        elif mode == AgentMode.PARALLEL:
            results = await self._run_parallel(specs, sse_emit)
            result = self._to_orchestration_result(mode, results, t0)
        elif mode == AgentMode.COMPETITIVE:
            results = await self._run_competitive(specs, sse_emit)
            result = self._to_orchestration_result(mode, results, t0)
        else:
            results = await self._run_parallel(specs, sse_emit)
            result = self._to_orchestration_result(mode, results, t0)

        return result

    async def run_streaming(
        self,
        mode: AgentMode,
        specs: List[AgentSpec],
        auto_critic: bool = False,
    ) -> AsyncGenerator[str, None]:
        """流式版本。"""
        buffer: List[str] = []

        async def _emit(chunk: str):
            buffer.append(chunk)

        holder: List[OrchestrationResult] = []

        async def _run_and_store():
            r = await self.run(mode, specs, _emit, auto_critic)
            holder.append(r)

        task = asyncio.create_task(_run_and_store())

        while not task.done() or buffer:
            if buffer:
                yield buffer.pop(0)
            else:
                await asyncio.sleep(0.05)

        try:
            await task
        except Exception as e:
            yield _sse_content(f"\n[MultiAgent Error] {e}")

        if holder:
            yield _sse_content(self._format_summary(holder[0]))

    # ── DAG 模式（v1.0 新增）──────────────────────────

    async def _run_dag(
        self,
        specs: List[AgentSpec],
        sse_emit: Optional[Callable],
        auto_critic: bool = True,
    ) -> OrchestrationResult:
        """AgentSpec → StepSpec 转换，委托给 DAGOrchestrator。"""
        if not HAS_ORCHESTRATOR:
            # Fallback: DAG 模式不可用时退化为 Pipeline
            results = await self._run_pipeline(specs, sse_emit)
            return self._to_orchestration_result(AgentMode.PIPELINE, results, time.time())
        dag_orch = DAGOrchestrator(
            self._run, workspace=self._workspace,
            parent_sid=self._parent_sid,  # [v1.0.5] 继承父中断
            tracer=self._tracer,           # [v1.1] 继承 tracer
            pause_check_fn=self._pause_check_fn,  # [P46] 断点钩子透传
            step_done_cb=self._step_done_cb,  # [v1.3] 实时进度回调透传
            step_begin_cb=getattr(self, '_step_begin_cb', None),  # [2026-09-05] 步开始回调透传
        )

        # 转换 AgentSpec → StepSpec
        dag_steps = []
        for i, spec in enumerate(specs):
            dag_steps.append(StepSpec(
                id=spec.step_id or f"step_{i+1}",
                task=spec.task,
                agent_type=spec.agent_type,
                label=spec.label or f"Agent-{i+1}[{spec.agent_type}]",
                depends_on=spec.depends_on,
                timeout=spec.timeout,
                max_retries=spec.max_retries,
                context=spec.context,
                critical=spec.critical,
                when=list(getattr(spec, "when", []) or []),  # [v1.3] 条件透传
                # [Round 4 · 2026-07-24] 原生工具步透传
                tool_name=getattr(spec, "tool_name", "") or "",
                tool_args=dict(getattr(spec, "tool_args", {}) or {}),
                dag_name=getattr(spec, "dag_name", "") or "",
            ))

        plan = DAGPlan(steps=dag_steps, auto_critic=auto_critic)
        dag_result = await dag_orch.execute(plan, sse_emit)

        # DAGResult → OrchestrationResult
        agents = []
        for sr in dag_result.steps:
            agents.append(AgentResult(
                label=sr.label, agent_type=sr.agent_type, task=sr.task,
                output=sr.output, elapsed=sr.elapsed,
                success=(sr.status == StepStatus.SUCCESS),
                error=sr.error, attempts=sr.attempts,
                error_stack=sr.error_stack,
            ))

        return OrchestrationResult(
            mode=AgentMode.DAG,
            agents=agents,
            final_output=dag_result.final_output,
            total_elapsed=dag_result.total_elapsed,
            success=dag_result.success,
            blackboard_snapshot=dag_result.blackboard_snapshot,
        )

    # ── Pipeline 模式（升级版：加入黑板 + 错误栈弹回）──

    async def _run_pipeline(
        self,
        specs: List[AgentSpec],
        sse_emit: Optional[Callable],
    ) -> List[AgentResult]:
        results: List[AgentResult] = []
        previous_output = ""
        previous_success = True
        previous_label = ""

        for i, spec in enumerate(specs):
            label = spec.label or f"Agent-{i+1}[{spec.agent_type}]"
            if sse_emit:
                await sse_emit(_sse_agent_status(label, "running",
                    f"第{i+1}/{len(specs)}步: {spec.task[:60]}"))

            # 构建 context: 黑板 + 前步输出
            context = spec.context
            bb_ctx = self.blackboard.to_context_string(max_chars=2000)

            if spec.ingest_previous and previous_output:
                max_prev = 8000 if spec.agent_type in ("coder", "tester", "writer") else 3000
                if not previous_success:
                    context = (
                        f"[警告: 前一步骤({previous_label})执行失败]\n"
                        f"失败原因: {previous_output[:500]}\n"
                        f"你需要独立完成任务，不要依赖前一步的输出。\n\n"
                        f"{bb_ctx}\n\n{context}"
                    )
                else:
                    context = (
                        f"[前一步骤的输出]\n{previous_output[:max_prev]}\n\n"
                        f"{bb_ctx}\n\n{context}"
                    )
            elif bb_ctx:
                context = f"{bb_ctx}\n\n{context}"

            t0 = time.time()
            error_stack = []
            success = False
            output = ""

            for attempt in range(spec.max_retries + 1):
                try:
                    output = await asyncio.wait_for(
                        self._run(spec.task, spec.agent_type, context,
                                  self._make_sse_forwarder(label, sse_emit)),
                        timeout=spec.timeout,
                    )
                    success = True
                    break
                except asyncio.TimeoutError:
                    output = f"[超时] {label} 执行超过 {spec.timeout}s"
                    error_stack.append(f"attempt {attempt+1}: timeout")
                except Exception as e:
                    output = f"[异常] {label}: {e}"
                    error_stack.append(f"attempt {attempt+1}: {e}")

                if attempt < spec.max_retries and sse_emit:
                    await sse_emit(_sse_agent_status(label, "retrying",
                        f"⚠️ 重试 {attempt+2}/{spec.max_retries+1}"))

            elapsed = time.time() - t0

            # 写入黑板
            if success:
                self.blackboard.put(
                    f"pipeline:step_{i}:output", output[:2000],
                    EntryType.RESULT, source=label,
                )

            result = AgentResult(
                label=label, agent_type=spec.agent_type, task=spec.task,
                output=output, elapsed=elapsed, success=success,
                error="" if success else output[:300],
                attempts=len(error_stack) + (1 if success else 0),
                error_stack=error_stack,
            )
            results.append(result)
            previous_output = output
            previous_success = success
            previous_label = label

            if sse_emit:
                icon = "✅" if success else "❌"
                await sse_emit(_sse_agent_status(label, "done" if success else "error",
                    f"{icon} {elapsed:.1f}s — {output[:80].replace(chr(10),' ')}"))

            # 关键步骤失败处理
            if not success and i == 0 and len(specs) > 2:
                if sse_emit:
                    await sse_emit(_sse_agent_status("系统提示", "running",
                        "⚠️ 第一步失败，后续步骤将独立执行"))

        return results

    # ── Parallel 模式 ────────────────────────────────

    async def _run_parallel(
        self,
        specs: List[AgentSpec],
        sse_emit: Optional[Callable],
    ) -> List[AgentResult]:
        if sse_emit:
            await sse_emit(_sse_content(f"\n🔀 启动 {len(specs)} 个并行代理...\n"))

        async def _run_one(i: int, spec: AgentSpec) -> AgentResult:
            label = spec.label or f"Agent-{i+1}[{spec.agent_type}]"
            if sse_emit:
                await sse_emit(_sse_agent_status(label, "running",
                    f"▶ {spec.task[:60]}"))
            t0 = time.time()
            try:
                output = await asyncio.wait_for(
                    self._run(spec.task, spec.agent_type, spec.context,
                              self._make_sse_forwarder(label, sse_emit)),
                    timeout=spec.timeout,
                )
                success = True
                error = ""
            except asyncio.TimeoutError:
                output = f"[timeout after {spec.timeout}s]"
                success = False
                error = "timeout"
            except Exception as e:
                output = f"[error: {e}]"
                success = False
                error = str(e)

            elapsed = time.time() - t0
            if success:
                self.blackboard.put(
                    f"parallel:agent_{i}:output", output[:2000],
                    EntryType.RESULT, source=label,
                )

            if sse_emit:
                icon = "✅" if success else "❌"
                await sse_emit(_sse_agent_status(label, "done" if success else "error",
                    f"{icon} {elapsed:.1f}s"))
            return AgentResult(
                label=label, agent_type=spec.agent_type, task=spec.task,
                output=output, elapsed=elapsed, success=success, error=error,
            )

        tasks = [_run_one(i, spec) for i, spec in enumerate(specs)]
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for i, r in enumerate(raw):
            if isinstance(r, Exception):
                label = specs[i].label or f"Agent-{i+1}[{specs[i].agent_type}]"
                results.append(AgentResult(
                    label=label, agent_type=specs[i].agent_type, task=specs[i].task,
                    output=f"[异常] {r}", elapsed=0, success=False, error=str(r),
                ))
            else:
                results.append(r)
        return results

    # ── Competitive 模式 ─────────────────────────────

    async def _run_competitive(
        self,
        specs: List[AgentSpec],
        sse_emit: Optional[Callable],
    ) -> List[AgentResult]:
        if sse_emit:
            await sse_emit(_sse_content(
                f"\n🏆 竞争模式: {len(specs)} 个代理同时解决同一任务...\n"))
        results = await self._run_parallel(specs, sse_emit)
        for r in results:
            if r.success:
                length_score = min(len(r.output) / 500, 5.0)
                r.score = 3.0 + length_score
            else:
                r.score = 0.0
        results.sort(key=lambda r: r.score, reverse=True)
        if sse_emit and results:
            best = results[0]
            await sse_emit(_sse_content(
                f"\n🏆 最佳: {best.label} (score={best.score:.1f})\n"))
        return results

    # ── Inline Critic（Pipeline 模式用）──────────────

    async def _run_inline_critic(
        self,
        results: List[AgentResult],
        sse_emit: Optional[Callable],
    ) -> Optional[AgentResult]:
        successful = [r for r in results if r.success]
        if not successful:
            return None

        if sse_emit:
            await sse_emit(_sse_agent_status("Critic", "running", "审查中..."))

        review = "\n".join(
            f"### [{r.label}]\n任务: {r.task[:100]}\n输出: {r.output[:1000]}\n"
            for r in successful
        )
        critic_task = (
            f"你是代码审查专家。审查以下输出:\n{review}\n\n"
            f"检查: 变量未定义/API 错误/路径不一致/安全漏洞/依赖缺失。\n"
            f"输出: ✅ 通过 / ❌ 问题+修复建议"
        )

        t0 = time.time()
        try:
            output = await asyncio.wait_for(
                self._run(critic_task, "critic", "", None),
                timeout=120,
            )
            success = True
        except Exception as e:
            output = f"Critic 失败: {e}"
            success = False
        elapsed = time.time() - t0

        if sse_emit:
            await sse_emit(_sse_agent_status(
                "Critic", "done" if success else "error", f"{'✅' if success else '❌'} {elapsed:.1f}s"))

        return AgentResult(
            label="Critic 审查", agent_type="critic",
            task="代码审查", output=output,
            elapsed=elapsed, success=success,
        )

    # ── 工具方法 ──────────────────────────────────────

    def _make_sse_forwarder(self, label: str,
                            sse_emit: Optional[Callable]) -> Optional[Callable]:
        if not sse_emit:
            return None
        async def _forward(chunk: str):
            if '"task_exec"' in chunk:
                try:
                    data = chunk.replace("data: ", "").strip()
                    if data == "[DONE]":
                        return
                    obj = json.loads(data)
                    te = obj["choices"][0]["delta"].get("task_exec", {})
                    te["agent_label"] = label
                    obj["choices"][0]["delta"]["task_exec"] = te
                    await sse_emit(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n")
                    return
                except Exception:
                    pass
            await sse_emit(chunk)
        return _forward

    def _to_orchestration_result(self, mode: AgentMode,
                                  results: List[AgentResult],
                                  t0: float) -> OrchestrationResult:
        final = self._merge_results(mode, results)
        return OrchestrationResult(
            mode=mode, agents=results,
            final_output=final,
            total_elapsed=time.time() - t0,
            success=any(r.success for r in results),
            blackboard_snapshot=self.blackboard.snapshot(),
        )

    def _merge_results(self, mode: AgentMode,
                       results: List[AgentResult]) -> str:
        if not results:
            return "(no results)"

        if mode == AgentMode.PIPELINE:
            last = results[-1]
            if last.success:
                return last.output
            for r in reversed(results):
                if r.success:
                    return (
                        f"[管道部分完成] 在 {last.label} 失败: {last.error}\n"
                        f"最后成功 ({r.label}):\n\n{r.output}"
                    )
            return "[管道全部失败]\n" + "\n".join(
                f"- {r.label}: {r.error}" for r in results
            )

        elif mode == AgentMode.COMPETITIVE:
            successful = [r for r in results if r.success]
            if successful:
                return max(successful, key=lambda r: r.score).output
            return "[竞争全部失败]\n" + "\n".join(
                f"- {r.label}: {r.error}" for r in results
            )

        else:
            parts = []
            for r in results:
                if r.success:
                    parts.append(f"### [{r.label}]\n{r.output}")
                else:
                    parts.append(f"### [{r.label}] ❌ {r.error}")
            return "\n\n---\n\n".join(parts)

    def _format_summary(self, result: OrchestrationResult) -> str:
        lines = [
            f"\n\n---",
            f"**编排完成** · `{result.mode}` · {result.total_elapsed:.1f}s"
            f" · 黑板 {len(result.blackboard_snapshot.get('entries', {}))} 条",
        ]
        for r in result.agents:
            icon = "✅" if r.success else "❌"
            retry_info = f" ({r.attempts}次)" if r.attempts > 1 else ""
            lines.append(f"- {icon} `{r.label}` ({r.agent_type}) {r.elapsed:.1f}s{retry_info}")
        return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════
# 预定义编排模板（升级版）
# ═══════════════════════════════════════════════════════

class OrchestratorTemplates:

    @staticmethod
    def code_with_tests(task: str, language: str = "Python") -> tuple:
        """代码+测试 Pipeline，自带 Critic。"""
        return AgentMode.PIPELINE, [
            AgentSpec(task=f"用{language}实现:\n{task}",
                      agent_type="coder", label="代码生成", ingest_previous=False),
            AgentSpec(task="写单元测试并运行",
                      agent_type="tester", label="单元测试", ingest_previous=True),
            AgentSpec(task="运行所有测试，修复至全部通过",
                      agent_type="tester", label="验证运行", ingest_previous=True),
        ]

    @staticmethod
    def code_with_tests_dag(task: str, language: str = "Python") -> tuple:
        """DAG 版: 代码+测试 + 自动 Critic 审查。"""
        return AgentMode.DAG, [
            AgentSpec(step_id="code", task=f"用{language}实现:\n{task}",
                      agent_type="coder", label="代码生成", max_retries=2),
            AgentSpec(step_id="test", task="写单元测试并运行",
                      agent_type="tester", label="测试验证",
                      depends_on=["code"], max_retries=1),
        ]

    @staticmethod
    def fullstack_dag(requirements: str) -> tuple:
        """DAG 全栈项目: 设计→前端‖后端→集成→审查"""
        return AgentMode.DAG, [
            AgentSpec(step_id="design", task=f"技术方案设计:\n{requirements}",
                      agent_type="analyst", label="架构设计", timeout=120),
            AgentSpec(step_id="backend", task="实现后端 API",
                      agent_type="coder", label="后端开发",
                      depends_on=["design"], timeout=300),
            AgentSpec(step_id="frontend", task="实现前端页面",
                      agent_type="coder", label="前端开发",
                      depends_on=["design"], timeout=300),
            AgentSpec(step_id="integrate", task="集成前后端 + 端到端测试",
                      agent_type="tester", label="集成测试",
                      depends_on=["backend", "frontend"], timeout=180),
        ]


# ═══════════════════════════════════════════════════════
# handle_spawn_agent（兼容 litecode_server.py 调用）
# ═══════════════════════════════════════════════════════

SPAWN_AGENT_EXTRA_PARAMS = {
    "pipeline_tasks": {
        "type": "array",
        "description": "管道模式: 串行 Agent 链。格式: [{task, agent_type, label, ...}]",
        "items": {"type": "object", "properties": {
            "task": {"type": "string"}, "agent_type": {"type": "string"},
            "label": {"type": "string"}, "context": {"type": "string"},
            "timeout": {"type": "integer"},
        }, "required": ["task", "agent_type"]},
    },
    "competitive_tasks": {
        "type": "array",
        "description": "竞争模式: 同任务多 Agent，选最佳。格式: [{task, agent_type, label}]",
        "items": {"type": "object", "properties": {
            "task": {"type": "string"}, "agent_type": {"type": "string"},
            "label": {"type": "string"},
        }, "required": ["task", "agent_type"]},
    },
    "dag_tasks": {
        "type": "array",
        "description": (
            "DAG 模式: 有向无环图编排，支持依赖、并行、自动审查。"
            "格式: [{step_id, task, agent_type, label, depends_on?, max_retries?}]"
        ),
        "items": {"type": "object", "properties": {
            "step_id": {"type": "string"}, "task": {"type": "string"},
            "agent_type": {"type": "string"}, "label": {"type": "string"},
            "depends_on": {"type": "array", "items": {"type": "string"}},
            "max_retries": {"type": "integer"},
        }, "required": ["step_id", "task", "agent_type"]},
    },
}


async def handle_spawn_agent_enhanced(
    args: dict,
    run_subagent_fn: Callable,
    sse_emit: Optional[Callable] = None,
    session_id: str = None,
    tracer=None,   # [v1.1] 传 itrace tracer 让 DAG_STEP / CRITIC 进日志
) -> str:
    # [v1.0.5] session_id 作为 parent_sid 传给 DAG orchestrator, 中断能向下穿透
    orch = MultiAgentOrchestrator(run_subagent_fn, parent_sid=session_id, tracer=tracer)

    # DAG 模式（v1.0 新增）
    dag_tasks = args.get("dag_tasks")
    if dag_tasks and isinstance(dag_tasks, list):
        specs = [AgentSpec(**{k: v for k, v in t.items()
                              if k in AgentSpec.__dataclass_fields__})
                 for t in dag_tasks]
        result = await orch.run(AgentMode.DAG, specs, sse_emit, auto_critic=True)
        return _format_result(result)

    # Pipeline 模式
    pipeline_tasks = args.get("pipeline_tasks")
    if pipeline_tasks and isinstance(pipeline_tasks, list):
        specs = [AgentSpec(**{k: v for k, v in t.items()
                              if k in AgentSpec.__dataclass_fields__})
                 for t in pipeline_tasks]
        result = await orch.run(AgentMode.PIPELINE, specs, sse_emit, auto_critic=True)
        return _format_result(result)

    # Competitive 模式
    competitive_tasks = args.get("competitive_tasks")
    if competitive_tasks and isinstance(competitive_tasks, list):
        specs = [AgentSpec(**{k: v for k, v in t.items()
                              if k in AgentSpec.__dataclass_fields__})
                 for t in competitive_tasks]
        result = await orch.run(AgentMode.COMPETITIVE, specs, sse_emit)
        return _format_result(result)

    # Parallel 模式
    parallel_tasks = args.get("parallel_tasks")
    if parallel_tasks and isinstance(parallel_tasks, list) and len(parallel_tasks) > 1:
        specs = [AgentSpec(
            task=t.get("task", ""), agent_type=t.get("agent_type", "coder"),
            context=t.get("context", ""), label=t.get("label", ""),
        ) for t in parallel_tasks]
        result = await orch.run(AgentMode.PARALLEL, specs, sse_emit)
        return _format_result(result)

    # [batch-items-2026-05] Batch 模式 — 同 agent_type, N 个独立子任务, 每个 fresh subagent
    # 区别于 parallel_tasks (任务和类型各异): batch_items 共享 base task+context,
    # 只换 item_id / item_task 后缀. 默认串行执行 (避免共写 state.json 冲突).
    # 适用: 写 N 章小说 / 批量修 issue / 批量爬 URL / 批量生成模块代码.
    batch_items = args.get("batch_items")
    if batch_items and isinstance(batch_items, list) and len(batch_items) > 0:
        base_task = args.get("task", "")
        agent_type = args.get("agent_type", "coder")
        base_context = args.get("context", "")
        total = len(batch_items)
        results = []
        for idx, item in enumerate(batch_items):
            if not isinstance(item, dict):
                item = {"task": str(item)}
            item_id = item.get("item_id") or item.get("id") or f"item_{idx+1}"
            item_task = item.get("task", "")
            item_ctx = item.get("context", "")
            full_task = (
                f"{base_task}\n\n"
                f"## 本批次第 {idx+1}/{total} 项  id={item_id}\n"
                f"{item_task}"
            ).strip()
            full_ctx = ""
            if base_context or item_ctx:
                full_ctx = f"{base_context}\n\n[item-specific]\n{item_ctx}".strip()
            if sse_emit:
                try:
                    await sse_emit({"type": "batch_progress", "data": {
                        "current": idx + 1, "total": total,
                        "item_id": item_id, "agent_type": agent_type,
                    }})
                except Exception:
                    pass
            try:
                out = await run_subagent_fn(full_task, agent_type, full_ctx, sse_emit)
                out_str = out if isinstance(out, str) else str(out)
            except Exception as e:
                out_str = f"ERROR: subagent failed: {e}"
            # 截断单项防爆 context, 大批量任务可能 100+ items
            results.append({
                "item_id": item_id,
                "output": out_str[:600],
                "is_loop_break": "[loop-detect" in out_str,
                "is_error": out_str.startswith("ERROR"),
            })
        # 格式化返回
        ok_count = sum(1 for r in results if not r["is_error"] and not r["is_loop_break"])
        parts = [
            f"[BatchItems: {total} items × {agent_type}  |  "
            f"ok={ok_count}  loop_break={sum(1 for r in results if r['is_loop_break'])}  "
            f"errors={sum(1 for r in results if r['is_error'])}]\n"
        ]
        for r in results:
            icon = "❌" if r["is_error"] else ("⛔️" if r["is_loop_break"] else "✅")
            parts.append(f"--- {icon} {r['item_id']} ---")
            parts.append(r["output"])
        return "\n".join(parts)

    # 单 Agent
    task = args.get("task", "")
    agent_type = args.get("agent_type", "coder")
    context = args.get("context", "")
    if not task:
        return "ERROR: task is required"
    return await run_subagent_fn(task, agent_type, context, sse_emit)


def _format_result(result: OrchestrationResult) -> str:
    parts = [
        f"[MultiAgent: {result.mode} · {len(result.agents)} agents · "
        f"{result.total_elapsed:.1f}s]\n"
    ]
    for r in result.agents:
        icon = "✅" if r.success else "❌"
        retry = f" ({r.attempts}次)" if r.attempts > 1 else ""
        parts.append(f"--- {icon} {r.label} ({r.elapsed:.1f}s{retry}) ---")
        parts.append(r.output[:800] if r.success else f"ERROR: {r.error}")

    parts.append("\n--- FINAL OUTPUT ---")
    parts.append(result.final_output[:2000])
    return "\n".join(parts)
