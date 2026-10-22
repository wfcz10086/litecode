#!/usr/bin/env python3
"""quickbench — LiteCode 10 分钟快测。

用法:
  python3 scripts/quickbench/run.py [--model ID] [--only CATEGORY|TASK_ID,...] [--concurrency 8]
                                    [--repeat 1] [--keep-files] [--tag NAME]

流程: 准备夹具 → 每题一个新会话 (qb- 前缀) 打网关 /v1/chat/completions (非流式, 一整轮 agent)
     → 解析会话 JSON + iteration_full.log 拿工具调用 → 判分 → 写 report.json / report.md
     → 清理 qb- 会话 / session_models 条目 / 任务文件。退出码恒为 0 (报告就是产物)。
网关 token 运行时从 config.json 读, 不打印、不落盘。
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import secrets
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import graders as G  # noqa: E402
import tasks as T  # noqa: E402

ROOT = HERE.parent.parent                       # /opt/litecode
CONFIG = ROOT / "config.json"
WS_HOST = ROOT / "workspace"                    # == 容器 /tmp/litecode_workspace
WS_CONT = "/tmp/litecode_workspace"
SESS_HOST = ROOT / "sessions"                   # == 容器 /tmp/litecode_workspace/sessions
ITER_LOG = WS_HOST / "logs" / "iteration_full.log"
REPORTS = ROOT / "reports" / "quickbench"
GATEWAY = "http://127.0.0.1:18789"
CONTAINER = "litecode"
WALL_BUDGET = 600


# ── 小工具 ──────────────────────────────────────────────────────

def _token() -> str:
    return json.loads(CONFIG.read_text())["server"]["token"]


def _cfg_models() -> tuple[list[str], str]:
    c = json.loads(CONFIG.read_text())
    return [m.get("id") for m in c.get("models", []) if isinstance(m, dict)], c.get("default_model_id", "")


def _dexec(args: list[str], stdin: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "exec", "-i", CONTAINER, *args], input=stdin,
                          capture_output=True, text=True, timeout=timeout)


_SM_SCRIPT = r"""
import json, os, sys, tempfile
p = "/tmp/litecode_workspace/session_models.json"
mode, model, sids = sys.argv[1], sys.argv[2], sys.argv[3:]
try:
    d = json.load(open(p))
    if not isinstance(d, dict): d = {}
except Exception:
    d = {}
for s in sids:
    if mode == "set": d[s] = model
    else: d.pop(s, None)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".session_models.")
