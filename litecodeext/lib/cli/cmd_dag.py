"""dag 子命令组: DAG 列表/运行/自然语言生成·修改·删除·定时 + 运行记录 (job) 详情/日志/产物/中止/断点 (从 litecli.py 拆出)."""
import json
import time
from pathlib import Path
from typing import Optional

import click

from .common import _c, _err, _ok, _info, _req, _r_json
from .common import _json_out, _none_if_empty


# ── dag ────────────────────────────────────────────────────
@click.group()
def dag():
    """DAG 编排."""


@dag.command("list")
def dag_list():
    d = _r_json("GET", "/api/dags")
    if _json_out(d):
        return
    for x in _none_if_empty(d.get("dags", [])):
        if isinstance(x, dict):
            click.echo(f"  {_c(x.get('name', '?'), 'cyan')}  steps={len(x.get('steps', []))}")
        else:
            click.echo(f"  {_c(str(x), 'cyan')}")


@dag.command("show")
@click.argument("name")
def dag_show(name: str):
    d = _r_json("GET", f"/api/dags/{name}")
    click.echo(json.dumps(d, ensure_ascii=False, indent=2))


@dag.command("run")
@click.argument("name")
def dag_run(name: str):
    d = _r_json("POST", f"/api/dags/{name}/run")
    if d.get("ok"):
        _ok(f"已启动 job_id={d.get('job_id')} step_count={d.get('step_count')}")
    else:
        _err(d.get("error", "启动失败"))


@dag.command("rm")
@click.argument("name")
def dag_rm(name: str):
    _r_json("DELETE", f"/api/dags/{name}")
    _ok(f"已删除 {name}")


@dag.command("jobs")
@click.option("-n", "--limit", type=int, default=None, help="最多显示 N 条 (服务端本身只返回最近 20 条)")
def dag_jobs(limit: Optional[int] = None):
    d = _r_json("GET", "/api/dags/jobs")
    if limit is not None and isinstance(d.get("jobs"), list):
        d = {**d, "jobs": d["jobs"][:max(0, limit)]}
    if _json_out(d):
        return
    for j in _none_if_empty(d.get("jobs", [])):
        ts = time.strftime("%m-%d %H:%M", time.localtime(j.get("started", 0)))
        status_color = {"done": "green", "running": "yellow", "failed": "red"}.get(j.get("status", ""), "gray")
        click.echo(f"  {_c(j['job_id'], 'cyan')}  {j.get('name', '')}  {_c(j.get('status', '?'), status_color)}  {ts}")


@dag.command("nl-gen")
@click.argument("request", nargs=-1, required=True)
@click.option("--name", default="", help="生成后保存为该名称")
def dag_nl_gen(request: tuple[str, ...], name: str):
    """自然语言生成 DAG. 例: litecli dag nl-gen 抓 hackernews 排序发到微信"""
    req = " ".join(request).strip()
    d = _r_json("POST", "/api/dags/generate", json_body={"request": req})
    steps = d.get("steps", [])
    if not steps:
        _err(d.get("error") or "生成失败")
    _ok(f"生成 {len(steps)} 步")
    for s in steps:
        deps = ",".join(s.get("depends_on") or []) or "-"
        click.echo(f"  {_c(s.get('id', '?'), 'cyan')}  [{s.get('agent_type', '?')}]  {s.get('task', '')[:60]}  ← {deps}")
    if name:
        _r_json("PUT", f"/api/dags/{name}", json_body=d)
        _ok(f"已保存为 {_c(name, 'cyan')}")


@dag.command("nl-edit")
@click.argument("name")
@click.argument("instruction", nargs=-1, required=True)
@click.option("--save/--dry", default=False, help="--save 直写文件, 默认 dry-run 只返回新版")
def dag_nl_edit(name: str, instruction: tuple[str, ...], save: bool):
    """自然语言修改已有 DAG. 例: litecli dag nl-edit weekly 把最后一步换成写周报"""
    instr = " ".join(instruction).strip()
    d = _r_json("POST", f"/api/dags/{name}/nl_edit", json_body={"instruction": instr, "save": save})
    if not d.get("ok"):
        _err(d.get("error") or "修改失败")
    dag_new = d.get("dag") or {}
    steps = dag_new.get("steps", [])
    _ok(f"修改后 {len(steps)} 步 ({'已保存' if d.get('saved') else 'dry-run'})")
    for s in steps:
        deps = ",".join(s.get("depends_on") or []) or "-"
        click.echo(f"  {_c(s.get('id', '?'), 'cyan')}  [{s.get('agent_type', '?')}]  {s.get('task', '')[:60]}  ← {deps}")


