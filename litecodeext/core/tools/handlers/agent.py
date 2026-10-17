"""handlers/agent.py — spawn_agent (含 pipeline/parallel/dag 编排)."""
from __future__ import annotations

import asyncio
from typing import Any

from . import register


@register("spawn_agent")
async def h_spawn_agent(sid: str, args: dict) -> tuple[str, Any]:
    import tool_dispatch as td
    log = td.log
    # [operator 2026-10-17] 远程操作员: 校验每个 operator 都指定了 alias, 同时运行的操作员不能共用一台电脑
    from lib.agent.subagent_operator import prepare_spawn_args
    args, _op_err = prepare_spawn_args(args)
    if _op_err:
        return _op_err, None
    WORKSPACE = td.WORKSPACE
    task = args.get("task", "")
    agent_type = args.get("agent_type", "coder")
    context = args.get("context", "")
    parallel_tasks = args.get("parallel_tasks")
    pipeline_tasks = args.get("pipeline_tasks")
    mode_str = args.get("mode", "")

    try:
        from lib.agent.subagent import _run_subagent
    except ImportError:
        async def _run_subagent(t, at, ctx="", sse=None, parent_sid=None):
            return "ERROR: lib.agent.subagent not available"

    dag_tasks = args.get("dag_tasks")
    multi_tasks = dag_tasks or pipeline_tasks or parallel_tasks
    # [worktree-iso 2026-10-17] 写类子代理并行时用独立 git 工作树隔离, 结束自动合并
    from lib.agent.subagent_worktree import plan_isolation
    _iso = plan_isolation(args, single=not (multi_tasks and isinstance(multi_tasks, list)))
    # [evidence 2026-10-17] 包在工作树之外: 采集每个子代理的证据, 结果开头标 [已验证]/[未验证: 原因]
    from lib.agent.subagent_evidence import plan_evidence
    _ev = plan_evidence()
    _run_subagent = _ev.wrap(_iso.wrap(_run_subagent))

    def _with_iso(text: str) -> str:
        return _ev.decorate(text + ("\n" + _iso.summary() if _iso.summary() else ""))
    if multi_tasks and isinstance(multi_tasks, list) and len(multi_tasks) > 0:
        try:
            from multi_agent import MultiAgentOrchestrator, AgentSpec, AgentMode
            if dag_tasks:
                mode = AgentMode.DAG
            elif mode_str == "competitive":
                mode = AgentMode.COMPETITIVE
            elif pipeline_tasks:
                mode = AgentMode.PIPELINE
            else:
                mode = AgentMode.PARALLEL
            log.info(f"  \033[33m[multi-agent] {mode.value} mode, {len(multi_tasks)} agents\033[0m")
            specs = [AgentSpec(task=pt.get("task", ""), agent_type=pt.get("agent_type", "coder"),
                               context=pt.get("context", context), label=pt.get("label", ""),
                               timeout=pt.get("timeout", 300),
                               depends_on=pt.get("depends_on", []),
                               step_id=pt.get("step_id", ""),
                               max_retries=pt.get("max_retries", 2),
                               ) for pt in multi_tasks]
            orch = MultiAgentOrchestrator(run_subagent_fn=_run_subagent,
                                          workspace=WORKSPACE, parent_sid=sid)
            result = await orch.run(mode, specs, auto_critic=(mode == AgentMode.DAG))
            parts = [f"{'✅' if r.success else '❌'} {r.label} ({r.agent_type}) — {r.elapsed:.1f}s" for r in result.agents]
            header = f"\n--- {mode.value} 编排完成 ({result.total_elapsed:.1f}s, {sum(1 for r in result.agents if r.success)}/{len(result.agents)} 成功) ---\n" + "\n".join(parts) + "\n---\n\n"
            combined = header + result.final_output
            if sid and td.HAS_MEMORY:
                try:
                    mgr = td._mem_get(workspace=WORKSPACE, session_id=sid, vllm_url=td.BACKEND_URL,
                                      model_id=td.MODEL_ID, api_key=td.API_KEY, context_window=td.CONTEXT_WINDOW)
                    mgr.save("Completed Work", f"[{mode.value}] {len(multi_tasks)} agents done")
                except Exception:
                    pass
            return _with_iso(combined), None
        except ImportError:
            log.warning("  [multi-agent] multi_agent.py not available, fallback to gather")

        async def _run_one(pt):
            return await _run_subagent(
                pt.get("task", ""), pt.get("agent_type", "coder"),
                pt.get("context", context), parent_sid=sid,
            )
        results = await asyncio.gather(*[_run_one(pt) for pt in multi_tasks], return_exceptions=True)
        parts = [f"[{i}:{pt.get('agent_type','?')}] {'ERROR: '+str(r) if isinstance(r, Exception) else r}"
                 for i, (pt, r) in enumerate(zip(multi_tasks, results))]
        return _with_iso("\n\n---\n\n".join(parts)), None

    if not task:
        return "ERROR: task is required", None
    try:
        result = await _run_subagent(task, agent_type, context, parent_sid=sid)
        if sid and td.HAS_MEMORY and result and not result.startswith("ERROR"):
            try:
                mgr = td._mem_get(workspace=WORKSPACE, session_id=sid,
                                  vllm_url=td.BACKEND_URL, model_id=td.MODEL_ID,
                                  api_key=td.API_KEY, context_window=td.CONTEXT_WINDOW)
                summary = f"[{agent_type}] {task[:60]} -> {result[:200]}"
                mgr.save("Completed Work", summary)
            except Exception:
                pass
        return _with_iso(result), None
    except Exception as e:
        return f"ERROR in subagent({agent_type}): {e}", None


@register("agent_worktree")
async def h_agent_worktree(sid: str, args: dict) -> tuple[str, Any]:
    """子代理隔离工作树的后续处理: list / merge / discard / diff。"""
    from lib.agent.subagent_worktree import agent_worktree_tool
    try:
        return await agent_worktree_tool(args or {}), None
    except Exception as e:
        return f"ERROR: agent_worktree 失败: {e}", None