with os.fdopen(fd, "w") as f: json.dump(d, f, ensure_ascii=False)
os.chmod(tmp, 0o644); os.replace(tmp, p)
print(len(d))
"""


def session_models(mode: str, model: str, sids: list[str]) -> str:
    """mode=set|clear。在容器里原子改 session_models.json (宿主无权写 root 文件)。"""
    if not sids:
        return ""
    r = _dexec(["python3", "-c", _SM_SCRIPT, mode, model or "-", *sids])
    if r.returncode != 0:
        raise RuntimeError(f"session_models {mode} 失败: {r.stderr[-300:]}")
    return r.stdout.strip()


class _QuietHandler(SimpleHTTPRequestHandler):
    hits: list = []

    def log_message(self, *a):  # noqa: D401
        pass

    def do_GET(self):
        self.hits.append(self.path)
        return super().do_GET()

    def do_POST(self):
        self.hits.append(self.path)
        self.send_response(204)
        self.end_headers()


def start_http(root: Path) -> tuple[ThreadingHTTPServer, dict]:
    root.mkdir(parents=True, exist_ok=True)
    hits: list[str] = []
    handler = type("H", (_QuietHandler,), {"hits": hits})

    def factory(*a, **kw):
        return handler(*a, directory=str(root), **kw)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), factory)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, {"port": srv.server_address[1], "hits": hits, "root": str(root)}


@dataclass
class TaskCtx:
    run_id: str
    hdir: Path
    cdir: str
    rng: random.Random
    http: dict
    ws_host: Path = WS_HOST
    ws_cont: str = WS_CONT


@dataclass
class Inst:
    task: dict
    rep: int
    sid: str
    ctx: TaskCtx
    state: dict = field(default_factory=dict)
    res: dict = field(default_factory=dict)
    passed: bool = False
    reason: str = ""

    @property
    def key(self) -> str:
        return self.task["id"] + (f"#r{self.rep}" if self.rep else "")


# ── 跑一题 ──────────────────────────────────────────────────────

def run_inst(inst: Inst, token: str) -> None:
    hdrs = {"Authorization": f"Bearer {token}"}
    timeout = float(inst.task.get("timeout", 180))
    t0 = time.time()
    answers, status, err = [], "ok", ""
    with httpx.Client(timeout=None) as cli:
        for prompt in inst.state["turns"]:
            left = timeout - (time.time() - t0)
            if left <= 1:
                status = "timeout"
                break
            body = {"user": inst.sid, "session_id": inst.sid, "stream": False,
                    "messages": [{"role": "user", "content": prompt}]}
            try:
                r = cli.post(f"{GATEWAY}/v1/chat/completions", json=body, headers=hdrs,
                             timeout=httpx.Timeout(left, connect=10))
                if r.status_code != 200:
                    status, err = "error", f"HTTP {r.status_code}: {r.text[:120]}"
                    break
                answers.append(r.json()["choices"][0]["message"]["content"] or "")
            except httpx.TimeoutException:
                status = "timeout"
                try:   # 让服务端在下一次迭代时停下, 别继续占 GPU
                    cli.post(f"{GATEWAY}/v1/sessions/{inst.sid}/interrupt", headers=hdrs, timeout=10)
                except Exception:
                    pass
                break
            except Exception as e:
                status, err = "error", f"{type(e).__name__}: {str(e)[:120]}"
                break
    inst.res = {"answers": answers, "answer": answers[-1] if answers else "",
                "wall": round(time.time() - t0, 1), "status": status, "error": err}


# ── 事后解析: 会话 JSON + iteration_full.log ─────────────────────

def parse_session(sid: str) -> tuple[list[dict], list[str]]:
    p = SESS_HOST / f"{sid}.json"
    calls, results = [], []
    try:
        d = json.loads(p.read_text())
    except Exception:
        return calls, results
    for m in d.get("msgs", []):
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = (tc or {}).get("function") or {}
                calls.append({"name": fn.get("name", "?"), "args": str(fn.get("arguments", ""))[:2000]})
        elif m.get("role") == "tool":
            c = m.get("content")
            results.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)[:4000])
    return calls, results


def parse_log(offset: int, sids: set[str]) -> dict[str, list[dict]]:
    """返回 {sid: [TOOL_EXEC dict...]}。TRACE_START 里的 session_id 只记前 12 位, qb sid 本身 ≤12 位。"""
    out: dict[str, list[dict]] = collections.defaultdict(list)
    try:
        with open(ITER_LOG, "rb") as f:
            f.seek(offset)
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return out
    trace2sid: dict[str, str] = {}
    pref = {s[:12]: s for s in sids}
    for line in data.splitlines():
        if line.startswith("[TRACE_START] "):
            try:
                d = json.loads(line[14:])
            except Exception:
                continue
            s = pref.get(str(d.get("session_id", ""))[:12])
            if s:
                trace2sid[d.get("trace_id")] = s
        elif line.startswith("[TOOL_EXEC] "):
            try:
                d = json.loads(line[12:])
            except Exception:
                continue
            s = trace2sid.get(d.get("trace_id"))
            if s:
                out[s].append({"tool": d.get("tool"), "elapsed_s": d.get("elapsed_s"),
                               "is_error": d.get("is_error"), "head": str(d.get("result_head", ""))[:160]})
    return out


def enrich(inst: Inst, logmap: dict) -> None:
    calls, results = parse_session(inst.sid)
    logs = logmap.get(inst.sid, [])
    names = [c["name"] for c in calls]
    for e in logs:   # 子代理等不进主会话 JSON 的调用, 从日志补名字
        if e["tool"] and e["tool"] not in names:
            names.append(e["tool"])
    heads = [e["head"] for e in logs]
    inst.res.update({
        "tool_calls": calls,
        "tool_results": results,
        "tool_names": names,
        "n_tool_calls": max(len(calls), len(logs)),
        "tool_time_s": round(sum(float(e["elapsed_s"] or 0) for e in logs), 1),
        "timeouts": G.find_timeouts(results + heads),
        "errors": G.find_errors(results) or [h for h, e in zip(heads, logs) if e["is_error"]],
    })


def grade_inst(inst: Inst) -> None:
    if inst.res.get("status") == "error" and not inst.res.get("answer"):
        inst.passed, inst.reason = False, "请求失败: " + inst.res.get("error", "")
        return
    try:
        ok, why = inst.task["grade"](inst.ctx, inst.state, inst.res)
    except Exception as e:
        ok, why = False, f"判分异常 {type(e).__name__}: {str(e)[:80]}"
    if inst.res.get("status") == "timeout" and not ok:
        why = f"超时({inst.task.get('timeout')}s) · " + why
    inst.passed, inst.reason = bool(ok), why


# ── 报告 ────────────────────────────────────────────────────────

def summarize(insts: list[Inst]) -> dict:
    cats = {}
    for c in T.CAT_WEIGHTS:
        xs = [i for i in insts if i.task["cat"] == c]
        if not xs:
            continue
        cats[c] = {"name": T.CAT_NAMES[c], "weight": T.CAT_WEIGHTS[c], "n": len(xs),
                   "passed": sum(i.passed for i in xs),
                   "score": round(100 * sum(i.passed for i in xs) / len(xs), 1),
                   "avg_wall_s": round(sum(i.res.get("wall", 0) for i in xs) / len(xs), 1),
                   "avg_tool_calls": round(sum(i.res.get("n_tool_calls", 0) for i in xs) / len(xs), 1)}
    wsum = sum(v["weight"] for v in cats.values()) or 1
    overall = round(sum(v["score"] * v["weight"] for v in cats.values()) / wsum, 1)
    return {"overall": overall, "categories": cats,
            "avg_wall_s": round(sum(i.res.get("wall", 0) for i in insts) / max(1, len(insts)), 1),
            "avg_tool_calls": round(sum(i.res.get("n_tool_calls", 0) for i in insts) / max(1, len(insts)), 1),
            "n": len(insts), "passed": sum(i.passed for i in insts)}


def find_previous(run_id: str) -> dict | None:
    if not REPORTS.exists():
        return None
    cands = sorted((p for p in REPORTS.iterdir() if p.is_dir() and p.name != run_id
                    and (p / "report.json").exists()), key=lambda p: p.name)
    for p in reversed(cands):
        try:
            d = json.loads((p / "report.json").read_text())
        except Exception:
            continue
        if not d.get("only"):
            return d
    return None


def compare(cur: dict, prev: dict | None) -> dict | None:
    if not prev:
        return None
    pc, cc = prev["summary"]["categories"], cur["summary"]["categories"]
    cat_delta = {c: round(cc[c]["score"] - pc[c]["score"], 1) for c in cc if c in pc}

    def by_task(rep):
        agg = collections.defaultdict(list)
        for t in rep["tasks"]:
            agg[t["id"]].append(t["passed"])
        return {k: sum(v) / len(v) >= 0.5 for k, v in agg.items()}
    pt, ct = by_task(prev), by_task(cur)
    flips = [{"id": k, "from": pt[k], "to": ct[k]} for k in ct if k in pt and pt[k] != ct[k]]
    return {"prev_run": prev["run_id"], "prev_model": prev.get("model"),
            "overall_delta": round(cur["summary"]["overall"] - prev["summary"]["overall"], 1),
            "cat_delta": cat_delta, "flips": flips,
            "same_taskset": prev.get("task_digest") == cur.get("task_digest")}


def render_md(rep: dict) -> str:
    s = rep["summary"]
    L = [f"# LiteCode 快测报告 `{rep['run_id']}`", "",
         f"- 模型: `{rep['model']}`{' (会话级覆盖)' if rep['model_override'] else ' (全局默认)'}",
         f"- 开始: {rep['started']} · 总耗时 **{rep['wall_s']:.0f}s** (预算 {WALL_BUDGET}s"
         f"{', 超预算' if rep['wall_s'] > WALL_BUDGET else ''}) · 并发 {rep['concurrency']} · 重复 {rep['repeat']}",
         f"- 第三方题来源: {T.SIMPLEQA_SOURCE} (GAIA 为 gated 数据集无法获取)",
         f"- 噪声提示: 题少 (~30), 单次运行 ±10 分属正常波动; 只适合同一套题下 A/B 和回归, 不能和公开榜单比。", "",
         f"## 总分 **{s['overall']}** / 100  ({s['passed']}/{s['n']} 通过)", "",
         "| 类别 | 权重 | 通过 | 得分 | 平均耗时 s | 平均工具调用 |", "|---|---|---|---|---|---|"]
    for c, v in s["categories"].items():
        L.append(f"| {v['name']} | {int(v['weight'] * 100)}% | {v['passed']}/{v['n']} | {v['score']} | "
                 f"{v['avg_wall_s']} | {v['avg_tool_calls']} |")
    L.append(f"| 全部 | | {s['passed']}/{s['n']} | **{s['overall']}** | {s['avg_wall_s']} | {s['avg_tool_calls']} |")
    cmp_ = rep.get("comparison")
    L += ["", "## 与上次对比"]
    if cmp_:
        L.append(f"上次 `{cmp_['prev_run']}` (模型 `{cmp_['prev_model']}`) · 总分变化 **{cmp_['overall_delta']:+}**"
                 + ("" if cmp_["same_taskset"] else " · ⚠ 题集不同, 对比仅供参考"))
        L.append("")
        L.append(" · ".join(f"{T.CAT_NAMES[c]} {d:+}" for c, d in cmp_["cat_delta"].items()))
        if cmp_["flips"]:
            L += ["", "翻转的题:"]
            L += [f"- `{f['id']}`: {'✓' if f['from'] else '✗'} → {'✓' if f['to'] else '✗'}" for f in cmp_["flips"]]
        else:
            L.append("\n无翻转。")
    else:
        L.append("没有更早的报告。")
    fails = [t for t in rep["tasks"] if not t["passed"]]
    L += ["", f"## 失败列表 ({len(fails)})", ""]
    if fails:
        L += ["| 题 | 类别 | 原因 | 耗时 s | 工具调用 |", "|---|---|---|---|---|"]
        for t in fails:
            L.append(f"| `{t['key']}` | {T.CAT_NAMES[t['cat']]} | {t['reason'].replace('|', '/')} | "
                     f"{t['wall']} | {t['n_tool_calls']} |")
    L += ["", "## 全部题目", "", "| 题 | 说明 | 结果 | 耗时 s | 工具 | TIMEOUT/ERROR | 原因 |",
          "|---|---|---|---|---|---|---|"]
    for t in rep["tasks"]:
        tools = ",".join(f"{k}×{v}" if v > 1 else k for k, v in collections.Counter(t["tool_names_all"]).items())
        L.append(f"| `{t['key']}` | {t['desc']} | {'✓' if t['passed'] else '✗'} | {t['wall']} | "
                 f"{t['n_tool_calls']} {tools[:70]} | {len(t['timeouts'])}/{len(t['errors'])} | "
                 f"{t['reason'].replace('|', '/')[:90]} |")
    pi = rep.get("prod_impact", {})
    L += ["", "## 生产影响观测", "",
          f"- 运行期间 `docker logs litecode` 里 ERROR/Traceback 行: {pi.get('error_lines', '?')}"
          f" (工具结果回显 {pi.get('tool_error_echo_lines', '?')}, 服务端级 {pi.get('server_error_lines', '?')})",
          *[f"  - `{x[:160]}`" for x in pi.get("server_error_samples", [])],
          f"- 运行期间非 qb- 会话的 agent 轮次: {pi.get('other_traces', '?')}",
          f"- 清理: {rep.get('cleanup', {})}", ""]
    return "\n".join(L)


# ── 清理 / 生产影响 ──────────────────────────────────────────────

def cleanup(insts: list[Inst], run_id: str, token: str, keep_files: bool, override: bool) -> dict:
    info: dict = {}
    hdrs = {"Authorization": f"Bearer {token}"}
    deleted = 0
    for i in insts:
        try:
            r = httpx.delete(f"{GATEWAY}/v1/sessions/{i.sid}", headers=hdrs, timeout=15)
            deleted += r.status_code == 200
        except Exception:
            pass
    info["sessions_deleted"] = deleted
    # 会话 JSON + 会话目录 + web_ui 自动导入到侧栏的副本 (web_ui.list_sessions 会把 API 会话导入 web_sessions)
    sid_paths = ([f"{WS_CONT}/sessions/{i.sid}" for i in insts] + [f"{WS_CONT}/sessions/{i.sid}.json" for i in insts]
                 + [f"/root/.litecode/web_sessions/{i.sid}.json" for i in insts])
    r = _dexec(["rm", "-rf", *sid_paths])
    info["session_dirs_removed"] = r.returncode == 0
    try:
        session_models("clear", "", [i.sid for i in insts])
        info["session_models_cleared"] = True
    except Exception as e:
        info["session_models_cleared"] = f"失败 {e}"
    # 夹具起的残留进程 (命令行带本次 run 目录)
    r = _dexec(["pkill", "-f", f"quickbench/{run_id}"])
    info["stray_procs"] = "已杀残留进程" if r.returncode == 0 else "无残留进程"
    if not keep_files:
        r = _dexec(["rm", "-rf", f"{WS_CONT}/quickbench/{run_id}", f"{WS_CONT}/quickbench/{run_id}_canary_outside"])
        info["task_files_removed"] = r.returncode == 0
    else:
        info["task_files_kept"] = str(WS_HOST / "quickbench" / run_id)
    return info


def prod_impact(since_iso: str, log_offset: int, sids: set[str]) -> dict:
    out: dict = {}
    try:
        r = subprocess.run(["docker", "logs", "--since", since_iso, CONTAINER],
                           capture_output=True, text=True, timeout=60)
        lines = (r.stdout + r.stderr).splitlines()
        err = [ln for ln in lines if "ERROR" in ln or "Traceback" in ln]
        # "↳ ERROR: ..." 是工具结果回显 (agent 正常试错), 不算服务端错误
        srv_err = [ln for ln in err if "↳" not in ln]
        out["error_lines"] = len(err)
        out["tool_error_echo_lines"] = len(err) - len(srv_err)
        out["server_error_lines"] = len(srv_err)
        out["server_error_samples"] = [ln[:200] for ln in srv_err][:5]
    except Exception as e:
        out["error_lines"] = f"读取失败 {e}"
    other = 0
    try:
        with open(ITER_LOG, "rb") as f:
            f.seek(log_offset)
            for ln in f.read().decode("utf-8", "replace").splitlines():
                if ln.startswith("[TRACE_START] "):
                    try:
                        s = json.loads(ln[14:]).get("session_id", "")
                    except Exception:
                        continue
                    if not str(s).startswith("qb-"):
                        other += 1
    except Exception:
        pass
    out["other_traces"] = other
    return out


# ── main ────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="LiteCode 10 分钟快测")
    ap.add_argument("--model", default="", help="会话级模型覆盖 (config.json models[].id), 不动全局默认")
    ap.add_argument("--only", default="", help="类别 third_party|coding|framework, 或逗号分隔的题 id")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--keep-files", action="store_true", help="保留任务目录 (默认判分后删除)")
    ap.add_argument("--tag", default="", help="附加到 run_id 的标签")
    ap.add_argument("--no-compare", action="store_true")
    a = ap.parse_args()
    a.concurrency = max(1, min(a.concurrency, 8))   # 生产在用, 上限 8

    token = _token()
    model_ids, default_model = _cfg_models()
    if a.model and a.model not in model_ids:
        print(f"[quickbench] 未知模型 {a.model}; 可选: {', '.join(model_ids)}")
        return 0

    tasks = T.all_tasks()
    if a.only:
        sel = {x.strip() for x in a.only.split(",") if x.strip()}
        tasks = [t for t in tasks if t["cat"] in sel or t["id"] in sel]
    if not tasks:
        print("[quickbench] 没有匹配的题")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_id = stamp + (f"-{a.tag}" if a.tag else "") + (f"-{a.model.replace('/', '_')}" if a.model else "")
    run_host = WS_HOST / "quickbench" / run_id
    run_host.mkdir(parents=True, exist_ok=True)
    # --only 的局部运行 (校准/调试) 放 _partial/, 不参与"与上次对比"
    out_dir = (REPORTS / "_partial" / run_id) if a.only else (REPORTS / run_id)
    rtag = secrets.token_hex(3)[:5]
    started_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    log_offset = ITER_LOG.stat().st_size if ITER_LOG.exists() else 0
    srv, http = start_http(run_host / "_http")

    # 准备夹具
    insts: list[Inst] = []
    idx = 0
    for rep in range(a.repeat):
        for t in tasks:
            sid = f"qb-{rtag}{idx:03d}"[:12]
            idx += 1
            sub = t["id"] + (f"_r{rep}" if rep else "")
            hdir = run_host / sub
            hdir.mkdir(parents=True, exist_ok=True)
            ctx = TaskCtx(run_id=run_id, hdir=hdir, cdir=f"{WS_CONT}/quickbench/{run_id}/{sub}",
                          rng=random.Random(f"{run_id}/{sub}"), http=http)
            inst = Inst(task=t, rep=rep, sid=sid, ctx=ctx)
            try:
                inst.state = t["setup"](ctx)
            except Exception as e:
                inst.res = {"status": "error", "error": f"夹具失败 {e}", "answer": "", "answers": [], "wall": 0}
                traceback.print_exc()
            insts.append(inst)
    sids = {i.sid for i in insts}
    if a.model:
        session_models("set", a.model, sorted(sids))
    model_used = a.model or default_model

    # 长题先跑, 缩短尾巴
    order = sorted([i for i in insts if i.state], key=lambda i: -i.task.get("timeout", 180))
    print(f"[quickbench] run={run_id} model={model_used} tasks={len(order)} conc={a.concurrency}", flush=True)
    t0 = time.time()
    wall = 0.0
    try:
        with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            futs = {ex.submit(run_inst, i, token): i for i in order}
            for f in as_completed(futs):
                i = futs[f]
                try:
                    f.result()
                except Exception as e:
                    i.res = {"status": "error", "error": str(e)[:200], "answer": "", "answers": [], "wall": 0}
                print(f"  [{time.time() - t0:5.0f}s] {i.key:<24} {i.res.get('status'):<7} {i.res.get('wall', 0):6.1f}s",
                      flush=True)
        wall = time.time() - t0

        time.sleep(2)   # 让会话 JSON / 日志落盘
        logmap = parse_log(log_offset, sids)
        for i in insts:
            if not i.res:
                i.res = {"status": "error", "error": "未运行", "answer": "", "answers": [], "wall": 0}
            enrich(i, logmap)
            grade_inst(i)
    finally:
        impact = prod_impact(started_iso, log_offset, sids)
        clean = cleanup(insts, run_id, token, a.keep_files, bool(a.model))
        srv.shutdown()

    rep = {"run_id": run_id, "model": model_used, "model_override": bool(a.model), "started": started_iso,
           "wall_s": round(wall, 1), "concurrency": a.concurrency, "repeat": a.repeat,
           "task_digest": T.task_digest(), "only": a.only, "third_party_source": T.SIMPLEQA_SOURCE,
           "summary": summarize(insts), "prod_impact": impact, "cleanup": clean,
           "tasks": [{"key": i.key, "id": i.task["id"], "cat": i.task["cat"], "desc": i.task["desc"],
                      "sid": i.sid, "passed": i.passed, "reason": i.reason,
                      "status": i.res.get("status"), "wall": i.res.get("wall", 0),
                      "n_tool_calls": i.res.get("n_tool_calls", 0),
                      "tool_names": sorted(set(i.res.get("tool_names", []))),
                      "tool_names_all": [c["name"] for c in i.res.get("tool_calls", [])] or i.res.get("tool_names", []),
                      "tool_time_s": i.res.get("tool_time_s", 0),
                      "timeouts": i.res.get("timeouts", []), "errors": i.res.get("errors", [])[:5],
                      "answer_tail": (i.res.get("answer") or "")[-300:]} for i in insts]}
    if not a.no_compare and not a.only:
        rep["comparison"] = compare(rep, find_previous(run_id))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    (out_dir / "report.md").write_text(render_md(rep))
    s = rep["summary"]
    print(f"[quickbench] 总分 {s['overall']} · " + " · ".join(
        f"{v['name']} {v['score']}" for v in s["categories"].values()) + f" · 耗时 {wall:.0f}s")
    print(f"[quickbench] 报告: {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