@dag.command("nl-rm")
@click.argument("query", nargs=-1, required=True)
@click.option("-y", "--yes", is_flag=True, help="确认删除, 不加只列候选")
def dag_nl_rm(query: tuple[str, ...], yes: bool):
    """自然语言删 DAG. 默认列候选, -y 才真删. 例: litecli dag nl-rm pptx"""
    q = " ".join(query).strip()
    d = _r_json("POST", "/api/dags/nl_delete", json_body={"query": q, "confirm": yes})
    cands = d.get("candidates", [])
    if not cands:
        _err(d.get("message") or "无匹配")
    if not yes:
        _info(f"匹配 {len(cands)} 个 (加 -y 才真删):")
        for c in cands:
            click.echo(f"  · {_c(c['name'], 'cyan')}  [{c['match']}]  {c.get('description', '')}")
    else:
        deleted = d.get("deleted", [])
        _ok(f"已删 {len(deleted)} 个: {', '.join(deleted)}")


@dag.command("nl-cron")
@click.argument("name")
@click.argument("when", nargs=-1, required=True)
def dag_nl_cron(name: str, when: tuple[str, ...]):
    """自然语言给 DAG 定时. 例: litecli dag nl-cron weekly 每周一早上 9 点"""
    w = " ".join(when).strip()
    d = _r_json("POST", "/api/dags/nl_schedule", json_body={"name": name, "when": w})
    if not d.get("ok"):
        _err(d.get("error") or "定时创建失败")
    t = d.get("timer") or {}
    _ok(f"已创建定时 {_c(t.get('id', '?'), 'cyan')}  cron={_c(d.get('cron', ''), 'yellow')}  ({d.get('human', '')})")


# ── DAG 运行记录 (job): 详情 / 时间线 / 产物 / 中止 / 断点 ───────────────
# 接口见 routers/dag_router.py 的 /api/dags/jobs/{job_id}/... ; job 不存在时服务端回 404 {"error": "job not found"}
_JOB_COLOR = {"done": "green", "running": "yellow", "failed": "red", "aborted": "red"}


def _fmt_ts(ts, fmt: str = "%m-%d %H:%M:%S") -> str:
    return time.strftime(fmt, time.localtime(ts)) if ts else "-"


def _step_mark(a: dict, job_status: str = "running") -> str:
    """单步状态标记: 与 Web Build 详情 / dag log 的口径一致 (条件不满足 = 跳过)."""
    err = a.get("error") or ""
    if a.get("success"):
        return _c("✓", "green")
    if "条件不满足" in err:
        return _c("⏭", "gray")
    if err:
        return _c("✗", "red")
    # 无结果: job 还在跑 = 执行中; job 已结束 (如被中止) = 未完成
    return _c("▶", "yellow") if job_status == "running" else _c("■", "gray")


@dag.command("job")
@click.argument("job_id")
def dag_job(job_id: str):
    """查看一次 DAG 运行的详情与每步状态."""
    d = _r_json("GET", f"/api/dags/jobs/{job_id}")
    if _json_out(d):
        return
    st = d.get("status", "?")
    elapsed = float(d.get("elapsed") or 0)
    if st == "running" and not elapsed and d.get("started"):   # 运行中服务端还没写 elapsed, 按开始时间算
        elapsed = max(0.0, time.time() - float(d["started"]))
    click.echo(_c(f"=== {d.get('name', '?')}  job {job_id} ===", "bold"))
    click.echo(f"  状态: {_c(st, _JOB_COLOR.get(st, 'gray'))}   开始: {_fmt_ts(d.get('started'))}"
               f"   耗时: {elapsed:.1f}s   步数: {d.get('step_count', '?')}")
    bps = d.get("breakpoints") or []
    click.echo(f"  断点: {', '.join(bps) or '-'}   暂停于: {d.get('paused_at') or '-'}")
    click.echo(_c("  步骤:", "cyan"))
    for a in _none_if_empty(d.get("agents", [])):
        sid = a.get("step_id") or a.get("label") or "?"
        line = f"    {_step_mark(a, st)} {_c(sid, 'cyan')}  [{a.get('agent_type', '?')}]  {float(a.get('elapsed') or 0):.1f}s"
        if a.get("error"):
            line += f"  {_c(str(a['error'])[:120], 'red')}"
        click.echo(line)
        prev = (a.get("output_preview") or "").strip().replace("\n", " ")
        if prev:
            click.echo(_c(f"        {prev[:160]}", "gray"))
    fo = (d.get("final_output") or "").strip()
    if fo:
        click.echo(_c("  最终输出:", "cyan"))
        click.echo(f"    {fo[:500]}")


@dag.command("log")
@click.argument("job_id")
def dag_log(job_id: str):
    """查看一次 DAG 运行的执行时间线 (每步开始/完成/耗时)."""
    d = _r_json("GET", f"/api/dags/jobs/{job_id}/log")
    if _json_out(d):
        return
    click.echo(d.get("text", ""))
    if not d.get("events"):
        _none_if_empty([])


