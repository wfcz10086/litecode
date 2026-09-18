"""dag_router.py — DAG 编排: list/save/get/delete + jobs/breakpoints/resume.

DAG 启动器 (_start_dag_internal) 和 _DAG_JOBS 全局 dict 保留在 web_ui.py 主模块,
本 router 通过 `_wui()` lazy import 拿到 (避免循环 import).
"""
import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()


def _wui():
    import web_ui  # type: ignore
    return web_ui


def _validate_dag_name(name: str) -> bool:
    """拒绝路径穿越/分隔符 — DAG 名只允许字母/数字/-/_/."""
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith("."):
        return False
    if not re.match(r"^[A-Za-z0-9_\-.]+$", name):
        return False
    return True


def _ws() -> Path:
    return Path(S.cfg.get("paths", {}).get("workspace_base", "/tmp/litecode_workspace"))


# ── job 相关 (jobs 必须先注册, 在通配 /api/dags/{name} 之前) ──
@router.get("/api/dags/jobs")
async def api_dags_jobs_list(request: Request):
    require_auth(request)
    jobs = _wui()._DAG_JOBS
    items = sorted(jobs.items(), key=lambda kv: kv[1].get("started", 0), reverse=True)[:20]
    # [FIX 2026-09-05] 过滤不可序列化字段 (_task=asyncio.Task, resume_events) —
    # 单 job 端点早已过滤, 列表端点漏了 → 整个 /api/dags/jobs 500 (JSON 序列化炸)。
    def _clean(j):
        return {k: v for k, v in j.items() if k not in ("_task", "resume_events")}
    return {"jobs": [{"job_id": jid, **_clean(j)} for jid, j in items]}


@router.get("/api/dags/jobs/{job_id}")
async def api_dags_job_status(job_id: str, request: Request):
    require_auth(request)
    job = _wui()._DAG_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    out = {k: v for k, v in job.items() if k not in ("resume_events", "_task")}
    out["breakpoints"] = sorted(job.get("breakpoints", set()))
    return out


@router.get("/api/dags/jobs/{job_id}/log")
async def api_dags_job_log(job_id: str, request: Request):
    """[④ 2026-09-05] DAG 运行的执行记录时间线 (step_begin/step_done 事件 + 可读文本)。"""
    require_auth(request)
    job = _wui()._DAG_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    import time as _t
    events = job.get("exec_log", [])
    t0 = job.get("started", events[0]["t"] if events else _t.time())
    lines = [f"DAG: {job.get('name')} | job {job_id} | status={job.get('status')}"]
    for e in events:
        rel = f"+{e['t'] - t0:6.1f}s"
        if e["event"] == "step_begin":
            lines.append(f"  {rel}  ▶ {e['step_id']} 开始 ({e.get('agent_type','')})")
        else:
            mark = "✓" if e.get("success") else ("⏭ skip" if "条件不满足" in (e.get("error") or "") else "✗")
            lines.append(f"  {rel}  {mark} {e['step_id']} 完成 status={e.get('status')} "
                         f"耗时={e.get('elapsed', 0):.1f}s 输出={e.get('out_len', 0)}字"
                         + (f" err={e['error']}" if e.get("error") else ""))
    return {"job_id": job_id, "name": job.get("name"), "status": job.get("status"),
            "events": events, "text": "\n".join(lines)}


@router.post("/api/dags/jobs/{job_id}/breakpoints/{node_id}")
async def api_dag_set_breakpoint(job_id: str, node_id: str, request: Request):
    require_auth(request)
    job = _wui()._DAG_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    body = await request.json()
    if body.get("set", True):
        job["breakpoints"].add(node_id)
    else:
        job["breakpoints"].discard(node_id)
        if job.get("paused_at") == node_id:
            ev = job["resume_events"].get(node_id)
            if ev:
                ev.set()
    return {"ok": True, "breakpoints": sorted(job["breakpoints"]),
            "paused_at": job.get("paused_at")}


@router.get("/api/dags/jobs/{job_id}/breakpoints")
async def api_dag_get_breakpoints(job_id: str, request: Request):
    require_auth(request)
    job = _wui()._DAG_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    return {"breakpoints": sorted(job["breakpoints"]),
            "paused_at": job.get("paused_at")}


@router.post("/api/dags/jobs/{job_id}/abort")
async def api_dag_job_abort(job_id: str, request: Request):
    """取消运行中的 DAG job — 底层 asyncio.Task.cancel(), bg_run 捕获后置状态 aborted."""
    require_auth(request)
    job = _wui()._DAG_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    if job.get("status") != "running":
        return {"ok": False, "already": job.get("status")}
    task = job.get("_task")
    if task and not task.done():
        task.cancel()
    return {"ok": True, "status": "aborting"}


