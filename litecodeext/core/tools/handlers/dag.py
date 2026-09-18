"""handlers/dag.py — create/update/delete/patch/run DAG 5 个 tool."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import register


def _ws() -> Path:
    try:
        from lib.config import CFG  # type: ignore
    except Exception:
        CFG = {}
    return Path(CFG.get("paths", {}).get("workspace_base", "/tmp/litecode_workspace"))


def _load_dag_helpers():
    try:
        from lib.dag_schema import save_dag, validate_dag_json, load_dag
    except Exception:
        from litecodeext.lib.dag_schema import save_dag, validate_dag_json, load_dag
    return save_dag, validate_dag_json, load_dag


@register("create_dag")
async def h_create_dag(sid: str, args: dict) -> tuple[str, Any]:
    try:
        dag_name = (args.get("name") or "").strip()
        nodes_in = args.get("nodes") or []
        edges_in = args.get("edges") or []
        if not dag_name or not nodes_in:
            return "ERROR: create_dag 需要 name + nodes", None
        save_dag, validate_dag_json, _ = _load_dag_helpers()
        steps = []
        for n in nodes_in:
            step = {
                "id":         (n.get("id") or "").strip() or n.get("agent_type", "node"),
                "agent_type": n.get("agent_type", "coder"),
                "label":      n.get("label") or n.get("id") or n.get("agent_type", "node"),
                "task":       n.get("task") or "",
                "depends_on": [],
                "when":       list(n.get("when") or []),
            }
            steps.append(step)
        id_set = {s["id"] for s in steps}
        for e in edges_in:
            src = e.get("from"); dst = e.get("to")
            if src in id_set and dst in id_set:
                next(s for s in steps if s["id"] == dst)["depends_on"].append(src)
        body = {"name": dag_name, "steps": steps, "created": time.time()}
        ok, errs = validate_dag_json(body)
        if not ok:
            return f"ERROR: create_dag 验证失败: {errs}", None
        ws = _ws()
        ws.mkdir(parents=True, exist_ok=True)
        fp = save_dag(ws, dag_name, body)
        return (f"✅ DAG 已创建: name={dag_name} 节点={len(steps)} "
                f"边={sum(len(s['depends_on']) for s in steps)}\n"
                f"路径: {fp}\n在 DAG 面板里点 ▶ 运行，或 POST /api/dags/{dag_name}/run"), None
    except Exception as e:
        import traceback as _tb
        return f"ERROR: create_dag 失败: {type(e).__name__}: {e}\n{_tb.format_exc()[:300]}", None


@register("update_dag")
async def h_update_dag(sid: str, args: dict) -> tuple[str, Any]:
    try:
        dag_name = (args.get("name") or "").strip()
        nodes_in = args.get("nodes") or []
        edges_in = args.get("edges") or []
        if not dag_name or not nodes_in:
            return "ERROR: update_dag 需要 name + nodes", None
        save_dag, validate_dag_json, load_dag = _load_dag_helpers()
        ws = _ws()
        if not load_dag(ws, dag_name):
            return f"ERROR: DAG '{dag_name}' 不存在，请先用 create_dag 创建", None
        steps = []
        for n in nodes_in:
            step = {
                "id":         (n.get("id") or "").strip() or n.get("agent_type", "node"),
                "agent_type": n.get("agent_type", "coder"),
                "label":      n.get("label") or n.get("id") or n.get("agent_type", "node"),
                "task":       n.get("task") or "",
                "depends_on": [],
            }
            steps.append(step)
        id_set = {s["id"] for s in steps}
        for e in edges_in:
            src = e.get("from"); dst = e.get("to")
            if src in id_set and dst in id_set:
                next(s for s in steps if s["id"] == dst)["depends_on"].append(src)
        body = {"name": dag_name, "steps": steps, "created": time.time()}
        ok, errs = validate_dag_json(body)
        if not ok:
            return f"ERROR: update_dag 验证失败: {errs}", None
        fp = save_dag(ws, dag_name, body)
        return (f"✅ DAG 已更新: name={dag_name} 节点={len(steps)} "
                f"边={sum(len(s['depends_on']) for s in steps)}\n路径: {fp}"), None
    except Exception as e:
        import traceback as _tb
        return f"ERROR: update_dag 失败: {type(e).__name__}: {e}\n{_tb.format_exc()[:300]}", None


@register("delete_dag")
async def h_delete_dag(sid: str, args: dict) -> tuple[str, Any]:
    try:
        dag_name = (args.get("name") or "").strip()
        if not dag_name:
            return "ERROR: delete_dag 需要 name", None
        import re as _re
        if not _re.match(r"^[A-Za-z0-9_\-.]+$", dag_name) or ".." in dag_name:
            return "ERROR: DAG 名包含非法字符", None
        ws = _ws()
        fp = ws / "dags" / f"{dag_name}.json"
        if not fp.exists():
            return f"ERROR: DAG '{dag_name}' 不存在", None
        fp.unlink()
        return f"✅ DAG 已删除: name={dag_name}", None
    except Exception as e:
        return f"ERROR: delete_dag 失败: {type(e).__name__}: {e}", None


@register("patch_dag_node")
async def h_patch_dag_node(sid: str, args: dict) -> tuple[str, Any]:
    _PATCH_ALLOWED = {"label", "task", "agent_type"}
    try:
        dag_name = (args.get("name") or "").strip()
        node_id = (args.get("node_id") or "").strip()
        if not dag_name or not node_id:
            return "ERROR: patch_dag_node 需要 name + node_id", None
        patch = {k: v for k, v in args.items() if k in _PATCH_ALLOWED}
        rejected = {k for k in args if k not in _PATCH_ALLOWED and k not in ("name", "node_id")}
        if rejected:
            return f"ERROR: patch_dag_node 不允许修改字段: {sorted(rejected)}（只允许 label/task/agent_type）", None
        if not patch:
            return "ERROR: patch_dag_node 未提供任何可修改字段（label/task/agent_type）", None
        save_dag, validate_dag_json, load_dag = _load_dag_helpers()
        ws = _ws()
        body = load_dag(ws, dag_name)
        if not body:
            return f"ERROR: DAG '{dag_name}' 不存在", None
        steps = body.get("steps") or []
        target = next((s for s in steps if s.get("id") == node_id), None)
        if not target:
            return f"ERROR: DAG '{dag_name}' 中找不到节点 id='{node_id}'", None
        for k, v in patch.items():
            target[k] = v
        ok, errs = validate_dag_json(body)
        if not ok:
            return f"ERROR: patch_dag_node 验证失败: {errs}", None
        save_dag(ws, dag_name, body)
        return (f"✅ 节点已更新: DAG={dag_name} node_id={node_id} "
                f"改动={list(patch.keys())}"), None
    except Exception as e:
        return f"ERROR: patch_dag_node 失败: {type(e).__name__}: {e}", None


@register("run_dag")
async def h_run_dag(sid: str, args: dict) -> tuple[str, Any]:
    try:
        import asyncio as _asyncio
        import uuid as _uuid
        dag_name = (args.get("name") or "").strip()
        if not dag_name:
            return "ERROR: run_dag 需要 name", None
        _, _, load_dag = _load_dag_helpers()
        ws = _ws()
        plan = load_dag(ws, dag_name)
        if not plan:
            return f"ERROR: DAG '{dag_name}' 不存在", None
        steps = plan.get("steps") or []
        if not steps:
            return f"ERROR: DAG '{dag_name}' 没有步骤", None
        try:
            from multi_agent import AgentMode, AgentSpec, MultiAgentOrchestrator
            from lib.agent.subagent import _run_subagent
        except ImportError as ie:
            return f"ERROR: orchestrator 不可用: {ie}", None
        try:
            try:
                from web_ui import _DAG_JOBS
            except Exception:
                from litecodeext.web_ui import _DAG_JOBS
        except Exception:
            _DAG_JOBS = {}
        job_id = _uuid.uuid4().hex[:12]
        _DAG_JOBS[job_id] = {
            "name": dag_name, "started": time.time(), "status": "running",
            "step_count": len(steps), "agents": [],
        }
        specs = [AgentSpec(
            task=s.get("task", ""),
            agent_type=s.get("agent_type", "coder"),
            context=s.get("context", ""),
            label=s.get("label") or s.get("id", ""),
            depends_on=list(s.get("depends_on", []) or []),
            step_id=s.get("id", ""),
            timeout=int(s.get("timeout", 300)),
            max_retries=int(s.get("max_retries", 2)),
        ) for s in steps]

        async def _bg_run():
            try:
                orch = MultiAgentOrchestrator(run_subagent_fn=_run_subagent, workspace=ws)
                res = await orch.run(AgentMode.DAG, specs, auto_critic=bool(plan.get("auto_critic")))
                _DAG_JOBS[job_id].update({
                    "status": "done",
                    "elapsed": time.time() - _DAG_JOBS[job_id]["started"],
                    "agents": [{"label": r.label, "agent_type": r.agent_type,
                                "success": r.success, "elapsed": r.elapsed,
                                "error": r.error[:200] if r.error else "",
                                "output_preview": (r.output or "")[:300]} for r in res.agents],
                    "final_output": (res.final_output or "")[:2000],
                })
            except Exception as _e:
                _DAG_JOBS[job_id].update({
                    "status": "failed",
                    "elapsed": time.time() - _DAG_JOBS[job_id]["started"],
                    "error": f"{type(_e).__name__}: {_e}",
                })

        _asyncio.ensure_future(_bg_run())
        return (f"✅ DAG 已启动: name={dag_name} job_id={job_id} "
                f"步骤数={len(specs)}\n"
                f"查询进度: GET /api/dags/jobs/{job_id}"), None
    except Exception as e:
        return f"ERROR: run_dag 失败: {type(e).__name__}: {e}", None
