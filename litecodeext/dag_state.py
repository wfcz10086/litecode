"""dag_state.py — DAG job 后台状态 + 启动器 (从 web_ui.py 抽出).

**拆分动机 (2026-07-27)**: web_ui.py 1153 → <1000 硬顶收敛第二波.
DAG 段 (原 web_ui.py:892-1122, 231 行) 内聚性强, 只用 CFG 一个外部依赖,
适合独立文件.

**导出** (`routers/dag_router.py` 通过 `web_ui._DAG_JOBS / _start_dag_internal`
拿到, 所以 web_ui.py 里必须 `from dag_state import *` 保持向后兼容):
  - `_DAG_JOBS`, `_DAG_JOBS_LOCK`, `_DAG_JOBS_DIR`
  - `_dag_job_save(job_id)`
  - `_dag_jobs_load()` — 模块 import 时自动调 (启动恢复)
  - `_start_dag_internal(name)` — HTTP/timer 共用的核心启动器
"""
from __future__ import annotations
import asyncio as _asyncio
import json
import time
from pathlib import Path

from lib.config import CFG  # type: ignore


# ── DAG 编排 API (P5-d) ────────────────────────────────────────
# 后台运行中的 DAG job 状态 — 提到列表/详情之前是为了路由顺序: jobs 字面量必须先于 {name} 通配
_DAG_JOBS: dict = {}
# [P0-#5] 保护复合 read-modify-write (create/update/save 序列). 单点 get/set 靠 GIL 原子性.
_DAG_JOBS_LOCK = _asyncio.Lock()

# [P54+] DAG jobs 持久化 — 容器重启后不丢
_DAG_JOBS_DIR = Path.home() / ".litecode" / "dag_jobs"
_DAG_JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _dag_job_save(job_id: str):
    """把 _DAG_JOBS[job_id] 写盘 (剥掉非 JSON 字段) [P0-#5 dict-copy · #6 fsync]"""
    job = _DAG_JOBS.get(job_id)
    if not job:
        return
    try:
        # [P0-#5] dict() 复制是 GIL 原子, 避免边 iter 边被 mutate 抛 RuntimeError
        _shallow = dict(job)
        snap = {k: v for k, v in _shallow.items() if k not in ("resume_events", "_task")}
        # breakpoints 是 set，要转成 list
        if isinstance(snap.get("breakpoints"), set):
            snap["breakpoints"] = sorted(snap["breakpoints"])
        from lib.atomic_io import atomic_write_json
        atomic_write_json(_DAG_JOBS_DIR / f"{job_id}.json", snap)
    except Exception as e:
        print(f"[_dag_job_save] {job_id} fail: {e}")