@router.post("/api/dags/jobs/{job_id}/resume/{node_id}")
async def api_dag_resume(job_id: str, node_id: str, request: Request):
    require_auth(request)
    job = _wui()._DAG_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    ev = job["resume_events"].setdefault(node_id, asyncio.Event())
    ev.set()
    return {"ok": True}


# ── M1: Build 详情 - Artifact 聚合 ─────────────────────────────
_ART_EXTS = ("png|jpg|jpeg|gif|webp|svg|pdf|xlsx|xls|docx|doc|pptx|ppt|"
             "txt|md|json|csv|yaml|yml|zip|tar|gz|dxf|dwg|stl|obj|html|log")
_ART_PATH_RE = re.compile(
    r'(/(?:tmp|home|root|var|opt|data|workspace)/[^\s`\'"<>()\[\]]+?\.(?:' + _ART_EXTS + r'))\b',
    re.IGNORECASE)
_ART_URL_RE = re.compile(r'https?://[^\s`\'"<>()\[\]]+', re.IGNORECASE)


def _extract_artifacts(agents: list) -> list:
    """从每个 stage 的 output_preview 里抽 file path / url. 去重, 优先取真实存在的文件."""
    import os
    seen: set[str] = set()
    out: list[dict] = []
    for a in agents:
        stage_id = a.get("step_id") or a.get("label") or a.get("agent_type", "")
        text = a.get("output_preview") or ""
        for m in _ART_PATH_RE.finditer(text):
            p = m.group(1).rstrip('.,;:)')
            if p in seen:
                continue
            seen.add(p)
            try:
                if os.path.isfile(p):
                    out.append({"stage": stage_id, "kind": "file", "path": p,
                                "name": os.path.basename(p),
                                "size": os.path.getsize(p)})
            except OSError:
                pass
        for m in _ART_URL_RE.finditer(text):
            u = m.group(0).rstrip('.,;:)')
            if u in seen:
                continue
            seen.add(u)
            name = u.rsplit('/', 1)[-1] or u
            out.append({"stage": stage_id, "kind": "url", "url": u, "name": name[:80]})
    return out


@router.get("/api/dags/jobs/{job_id}/artifacts")
async def api_dag_job_artifacts(job_id: str, request: Request):
    """M1: 聚合 Build 产物 (从 stages 的 output 里抽 file path / url)."""
    require_auth(request)
    job = _wui()._DAG_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    return {"job_id": job_id, "artifacts": _extract_artifacts(job.get("agents", []))}


# ── AI 生成 / 修改 / 定时 (LLM 辅助) ──
async def _llm_call(prompt: str, model_id: str, max_tokens: int = 2000, timeout: int = 90) -> str:
    """内部走 /v1/chat/completions, 复用集群网关."""
    import httpx
    port = S.cfg.get("server", {}).get("port", 18789)
    token = S.cfg.get("server", {}).get("token", "")
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"model": model_id, "stream": False, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]}
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _default_model(body: dict) -> str:
    return body.get("model") or S.cfg.get("default_model_id") or "deepseek-v4-pro"


@router.post("/api/dags/generate")
async def api_dags_generate(request: Request):
    require_auth(request)
    body = await request.json()
    user_request = (body.get("request") or "").strip()
    if not user_request:
        return JSONResponse({"error": "need 'request' field"}, status_code=400)
    from lib.dag_ai_gen import build_dag_gen_prompt, parse_llm_dag_response
    from lib.dag_schema import validate_dag_json
    prompt = build_dag_gen_prompt(user_request)
    text = await _llm_call(prompt, _default_model(body))
    dag = parse_llm_dag_response(text)
    if not dag:
        return JSONResponse({"error": "LLM did not return valid DAG JSON", "raw": text[:500]}, status_code=422)
    ok, errs = validate_dag_json(dag)
    if not ok:
        return JSONResponse({"warning": errs, **dag})
    return dag


@router.post("/api/dags/{name}/nl_edit")
async def api_dags_nl_edit(name: str, request: Request):
    """自然语言修改已有 DAG. body: {instruction, model?, save?} — save=True 直写文件."""
    require_auth(request)
    if not _validate_dag_name(name):
        return JSONResponse({"error": "invalid DAG name"}, status_code=400)
    body = await request.json()
    instruction = (body.get("instruction") or "").strip()
    if not instruction:
        return JSONResponse({"error": "need 'instruction'"}, status_code=400)
    from lib.dag_ai_gen import build_dag_edit_prompt, parse_llm_dag_response
    from lib.dag_schema import load_dag, save_dag, validate_dag_json
    existing = load_dag(_ws(), name)
    if not existing:
        return JSONResponse({"error": f"DAG '{name}' not found"}, status_code=404)
    prompt = build_dag_edit_prompt(existing, instruction)
    text = await _llm_call(prompt, _default_model(body))
    new_dag = parse_llm_dag_response(text)
    if not new_dag:
        return JSONResponse({"error": "LLM did not return valid DAG JSON", "raw": text[:500]}, status_code=422)
    ok, errs = validate_dag_json(new_dag)
    if not ok:
        return JSONResponse({"error": "invalid DAG after edit", "details": errs, "dag": new_dag}, status_code=422)
    saved = False
    if body.get("save"):
        save_dag(_ws(), name, new_dag)
        saved = True
    return {"ok": True, "name": name, "saved": saved, "dag": new_dag}


