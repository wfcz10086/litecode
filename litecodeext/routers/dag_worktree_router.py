"""dag_worktree_router.py — DAG 运行记录里的子代理工作树 (前端 web_assets/dag_worktrees.js)。

  GET  /api/dags/jobs/{job_id}/worktrees                    本次运行的工作树列表 (分支 / 合并状态 / 冲突文件)
  GET  /api/dags/jobs/{job_id}/worktrees/{wid}/diff         只读 diff 文本 (截断)
  POST /api/dags/jobs/{job_id}/worktrees/{wid}/discard      丢弃未合并的工作树, 两步: body {"confirm": "<wid>"}

数据来源: saved DAG 运行时 (dag_state._start_dag_internal, web_ui 进程) 经 subagent_worktree.wrap_saved_dag
写的 job["worktrees"]; 工作树元数据在工作区磁盘 (.agent_worktrees/_meta), 故本进程直接处理, 不需转发到 server。
diff / discard 复用 agent_worktree 工具同一实现 (subagent_worktree.agent_worktree_tool), 不复制 git 逻辑。
安全: 只允许操作该运行记录里列出的工作树 id, 且元数据里的分支必须与记录一致 (lc-agent/ 前缀), 防任意分支名。
"""
import re
import time

from fastapi import APIRouter, HTTPException, Request

from routers.auth_router import require_auth

router = APIRouter()
ORDER = 108  # 字面量 jobs/... 路径, 与 dag_router 的 {name} 通配不重叠; 放在其前更稳

DIFF_MAX = 8000          # 与 agent_worktree diff 的截断一致
_WID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _wui():
    import web_ui  # type: ignore
    return web_ui


def _wt():
    from lib.agent import subagent_worktree as wt
    return wt


def _job(job_id: str) -> dict:
    job = _wui()._DAG_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _live(row: dict) -> dict:
    """把运行记录里的行与磁盘元数据合并 (界面/工具丢弃后状态以元数据为准)。"""
    wt = _wt()
    out = dict(row)
    meta = wt.load_meta(row.get("id", "")) if row.get("id") else None
    if meta and meta.get("branch") == row.get("branch"):
        for k in ("status", "note", "conflicts", "files", "diffstat", "path"):
            if meta.get(k) not in (None, "", []):
                out[k] = meta[k]
    st = out.get("status")
    out["status_cn"] = wt._STATUS_CN.get(st, st)
    out["unmerged"] = st not in wt._DONE_STATUSES
    out["can_discard"] = bool(out["unmerged"] and st != "running")
    return out


def _row_for(job: dict, wid: str) -> dict:
    """白名单: wid 必须在本运行记录里, 元数据分支必须与记录一致且是 lc-agent/ 分支。"""
    wt = _wt()
    if not _WID_RE.match(wid or ""):
        raise HTTPException(400, "非法工作树 id")
    row = next((w for w in job.get("worktrees") or [] if w.get("id") == wid), None)
    if row is None:
        raise HTTPException(404, "该运行记录里没有这个工作树")
    meta = wt.load_meta(wid)
    br = row.get("branch") or ""
    if not meta or meta.get("branch") != br or not br.startswith(wt.BRANCH_PREFIX):
        raise HTTPException(409, "工作树元数据与运行记录不一致, 拒绝操作")
    return row


@router.get("/api/dags/jobs/{job_id}/worktrees")
async def api_dag_job_worktrees(job_id: str, request: Request):
    require_auth(request)
    job = _job(job_id)
    rows = [_live(w) for w in job.get("worktrees") or []]
    return {"job_id": job_id, "worktrees": rows,
            "unmerged": sum(1 for r in rows if r["unmerged"]),
            "notes": list(job.get("worktree_notes") or [])}


@router.get("/api/dags/jobs/{job_id}/worktrees/{wid}/diff")
async def api_dag_job_worktree_diff(job_id: str, wid: str, request: Request):
    require_auth(request)
    _row_for(_job(job_id), wid)
    text = await _wt().agent_worktree_tool({"action": "diff", "id": wid})
    if len(text) > DIFF_MAX + 200:   # 工具已截到 8000 + 提示行; 兜底再截一次
        text = text[:DIFF_MAX] + f"\n...[截断, 共 {len(text)} 字符]"
    return {"id": wid, "diff": text}


@router.post("/api/dags/jobs/{job_id}/worktrees/{wid}/discard")
async def api_dag_job_worktree_discard(job_id: str, wid: str, request: Request):
    require_auth(request)
    job = _job(job_id)
    row = _row_for(job, wid)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict) or body.get("confirm") != wid:
        # 两步确认: 第一步只返回待确认, 不动分支; 前端第二次点击带 confirm=<wid> 才真丢弃
        raise HTTPException(400, "需要二次确认: body 带 {\"confirm\": \"<工作树 id>\"}")
    cur = _live(row)
    if not cur["can_discard"]:
        return {"ok": False, "id": wid, "status": cur.get("status"), "status_cn": cur.get("status_cn"),
                "message": f"工作树状态为 {cur.get('status_cn')}, 无需丢弃"}
    wt = _wt()
    msg = await wt.agent_worktree_tool({"action": "discard", "id": wid})
    meta = wt.load_meta(wid) or {}
    if meta.get("status") != "discarded":
        raise HTTPException(500, f"丢弃失败: {msg[:300]}")
    # 运行记录同步 (重算 worktree_unmerged + exec_log 记一笔) 并落盘, 刷新页面也看得到
    rep = dict(row, status="discarded", note="界面丢弃: 工作树与分支已删除")
    wt.record_dag_report(job, rep)
    if job.get("exec_log"):
        job["exec_log"][-1]["by"] = "web"
    job["worktree_discarded_at"] = time.time()
    try:
        _wui()._dag_job_save(job_id)
    except Exception:
        pass
    return {"ok": True, "id": wid, "status": "discarded", "status_cn": wt._STATUS_CN["discarded"],
            "message": msg[:500]}
