#!/usr/bin/env python3
"""SWE-bench Verified 评测 — LiteCode 出补丁, 官方 harness 打分。

旧脚本 swe_bench.py 在宿主裸跑 pytest, 题目的老版本依赖装不上 (连 PASS_TO_PASS 都 0/13 全挂),
测出来的 0 分是环境坏了不是模型差。这里换成官方路线:

  1. 拉官方题目镜像 swebench/sweb.eval.x86_64.<id> (依赖装好, 仓库在 /testbed)
  2. 把 /testbed 拷到 litecode 容器能看见的 workspace/swe/<id>, 再起一个题目容器把这份目录挂回 /testbed
     → agent 用自己的文件工具改 workspace 里的文件, 用 swe_dexec.py 在题目容器里跑测试, 两边是同一份文件
  3. POST /v1/chat/completions 让 agent 解题 (只给 issue, 不给提示)
  4. 在题目容器里 git diff 取补丁 (去掉 agent 写的根目录草稿脚本和评审方测试文件)
  5. 官方 swebench.harness.run_evaluation 打分, 每题打完删镜像省磁盘

用法 (宿主上, 用装了 swebench<5 的 venv):
  /opt/build/swe-venv/bin/python tests/swe_official.py --run r1 --limit 20 --seed 42
  /opt/build/swe-venv/bin/python tests/swe_official.py --run smoke --ids astropy__astropy-12907
结果: reports/swe_official/<run>/ 下 predictions.jsonl / results.jsonl / summary.md, 可断点续跑。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]            # /opt/litecode
CFG = json.loads((ROOT / "config.json").read_text())
SERVER = f"http://127.0.0.1:{CFG.get('server', {}).get('port', 18789)}"
TOKEN = CFG.get("server", {}).get("token", "")
HOST_WS = ROOT / "workspace" / "swe"                   # 宿主侧
CONT_WS = "/tmp/litecode_workspace/swe"                # litecode 容器侧 (同一目录)
SESSIONS = ROOT / "sessions"
# 评测断网: 联网工具和子代理不给模型 (2026-10-13 flashnext-v1 有 21/50 题直接拉了官方 PR diff)
DISABLED_TOOLS = ["web", "spawn_agent"]
# 兜底审计: shell 里 curl 这些站点也算看答案, 该题记 net_leak, 不计入成绩
LEAK_RE = re.compile(r"https?://[^\s\"']*(github\.com|githubusercontent\.com|gitlab\.com|djangoproject\.com"
                     r"|bugs\.python\.org|sourceforge\.net|bitbucket\.org)[^\s\"']*")
INFRA_MARKS = ("连不上模型服务", "[harness] agent 调用异常")
DATASET = "princeton-nlp/SWE-bench_Verified"
PY = sys.executable


def sh(cmd: list, timeout: int = 600, check: bool = False, cwd=None) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    out = (p.stdout or "") + (p.stderr or "")
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(cmd[:4])} → rc={p.returncode}: {out[-500:]}")
    return p.returncode, out


def image_of(iid: str) -> str:
    return f"swebench/sweb.eval.x86_64.{iid.lower().replace('__', '_1776_')}:latest"


def load_instances(ids: list[str], limit: int, seed: int) -> list[dict]:
    from datasets import load_dataset
    rows = list(load_dataset(DATASET, split="test"))
    if ids:
        return [r for r in rows if r["instance_id"] in set(ids)]
    random.Random(seed).shuffle(rows)
    return rows[:limit]


# ── 题目环境 ────────────────────────────────────────────
def prepare(inst: dict) -> tuple[str, Path]:
    iid, img = inst["instance_id"], image_of(inst["instance_id"])
    sh(["docker", "pull", "-q", img], timeout=1800, check=True)
    work = HOST_WS / iid
    cleanup_dir(work, img)
    # 上一题 agent 在 swe/ 根下写的草稿 (test_fix.py 之类) 清掉, 别串到这题
    sh(["docker", "run", "--rm", "-v", f"{HOST_WS}:/p", img, "bash", "-c",
        "find /p -maxdepth 1 -type f ! -name swe_dexec.py -delete"], timeout=300)
    work.mkdir(parents=True, exist_ok=True)
    sh(["docker", "run", "--rm", "-v", f"{work}:/out", img, "bash", "-c", "cp -a /testbed/. /out/"],
       timeout=900, check=True)
    name = f"swe-gen-{iid.lower().replace('__', '-')}"[:60]
    sh(["docker", "rm", "-f", name])
    sh(["docker", "run", "-d", "--name", name, "-v", f"{work}:/testbed", img, "sleep", "infinity"],
       check=True)
    sh(["docker", "exec", name, "git", "config", "--global", "--add", "safe.directory", "/testbed"])
    return name, work


def cleanup_dir(work: Path, img: str) -> None:
    if work.exists():   # 文件是 root 的, 借题目镜像删
        sh(["docker", "run", "--rm", "-v", f"{work.parent}:/p", img, "rm", "-rf", f"/p/{work.name}"],
           timeout=600)


def extract_patch(name: str, inst: dict) -> str:
    test_files = {l[6:].strip() for l in inst["test_patch"].splitlines() if l.startswith("+++ b/")}
    sh(["docker", "exec", name, "git", "-C", "/testbed", "add", "-A"])
    _, files = sh(["docker", "exec", name, "git", "-C", "/testbed", "diff", "--cached", "--name-status"])
    keep = []
    for line in files.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        st, path = parts[0], parts[-1]
        if path in test_files:
            continue
        if st.startswith("A") and "/" not in path:      # 根目录新建的 reproduce.py 之类草稿
            continue
        if st.startswith("A") and path.endswith((".orig", ".rej", ".patch", ".bak")):   # patch 工具残留
            continue
        keep.append(path)
    if not keep:
        return ""
    _, diff = sh(["docker", "exec", name, "git", "-C", "/testbed", "diff", "--cached", "--", *keep])
    return diff if diff.endswith("\n") else diff + "\n"


# ── 让 agent 解题 ───────────────────────────────────────
def prompt_for(inst: dict, name: str) -> str:
    repo = f"{CONT_WS}/{inst['instance_id']}"
    return (
        f"请修复开源项目 {inst['repo']} 的一个 issue。\n\n"
        f"代码已经在 {repo} (git 仓库, 已切到对应版本, 不要重新 clone)。用你的文件工具直接读改这里的文件。\n"
        f"项目的运行环境在另一个容器里, 那边的 /testbed 就是同一份代码。要跑 python / pytest / 复现脚本, 用:\n"
        f"  python3 {CONT_WS}/swe_dexec.py {name} \"<命令>\"\n"
        f"例: python3 {CONT_WS}/swe_dexec.py {name} \"python -m pytest path/to/test_x.py -x -q\"\n"
        f"(命令已在 /testbed 下、依赖已装好; 不要在本机直接跑项目的 python, 本机缺依赖)\n\n"
        f"步骤: 先读相关代码并写个小脚本复现问题 → 改源码修好 → 再跑复现脚本和相关已有测试确认。\n"
        f"只改源码, 不要改已有测试文件。不要上网查这个 issue 的官方修复 / PR / commit, 只靠本地代码和测试解决。\n"
        f"全部做完再回复, 最后一句话说明改了什么。\n\n"
        f"## Issue\n{inst['problem_statement']}"
    )


def call_agent(inst: dict, name: str, sid: str, timeout: int) -> tuple[str, float]:
    t0 = time.time()
    text = ""
    try:
        r = requests.post(f"{SERVER}/v1/chat/completions", stream=True, timeout=timeout,
                          headers={"Authorization": f"Bearer {TOKEN}"},
                          json={"model": "x", "stream": True, "user": sid, "disabled_tools": DISABLED_TOOLS,
                                "messages": [{"role": "user", "content": prompt_for(inst, name)}]})
        for line in r.iter_lines(decode_unicode=True):
            if time.time() - t0 > timeout:
                text += "\n[harness] 超时"
                break
            if not line or not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            try:
                d = (json.loads(line[5:]).get("choices") or [{}])[0].get("delta") or {}
                text += d.get("content") or ""
            except Exception:
                pass
    except Exception as e:
        text += f"\n[harness] agent 调用异常: {e}"
    return text, time.time() - t0


# ── 官方打分 ────────────────────────────────────────────
def grade(inst: dict, patch: str, run: str, out: Path, model: str) -> dict:
    iid = inst["instance_id"]
    pred = out / "preds" / f"{iid}.jsonl"
    pred.parent.mkdir(parents=True, exist_ok=True)
    pred.write_text(json.dumps({"instance_id": iid, "model_name_or_path": model, "model_patch": patch}) + "\n")
    rc, log = sh([PY, "-m", "swebench.harness.run_evaluation", "--dataset_name", DATASET,
                  "--predictions_path", str(pred), "--instance_ids", iid, "--run_id", run,
                  "--max_workers", "1", "--namespace", "swebench", "--report_dir", "reports"],
                 timeout=3600, cwd=out)
    rep = out / "logs" / "run_evaluation" / run / model / iid / "report.json"
    if rep.exists():
        r = json.loads(rep.read_text()).get(iid, {})
        ts = r.get("tests_status", {})
        return {"resolved": bool(r.get("resolved")),
                "f2p": f"{len(ts.get('FAIL_TO_PASS', {}).get('success', []))}/"
                       f"{len(ts.get('FAIL_TO_PASS', {}).get('success', [])) + len(ts.get('FAIL_TO_PASS', {}).get('failure', []))}",
                "p2p_fail": len(ts.get("PASS_TO_PASS", {}).get("failure", [])),
                "patch_applied": r.get("patch_successfully_applied", False)}
    return {"resolved": False, "error": "no report", "log_tail": log[-400:]}


def net_leaks(sid: str) -> list[str]:
    """会话里所有工具调用参数中出现的代码托管 / issue 站点 URL。"""
    f = SESSIONS / f"{sid}.json"
    if not f.exists():
        return []
    urls = set()
    for m in json.loads(f.read_text()).get("msgs", []):
        for tc in (m.get("tool_calls") or []) if isinstance(m, dict) else []:
            urls.update(u.group(0)[:160] for u in LEAK_RE.finditer(str((tc.get("function") or {}).get("arguments"))))
    return sorted(urls)


def summarize(out: Path, model: str) -> str:
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines() if l.strip()]
    ok = sum(1 for r in rows if r.get("resolved") and not r.get("net_leak"))
    leaked = sum(1 for r in rows if r.get("net_leak"))
    lines = [f"# SWE-bench Verified · LiteCode + {model}", "",
             f"- 题数 {len(rows)} · **resolved {ok}/{len(rows)} = {ok / max(1, len(rows)):.0%}**"
             + (f" (另有 {leaked} 题访问了代码托管站点, 不计分)" if leaked else ""),
             f"- 打分: 官方 swebench harness (题目官方镜像)", "",
             "| 题目 | 结果 | F2P | P2P 挂 | 补丁行 | 用时 |", "|---|---|---|---|---|---|"]
    for r in rows:
        res = "🚫 联网" if r.get("net_leak") else "✅" if r.get("resolved") else (
            "⚠ 空补丁" if not r.get("patch_lines") else "❌")
        lines.append(f"| `{r['instance_id']}` | {res} | {r.get('f2p', '-')} | {r.get('p2p_fail', '-')} | "
                     f"{r.get('patch_lines', 0)} | {r.get('agent_s', 0):.0f}s |")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    return lines[2]


class InfraError(RuntimeError):
    pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--ids", nargs="*", default=[])
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--timeout", type=int, default=2400, help="单题 agent 时限 (秒)")
    ap.add_argument("--keep-images", action="store_true")
    a = ap.parse_args()

    out = ROOT / "reports" / "swe_official" / a.run
    out.mkdir(parents=True, exist_ok=True)
    HOST_WS.mkdir(parents=True, exist_ok=True)
    shutil.copy(Path(__file__).with_name("swe_dexec.py"), HOST_WS / "swe_dexec.py")
    model = requests.get(f"{SERVER}/v1/model/list", headers={"Authorization": f"Bearer {TOKEN}"},
                         timeout=10).json().get("active", "litecode")
    model_tag = "litecode-" + str(model).replace("/", "_").replace(":", "_")
    done = set()
    if (out / "results.jsonl").exists():
        done = {json.loads(l)["instance_id"] for l in (out / "results.jsonl").read_text().splitlines() if l.strip()}

    insts = load_instances(a.ids, a.limit, a.seed)
    print(f"[swe] {len(insts)} 题, 已完成 {len(done)}, 模型 {model}", flush=True)
    for i, inst in enumerate(insts, 1):
        iid = inst["instance_id"]
        if iid in done:
            continue
        print(f"[swe] ({i}/{len(insts)}) {iid}", flush=True)
        rec = {"instance_id": iid}
        stop = False
        name, work = "", HOST_WS / iid
        try:
            name, work = prepare(inst)
            sid = f"swe-{a.run}-{iid}"[:64]
            text, rec["agent_s"] = call_agent(inst, name, sid, a.timeout)
            (out / "transcripts").mkdir(exist_ok=True)
            (out / "transcripts" / f"{iid}.md").write_text(text)
            if any(k in text for k in INFRA_MARKS):
                raise InfraError(text.strip()[-200:])
            rec["net_leak"] = net_leaks(sid)
            patch = extract_patch(name, inst)
            rec["patch_lines"] = sum(1 for l in patch.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---"))
            (out / "patches").mkdir(exist_ok=True)
            (out / "patches" / f"{iid}.diff").write_text(patch)
            sh(["docker", "rm", "-f", name])
            rec.update(grade(inst, patch, a.run, out, model_tag))
        except InfraError as e:
            # 模型服务挂了不是模型的错: 不记结果, 停下来, 恢复后断点续跑会重做这题
            print(f"[swe] 模型服务异常, 停止: {e}", flush=True)
            stop = True
        except Exception as e:
            rec.update({"resolved": False, "error": str(e)[:500]})
        finally:
            if name:
                sh(["docker", "rm", "-f", name])
            cleanup_dir(work, image_of(iid))
            if not a.keep_images:
                sh(["docker", "rmi", "-f", image_of(iid)])
        if stop:
            break
        with open(out / "results.jsonl", "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[swe]   → {'RESOLVED' if rec.get('resolved') else 'fail'} {rec.get('f2p', '')} "
              f"patch={rec.get('patch_lines', 0)}L {rec.get('agent_s', 0):.0f}s {rec.get('error', '')}", flush=True)
        print("[swe] " + summarize(out, model), flush=True)


if __name__ == "__main__":
    main()