@dag.command("artifacts")
@click.argument("job_id")
@click.option("--download", "download_dir", default=None, metavar="DIR",
              help="把文件类产物下载到 DIR (链接类产物只列出, 不下载)")
def dag_artifacts(job_id: str, download_dir: Optional[str]):
    """列出一次 DAG 运行的产出物 (文件/链接), 可选下载."""
    d = _r_json("GET", f"/api/dags/jobs/{job_id}/artifacts")
    arts = d.get("artifacts", []) or []
    if download_dir:
        d["downloaded"], failed = _download_artifacts(arts, Path(download_dir))
    else:
        failed = 0
    if not _json_out(d):
        for a in _none_if_empty(arts):
            stage = _c(f"[{a.get('stage', '')}]", "gray")
            if a.get("kind") == "file":
                click.echo(f"  📄 {_c(a.get('name', '?'), 'cyan')}  {a.get('size', '?')}B  {a.get('path', '')}  {stage}")
            else:
                click.echo(f"  🔗 {_c(a.get('name', '?'), 'cyan')}  {a.get('url', '')}  {stage}")
        if download_dir:
            for p in d["downloaded"]:
                click.echo(f"  ↓ {p}")
            _ok(f"已下载 {len(d['downloaded'])} 个文件 → {download_dir}" + (f", 失败 {failed} 个" if failed else ""))
    if failed:
        raise SystemExit(1)


def _download_artifacts(arts: list, out_dir: Path):
    """下载文件类产物; 返回 (已保存路径列表, 失败数)。失败逐条打到 stderr, 不中断其余下载。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    saved, failed, used = [], 0, set()
    for a in arts:
        if a.get("kind") != "file":
            continue
        name = a.get("name") or Path(a.get("path", "artifact")).name
        if name in used:                       # 不同 stage 同名文件: 加 stage 前缀避免互相覆盖
            name = f"{a.get('stage', 'stage')}_{name}"
        used.add(name)
        # 产物是服务端绝对路径, 走白名单下载端点 /api/media (workspace 内文件可下)
        r = _req("GET", "/api/media", params={"path": a.get("path", "")}, timeout=120)
        if r.status_code != 200:
            failed += 1
            click.echo(_c(f"✗ {a.get('path')} → HTTP {r.status_code}", "red"), err=True)
            continue
        (out_dir / name).write_bytes(r.content)
        saved.append(str(out_dir / name))
    return saved, failed


@dag.command("abort")
@click.argument("job_id")
@click.option("-y", "--yes", is_flag=True, help="跳过确认直接中止")
def dag_abort(job_id: str, yes: bool):
    """中止一个运行中的 DAG job (不加 -y 会先确认)."""
    if not yes:
        click.confirm(f"确认中止 DAG job {job_id}?", abort=True)
    d = _r_json("POST", f"/api/dags/jobs/{job_id}/abort")
    if d.get("ok"):
        _ok(f"已发送中止 {_c(job_id, 'cyan')} (status={d.get('status', 'aborting')})")
    else:
        _info(f"job {job_id} 不在运行 (status={d.get('already', '?')}), 无需中止")


@dag.command("breakpoints")
@click.argument("job_id")
def dag_breakpoints(job_id: str):
    """查看 job 的断点及当前暂停位置."""
    d = _r_json("GET", f"/api/dags/jobs/{job_id}/breakpoints")
    if _json_out(d):
        return
    click.echo(f"  暂停于: {_c(d.get('paused_at') or '-', 'yellow')}")
    for b in _none_if_empty(d.get("breakpoints", [])):
        click.echo(f"  ● {_c(b, 'cyan')}")


@dag.command("break")
@click.argument("job_id")
@click.argument("node_id")
@click.option("--clear", is_flag=True, help="取消该节点断点 (若正停在此处则同时放行)")
def dag_break(job_id: str, node_id: str, clear: bool):
    """给运行中 job 的某节点设断点 (执行到该节点前暂停)."""
    d = _r_json("POST", f"/api/dags/jobs/{job_id}/breakpoints/{node_id}", json_body={"set": not clear})
    if _json_out(d):
        return
    _ok(f"{'已取消' if clear else '已设置'}断点 {_c(node_id, 'cyan')}  "
        f"当前断点: {', '.join(d.get('breakpoints') or []) or '-'}  暂停于: {d.get('paused_at') or '-'}")


@dag.command("resume")
@click.argument("job_id")
@click.argument("node_id")
def dag_resume(job_id: str, node_id: str):
    """放行停在断点 NODE_ID 的 job, 继续执行."""
    d = _r_json("POST", f"/api/dags/jobs/{job_id}/resume/{node_id}")
    if d.get("ok"):
        _ok(f"已继续 {_c(job_id, 'cyan')} @ {node_id}")
    else:
        _err(d.get("error") or "继续失败")


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [dag]