@router.post("/api/dags/nl_delete")
async def api_dags_nl_delete(request: Request):
    """自然语言删 DAG. body: {query, confirm?} — 默认 dry-run 返回候选; confirm=True 才删.
    匹配策略: name/description 大小写不敏感子串匹配, 命中 ≥1 才返回候选."""
    require_auth(request)
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "need 'query'"}, status_code=400)
    from lib.dag_schema import list_dags, load_dag
    ws = _ws()
    all_names = list_dags(ws) or []
    ql = query.lower()
    candidates = []
    for n in all_names:
        name_str = n if isinstance(n, str) else n.get("name", "")
        if not name_str:
            continue
        hit_name = ql in name_str.lower()
        d = load_dag(ws, name_str) or {}
        desc = (d.get("description") or "").lower()
        hit_desc = ql in desc
        if hit_name or hit_desc:
            candidates.append({"name": name_str, "match": "name" if hit_name else "description",
                               "description": d.get("description", "")[:120]})
    if not candidates:
        return {"ok": False, "candidates": [], "message": f"没有 DAG 匹配 '{query}'"}
    if not body.get("confirm"):
        return {"ok": True, "dry_run": True, "candidates": candidates,
                "message": f"匹配到 {len(candidates)} 个, 加 confirm=true 才真删"}
    deleted = []
    for c in candidates:
        fp = ws / "dags" / f"{c['name']}.json"
        if fp.exists():
            fp.unlink()
            deleted.append(c["name"])
    return {"ok": True, "deleted": deleted, "count": len(deleted)}


@router.post("/api/dags/nl_schedule")
async def api_dags_nl_schedule(request: Request):
    """自然语言给 DAG 定时. body: {name, when, model?} → LLM 转 cron → 创建 timer.
    直接返回 timer 对象 + 生成的 cron 表达式."""
    require_auth(request)
    body = await request.json()
    name = (body.get("name") or "").strip()
    when = (body.get("when") or "").strip()
    if not name or not when:
        return JSONResponse({"error": "need 'name' and 'when'"}, status_code=400)
    if not _validate_dag_name(name):
        return JSONResponse({"error": "invalid DAG name"}, status_code=400)
    from lib.dag_schema import load_dag
    from lib.dag_ai_gen import build_nl_cron_prompt, parse_llm_dag_response
    if not load_dag(_ws(), name):
        return JSONResponse({"error": f"DAG '{name}' not found"}, status_code=404)
    prompt = build_nl_cron_prompt(when)
    text = await _llm_call(prompt, _default_model(body), max_tokens=200, timeout=30)
    parsed = parse_llm_dag_response(text)
    cron = (parsed or {}).get("cron", "").strip()
    human = (parsed or {}).get("human", "")
    if not cron or len(cron.split()) != 5:
        return JSONResponse({"error": "LLM 未能生成合法 cron", "raw": text[:400]}, status_code=422)
    try:
        from timer_manager import get_timer_manager  # type: ignore
        tm = get_timer_manager()
    except Exception as e:
        return JSONResponse({"error": f"timer 模块不可用: {e}"}, status_code=500)
    try:
        timer = tm.add_timer({
            "name": f"NL-DAG-{name}",
            "type": "cron",
            "schedule": cron,
            "action_type": "dag",
            "action_target": name,
            "action_content": "",
            "description": f"NL: {when} → {human}",
            "enabled": True,
        })
    except ValueError as ve:
        return JSONResponse({"error": f"timer 创建失败: {ve}", "cron": cron}, status_code=400)
    return {"ok": True, "timer": timer, "cron": cron, "human": human}


# ── DAG 定义文件 CRUD ──
@router.get("/api/dags")
async def api_dags_list(request: Request):
    require_auth(request)
    try:
        from lib.dag_schema import list_dags
        names = list_dags(_ws())
        # 附加每个 DAG 的最近一次 build 信息 (给 Pipeline 列表页状态点用)
        jobs = _wui()._DAG_JOBS
        last = {}
        for jid, j in jobs.items():
            n = j.get("name") or ""
            if not n:
                continue
            started = j.get("started", 0)
            if n not in last or started > last[n]["started"]:
                last[n] = {
                    "job_id": jid, "status": j.get("status", "?"),
                    "started": started, "elapsed": j.get("elapsed", 0),
                }
        items = [{"name": n, "last_build": last.get(n)} for n in names]
        return {"dags": names, "items": items}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/dags/{name}")