def _dag_jobs_load():
    """启动时从盘恢复 _DAG_JOBS. [P0-#20] 只在心跳陈旧 >5min 才标 interrupted,
    避 gunicorn worker 重启把仍在跑的 job 误杀 (虽然此进程 _task 已丢, 但盘上 heartbeat
    有可能被另一个进程/后续心跳继续更新, 保守只标可见死亡)."""
    if not _DAG_JOBS_DIR.exists():
        return
    n = 0
    _STALE_SEC = 300  # 5 min
    _now = time.time()
    for f in sorted(_DAG_JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        try:
            d = json.loads(f.read_text())
            jid = f.stem
            if d.get("status") == "running":
                hb = d.get("last_heartbeat") or d.get("started") or 0
                stale = (_now - hb) if hb else _STALE_SEC + 1
                # 老 record 无心跳字段 → 保守判 interrupted
                # 新 record 心跳 >5min → 判 interrupted
                # 新 record 心跳新鲜 → 保留 running (让 lifespan/shutdown 兜底或人工 abort)
                if stale > _STALE_SEC:
                    d["status"] = "interrupted"
                    d["error"] = f"容器重启时 job 仍 running, 心跳陈旧 {int(stale)}s > {_STALE_SEC}s"
            # breakpoints 还原为 set 类型 (在 ws _bg_run 用 in 判断)
            if isinstance(d.get("breakpoints"), list):
                d["breakpoints"] = set(d["breakpoints"])
            d["resume_events"] = {}
            _DAG_JOBS[jid] = d
            n += 1
        except Exception as e:
            print(f"[_dag_jobs_load] skip {f.name}: {e}")
    if n:
        print(f"[_dag_jobs_load] 从 {_DAG_JOBS_DIR} 恢复 {n} 个 DAG job")


# 启动时立即恢复
_dag_jobs_load()


async def _start_dag_internal(name: str) -> dict:
    """[v1.3] 启动 DAG 的核心实现, 无 HTTP/auth. 给 api_dags_run 和 timer dag-action 共用.
    成功返回 {"ok": True, "job_id", "name", "step_count"};
    失败返回 {"ok": False, "error", "status_code": <int>}."""
    import asyncio
    import uuid
    from pathlib import Path as _P
    from lib.dag_schema import load_dag
    ws = _P(CFG.get("paths", {}).get("workspace_base", "/tmp/litecode_workspace"))
    plan = load_dag(ws, name)
    if not plan:
        return {"ok": False, "error": f"DAG '{name}' not found", "status_code": 404}
    steps = plan.get("steps") or []
    if not steps:
        return {"ok": False, "error": "DAG has no steps", "status_code": 400}

    try:
        from multi_agent import AgentMode, AgentSpec, MultiAgentOrchestrator
        from lib.agent.subagent import _run_subagent
        import lib.agent.subagent as _sa_mod
    except ImportError as ie:
        return {"ok": False, "error": f"orchestrator unavailable: {ie}", "status_code": 503}
    # 关键: web_ui 自身没初始化 tool_dispatch, 子代理调任何工具会拿 execute_tool=None
    # 复用 server 同一份初始化路径 (litecode_server 模块顶层 _init_tool_dispatch + set_execute_tool)
    # __main__ 保护让 import 不会真起 uvicorn
    if _sa_mod.execute_tool is None:
        try:
            import sys as _sys
            if 'litecode_server' not in _sys.modules:
                import litecode_server as _ls  # noqa: F401  触发 init
            # 二次保险: 直接拿 execute_tool 注入
            if _sa_mod.execute_tool is None:
                from core.tool_dispatch import execute_tool as _et
                _sa_mod.set_execute_tool(_et)
                try:
                    from lib.transport import parse_text_tool_calls as _ptc
                    _sa_mod.set_parse_text_tool_calls(_ptc)
                except Exception:
                    pass
        except Exception as _e:
            return {"ok": False, "error": f"tool injection failed: {_e}", "status_code": 503}

    specs = [AgentSpec(
        task=s.get("task", ""),
        agent_type=s.get("agent_type", "coder"),
        context=s.get("context", ""),
        label=s.get("label") or s.get("id", ""),
        depends_on=list(s.get("depends_on", []) or []),
        step_id=s.get("id", ""),
        timeout=int(s.get("timeout", 300)),
        max_retries=int(s.get("max_retries", 2)),
        when=list(s.get("when", []) or []),  # [v1.3] 多条件透传
        # [Round 4 · 2026-07-24] 原生工具步透传
        tool_name=s.get("tool_name", "") or "",
        tool_args=dict(s.get("tool_args") or {}),
        dag_name=s.get("dag_name", "") or "",  # [#2] 子 DAG 名透传
    ) for s in steps]

    job_id = uuid.uuid4().hex[:12]
    # [P0-#5] 创建 + 落盘作为一个原子块 (防跟并发 router job 状态查询交错)
    # [P0-#20] last_heartbeat: 后续 lifespan/load 用这个判 running 是否真活着
    async with _DAG_JOBS_LOCK:
        _DAG_JOBS[job_id] = {
            "name": name, "started": time.time(), "status": "running",
            "last_heartbeat": time.time(),
            "step_count": len(specs),
            "agents": [],
            "breakpoints": {s.get("id") for s in steps if s.get("breakpoint")},
            "resume_events": {},
            "paused_at": None,
        }
        _dag_job_save(job_id)

    async def _bg_run():
        async def _pause_check(node_id):
            job = _DAG_JOBS.get(job_id)
            if not job or node_id not in job["breakpoints"]:
                return
            ev = job["resume_events"].setdefault(node_id, asyncio.Event())
            job["paused_at"] = node_id
            try:
                await ev.wait()
            finally:
                ev.clear()
                if job.get("paused_at") == node_id:
                    job["paused_at"] = None

        # [v1.3] 步骤完成回调: 实时把 StepResult 灌进 _DAG_JOBS[job_id]['agents']
        # 让前端轮询 _pollDAGJob 在 DAG 跑完之前就能看到节点日志, 不再卡 "还未产出"
        def _step_done(sr):
            job = _DAG_JOBS.get(job_id)
            if not job:
                return
            from orchestrator import StepStatus as _SS
            row = {
                "label": sr.label,
                "agent_type": sr.agent_type,
                "success": (sr.status == _SS.SUCCESS),
                "elapsed": sr.elapsed,
                "error": (sr.error or "")[:500],
                "output_preview": (sr.output or "")[:8000],
                "step_id": sr.step_id or sr.label,
                "status": sr.status.value if hasattr(sr.status, "value") else str(sr.status),
            }
            ags = job.setdefault("agents", [])
            replaced = False
            for i, a in enumerate(ags):
                if a.get("step_id") == row["step_id"]:
                    ags[i] = row
                    replaced = True
                    break
            if not replaced:
                ags.append(row)
            # [④ 2026-09-05] 执行记录时间线
            job.setdefault("exec_log", []).append(
                {"t": time.time(), "event": "step_done", "step_id": row["step_id"],
                 "status": row["status"], "success": row["success"],
                 "elapsed": row["elapsed"], "out_len": len(sr.output or ""),
                 "error": (sr.error or "")[:200]})
            # [P0-#20] 每步完成都跳心跳, load 时 >5 分钟无心跳才判 interrupted
            job["last_heartbeat"] = time.time()
            _dag_job_save(job_id)  # 落盘, 刷新页面也不丢

        # [2026-09-05] 步开始回调: 长步期间也能看到"运行中"步 + 刷心跳 (解决执行期间全盲/心跳陈旧)。
        # 完成时 _step_done 会按 step_id 替换掉这条 running 记录 (已有 update-or-append 逻辑)。
        def _step_begin(spec, label):
            job = _DAG_JOBS.get(job_id)
            if not job:
                return
            sid = getattr(spec, "step_id", None) or getattr(spec, "id", None) or label
            ags = job.setdefault("agents", [])
            if not any(a.get("step_id") == sid for a in ags):
                ags.append({"label": label, "agent_type": getattr(spec, "agent_type", ""),
                            "success": None, "elapsed": 0.0, "error": "",
                            "output_preview": "", "step_id": sid, "status": "running"})
            # [④ 2026-09-05] 执行记录时间线
            job.setdefault("exec_log", []).append(
                {"t": time.time(), "event": "step_begin", "step_id": sid,
                 "agent_type": getattr(spec, "agent_type", "")})
            job["last_heartbeat"] = time.time()
            _dag_job_save(job_id)

        # [2026-09-05 ①②] 每个 SSE chunk 刷心跳(长步内也不 stale) + 累积实时 output 到运行中步。
        # in-memory 更新, 不逐 chunk 落盘(避免写风暴), 落盘仍走步边界 _step_begin/_step_done。
        async def _sse_emit(chunk):
            job = _DAG_JOBS.get(job_id)
            if not job:
                return
            job["last_heartbeat"] = time.time()   # ① 长步心跳
            try:
                raw = chunk.strip()
                if raw.startswith("data:"):
                    raw = raw[5:].strip()
                delta = json.loads(raw)["choices"][0]["delta"]
                osd = delta.get("orchestrator_step") or {}
                if osd.get("output_delta"):
                    sid = osd.get("step_id")
                    for a in job.get("agents", []):
                        if a.get("step_id") == sid and a.get("status") == "running":
                            a["output_preview"] = (a.get("output_preview", "") + osd["output_delta"])[-8000:]
                            break
            except Exception:
                pass

        # [P0-3 2026-09-05] 给独立 DAG job 接真 itrace tracer。
        # 此前这里不传 tracer → MultiAgentOrchestrator._tracer=None → 下游 DAGOrchestrator
        # 退回 NullTracer (no-op), 于是 build-history 在 iteration_full.log 一条不留 (全盲)。
        # make_tracer 尊重 config.iteration_trace.enabled: 关则返回 NullTracer, 零成本。
        _dag_tracer = None
        try:
            try:
                from core.itrace import make_tracer as _make_tracer
            except ImportError:
                from itrace import make_tracer as _make_tracer  # 容器内扁平路径兜底
            _dag_tracer = _make_tracer(CFG.get("iteration_trace", {}), workspace=ws,
                                       session_id=f"dag:{name}:{job_id}")
        except Exception:
            _dag_tracer = None  # 造不出就退回 None, 下游 NullTracer 兜底, 不影响主流

        try:
            orch = MultiAgentOrchestrator(run_subagent_fn=_run_subagent, workspace=ws,
                                          pause_check_fn=_pause_check,
                                          step_done_cb=_step_done,
                                          step_begin_cb=_step_begin,
                                          tracer=_dag_tracer)
            res = await orch.run(AgentMode.DAG, specs, auto_critic=bool(plan.get("auto_critic")),
                                 sse_emit=_sse_emit)  # [①②] 长步心跳 + 实时输出
            # [P0-#5] 状态转移 + 落盘, 单原子块
            async with _DAG_JOBS_LOCK:
                _DAG_JOBS[job_id].update({
                    "status": "done",
                    "elapsed": time.time() - _DAG_JOBS[job_id]["started"],
                    "last_heartbeat": time.time(),
                    "agents": [{"label": r.label, "agent_type": r.agent_type,
                                "success": r.success, "elapsed": r.elapsed,
                                "error": r.error[:500] if r.error else "",
                                # [P54+] 加大节点输出窗口: 300 → 8000 字符, 让 modal 真有日志可看
                                "output_preview": (r.output or "")[:8000],
                                "step_id": getattr(r, "step_id", None) or r.label} for r in res.agents],
                    "final_output": (res.final_output or "")[:4000],
                })
                _dag_job_save(job_id)
        except asyncio.CancelledError:
            async with _DAG_JOBS_LOCK:
                _DAG_JOBS[job_id].update({
                    "status": "aborted",
                    "elapsed": time.time() - _DAG_JOBS[job_id]["started"],
                    "last_heartbeat": time.time(),
                    "error": "aborted by user",
                })
                _dag_job_save(job_id)
            raise
        except Exception as e:
            async with _DAG_JOBS_LOCK:
                _DAG_JOBS[job_id].update({
                    "status": "failed",
                    "elapsed": time.time() - _DAG_JOBS[job_id]["started"],
                    "last_heartbeat": time.time(),
                    "error": f"{type(e).__name__}: {e}",
                })
                _dag_job_save(job_id)

    _DAG_JOBS[job_id]["_task"] = asyncio.create_task(_bg_run())
    return {"ok": True, "job_id": job_id, "name": name, "step_count": len(specs)}