async def api_dags_get(name: str, request: Request):
    require_auth(request)
    try:
        from lib.dag_schema import load_dag
        d = load_dag(_ws(), name)
        if not d:
            return JSONResponse({"error": "not found"}, status_code=404)
        return d
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.put("/api/dags/{name}")
async def api_dags_save(name: str, request: Request):
    require_auth(request)
    if not _validate_dag_name(name):
        return JSONResponse({"error": "invalid DAG name (allowed: A-Za-z0-9_-., 不可路径穿越)"}, status_code=400)
    try:
        from lib.dag_schema import save_dag, validate_dag_json
        body = await request.json()
        ok, errs = validate_dag_json(body)
        if not ok:
            return JSONResponse({"error": "invalid DAG", "details": errs}, status_code=400)
        fp = save_dag(_ws(), name, body)
        from lib.audit import audit_log as _audit_log
        _audit_log(request, "dag.save", name, steps=len(body.get("steps", [])))
        return {"ok": True, "name": name, "path": str(fp)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/api/dags/{name}")
async def api_dags_delete(name: str, request: Request):
    require_auth(request)
    if not _validate_dag_name(name):
        return JSONResponse({"error": "invalid DAG name"}, status_code=400)
    try:
        fp = _ws() / "dags" / f"{name}.json"
        if fp.exists():
            fp.unlink()
            from lib.audit import audit_log as _audit_log
            _audit_log(request, "dag.delete", name)
            return {"ok": True}
        return JSONResponse({"error": "not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/dags/{name}/run")
async def api_dags_run(name: str, request: Request):
    """启动 DAG → 后台运行, 立即返回 job_id."""
    require_auth(request)
    # [P0-#4] DAG start 节流: 20 次 / 60s / IP
    from lib.rate_limit import rate_check as _rate_check
    _rate_check(request, "dag_run", 20, 60)
    try:
        ret = await _wui()._start_dag_internal(name)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if ret.get("ok"):
        from lib.audit import audit_log as _audit_log
        _audit_log(request, "dag.run", name, job_id=ret.get("job_id", ""))
        return {k: v for k, v in ret.items() if k != "status_code"}
    return JSONResponse({"error": ret.get("error", "?")}, status_code=ret.get("status_code", 500))


@router.post("/api/dags/{name}/dryrun")
async def api_dags_dryrun(name: str, request: Request):
    """Dry-run — 校验 DAG + 拓扑排序 + 环检测, 不执行. 前端"🧪 干跑"按钮触发."""
    require_auth(request)
    if not _validate_dag_name(name):
        return JSONResponse({"error": "invalid DAG name"}, status_code=400)
    from lib.dag_schema import load_dag, validate_dag_json
    d = load_dag(_ws(), name)
    if not d:
        return JSONResponse({"error": "DAG not found"}, status_code=404)

    ok, errs = validate_dag_json(d)
    warnings: list[str] = []

    steps = d.get("steps", []) or []
    id_set = {s.get("id") for s in steps if s.get("id")}
    in_deg = {sid: 0 for sid in id_set}
    graph_map: dict[str, list[str]] = {sid: [] for sid in id_set}
    for s in steps:
        sid = s.get("id")
        for dep in (s.get("depends_on") or []):
            if dep in id_set and sid in id_set:
                in_deg[sid] += 1
                graph_map[dep].append(sid)

    order: list[str] = []
    queue = [sid for sid, d0 in in_deg.items() if d0 == 0]
    queue.sort()
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in graph_map.get(cur, []):
            in_deg[nxt] -= 1
            if in_deg[nxt] == 0:
                queue.append(nxt)
        queue.sort()

    unreached = sorted(id_set - set(order))
    if unreached:
        errs.append(f"环/未达节点: {', '.join(unreached)}")

    for s in steps:
        if s.get("agent_type") == "tool" and not s.get("tool_name"):
            errs.append(f"step {s.get('id')} 类型 tool 但缺 tool_name")
        if s.get("agent_type") not in {"tool"} and s.get("tool_name"):
            warnings.append(f"step {s.get('id')} 非 tool 类型但填了 tool_name (忽略)")
        if isinstance(s.get("timeout"), int) and s["timeout"] > 3600:
            warnings.append(f"step {s.get('id')} timeout={s['timeout']}s > 1h, 是否过长?")

    return {
        "ok": ok and not unreached,
        "step_count": len(steps),
        "order": order,
        "errors": errs,
        "warnings": warnings,
    }
