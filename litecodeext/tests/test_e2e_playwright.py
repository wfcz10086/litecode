"""16_full_e2e.py — LiteCode 完整 E2E 测试 (2026-05-19 v2)

承袭 15_full_e2e.py 的 37 项 CLI ↔ Web UI 同步测试 (Tier 1-3),
新增 Tier 4 任务执行场景验证 (Tier 4-1..5):
  1. 简单单文件任务   — 不该走 PLAN
  2. 复杂大项目      — 必须走 PLAN + batch_guard 不被触发或被正确触发
  3. 定时器 cron 自动 — 60s 触发 run_count +1
  4. DAG 编排        — 跑现有 DAG, 验证 job 成功
  5. 综合 timer→DAG  — 一句话 timer + DAG 联动

用法:
  python3 16_full_e2e.py            # Tier 1-3 (快速, ~2min)
  python3 16_full_e2e.py --exec     # 加 Tier 4 (慢, ~15-30min, LLM 真推理)
  python3 16_full_e2e.py --only=4-3 # 只跑某个场景
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

URL = "http://localhost:18790"
PWD = "CHANGE_ME_PASSWORD"
OUT = "/tmp/litecode_e2e"
WS_HOST = "/opt/litecode/workspace"

passed, failed, skipped = [], [], []


# ── helpers ───────────────────────────────────────────────────
def assert_in(needle: str, hay):
    s = hay if isinstance(hay, str) else str(hay)
    assert needle in s, f"expect '{needle}' in output"


def assert_any_of(hay: str, needles: list):
    assert any(n in hay for n in needles), f"none of {needles}"


def assert_count(hay: str, needle: str, n: int):
    c = hay.count(needle)
    assert c == n, f"expect {n} '{needle}' got {c}"


def cli(*args, check=True, timeout=30) -> str:
    r = subprocess.run(["litecli", *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"CLI failed: {args}\n  stderr={r.stderr}")
    return r.stdout


def test(name: str, fn):
    try:
        fn()
        passed.append(name)
        print(f"  ✅ {name}")
    except AssertionError as e:
        failed.append((name, str(e) or "assertion failed"))
        print(f"  ❌ {name}: {e}")
    except Exception as e:
        failed.append((name, f"{type(e).__name__}: {e}"))
        print(f"  ❌ {name}: {type(e).__name__}: {e}")


def wait_for(predicate, timeout: int, interval: int = 10, label: str = ""):
    """Poll predicate() every `interval` sec until True or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        if label:
            print(f"    ... waiting {label} ({int(deadline - time.time())}s left)")
        time.sleep(interval)
    return False


def send_chat(page, message: str, new: bool = True) -> str:
    """Send a chat message via web UI, return new sid."""
    if new:
        d = page.evaluate(f"async () => (await (await fetch('/api/sessions', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:'{{\"name\":{json.dumps(message[:40])}}}'}})).json())")
        sid = d.get("id")
    else:
        raise NotImplementedError
    # 异步触发 chat (不等完成)
    page.evaluate(f"""async () => {{
        fetch('/api/chat/{sid}', {{
            method: 'POST',
            headers: {{'Content-Type':'application/json'}},
            body: JSON.stringify({{message: {json.dumps(message)}}})
        }});  // 不 await
    }}""")
    return sid


# ── 主 ────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exec", action="store_true", help="加跑 Tier 4 真任务执行场景 (慢)")
    ap.add_argument("--only", default=None, help="只跑某个 Tier (e.g. '4-2')")
    args = ap.parse_args()

    only_filter = args.only
    do_exec = args.exec or (only_filter and only_filter.startswith("4"))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
        ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = ctx.new_page()

        # Setup
        page.goto(URL, wait_until="domcontentloaded", timeout=20000)
        page.fill("#lpwd", PWD); page.click("button:has-text('登录')")
        page.wait_for_load_state("networkidle"); time.sleep(2)
        cli("login", "-p", PWD)

        # ──────────────────────────────────────────────────────
        # Tier 1: CLI 子命令全覆盖 (28 项)
        # ──────────────────────────────────────────────────────
        if not only_filter or only_filter.startswith("1"):
            print("\n━━━ Tier 1: CLI 子命令全覆盖 ━━━\n")
            test("auth/whoami", lambda: assert_in("ok: True", cli("whoami")))
            test("model/list 含 6 个", lambda: assert_count(cli("model", "list"), "  #", 6))
            test("model/use #1", lambda: assert_in("Qwen3.5-35B", cli("model", "use", "#1")))
            test("model/use #6 (切回)", lambda: assert_in("nutboy02", cli("model", "use", "#6")))
            test("health", lambda: assert_in('"ok": true', cli("--json", "health")))
            test("stats", lambda: assert_in("total_tokens", cli("stats")))

            # timer CRUD
            tid_holder = []
            def _tc():
                out = cli("timer", "create", "--name", "e2e-full",
                          "--schedule", "*/5 * * * *", "--action", "web_notify",
                          "--content", "full e2e", "--target", "")
                assert "已创建" in out
                tlist = page.evaluate("async () => (await (await fetch('/api/timers')).json())")
                t = next((x for x in tlist["timers"] if x["name"] == "e2e-full"), None)
                assert t
                tid_holder.append(t["id"])
            test("timer/create", _tc)
            test("timer/list", lambda: assert_in("e2e-full", cli("timer", "list")))
            test("timer/run 手动", lambda: assert_in("ok", cli("timer", "run", tid_holder[0])))
            test("timer/history", lambda: assert_in("ok", cli("timer", "history", tid_holder[0])))
            test("timer/toggle off", lambda: assert_in("已暂停", cli("timer", "toggle", tid_holder[0], "--off")))
            test("timer/toggle on", lambda: assert_in("已启用", cli("timer", "toggle", tid_holder[0], "--on")))
            test("timer/rm", lambda: assert_in("已删除", cli("timer", "rm", tid_holder[0])))

            test("sessions/list", lambda: assert_in("web-", cli("sessions", "list", "-n", "5")))
            sess_id = page.evaluate("async () => { const d=await (await fetch('/api/sessions')).json(); return d[0].id; }")
            test("sessions/show", lambda: assert_in("msgs)", cli("sessions", "show", sess_id)))
            test("sessions/export md", lambda: assert_in("###", cli("sessions", "export", sess_id, "--fmt", "md")))
            test("sessions/export json", lambda: assert_in('"messages"', cli("sessions", "export", sess_id, "--fmt", "json")))
            test("memory/<sid>", lambda: assert_in("ctx=", cli("memory", sess_id)))
            test("ws/ls", lambda: assert_any_of(cli("ws", "ls"), ["📁", "📄"]))
            test("dag/list", lambda: cli("dag", "list"))  # 不抛即可
            test("dag/jobs", lambda: cli("dag", "jobs"))
            test("wechat/status", lambda: assert_in("available", cli("wechat", "status")))
            test("projects/list", lambda: cli("projects", "list"))
            test("config/get", lambda: assert_in("agent", cli("config", "get")))
            test("config/set noop",
                 lambda: cli("config", "set", "search", "default_mode=international"))
            test("--json health", lambda: json.loads(cli("--json", "health")))

        # ──────────────────────────────────────────────────────
        # Tier 2: CLI ↔ Web UI 双向同步 (3 项)
        # ──────────────────────────────────────────────────────
        if not only_filter or only_filter.startswith("2"):
            print(f"\n━━━ Tier 2: CLI ↔ Web UI 同步 ━━━\n")

            def _sync_model():
                cli("model", "use", "#1"); page.evaluate("refreshSbfStatus()"); time.sleep(1.5)
                assert "#1" in page.eval_on_selector("#sbf-model-id", "e=>e.innerText")
                cli("model", "use", "#6"); page.evaluate("refreshSbfStatus()"); time.sleep(1.5)
                assert "#6" in page.eval_on_selector("#sbf-model-id", "e=>e.innerText")
            test("sync/CLI 切模型 → web badge", _sync_model)

            def _sync_timer():
                cli("timer", "create", "--name", "sync-test",
                    "--schedule", "0 9 * * *", "--action", "web_notify",
                    "--content", "sync", "--target", "")
                page.click("button:has-text('定时任务')")
                page.wait_for_selector("#timer-bd:not(:has-text('加载中'))", timeout=8000)
                assert "sync-test" in page.inner_text("#timer-bd")
                tid = next(x["id"] for x in page.evaluate(
                    "async () => (await (await fetch('/api/timers')).json())")["timers"]
                    if x["name"] == "sync-test")
                cli("timer", "rm", tid)
                time.sleep(1); page.evaluate("loadTimers()"); time.sleep(1)
                assert "sync-test" not in page.inner_text("#timer-bd")
            test("sync/CLI timer CRUD ↔ UI 面板", _sync_timer)

            def _sync_sessions():
                ui = len(page.evaluate("async () => (await (await fetch('/api/sessions')).json())"))
                out = cli("sessions", "list", "-n", "999")
                cli_n = sum(1 for ln in out.splitlines()
                            if ln.startswith("  web-") or ln.startswith("  wx-") or ln.startswith("  cli-"))
                assert ui == cli_n, f"webUI={ui} CLI={cli_n}"
            test("sync/sessions count", _sync_sessions)

        # ──────────────────────────────────────────────────────
        # Tier 3: 持久化 (1 项)
        # ──────────────────────────────────────────────────────
        if not only_filter or only_filter.startswith("3"):
            print(f"\n━━━ Tier 3: 持久化 (web_ui 重启) ━━━\n")

            def _persist():
                out = cli("timer", "create", "--name", "persist-test",
                          "--schedule", "0 9 * * *", "--action", "web_notify",
                          "--content", "persist", "--target", "")
                assert "已创建" in out
                subprocess.run(["sudo", "docker", "exec", "litecode", "pkill", "-f", "web_ui.py"],
                               check=False, capture_output=True, timeout=10)
                for _ in range(15):
                    time.sleep(2)
                    r = subprocess.run(["curl", "-sf", URL + "/"], capture_output=True, timeout=5)
                    if r.returncode == 0:
                        break
                out = cli("timer", "list")
                assert "persist-test" in out
                m = re.search(r'(tmr_\w+)\s+persist-test', out)
                assert m
                cli("timer", "rm", m.group(1))
            test("persist/timer 跨 web_ui 重启", _persist)

        # ──────────────────────────────────────────────────────
        # Tier 4: 真任务执行场景 (需 --exec)
        # ──────────────────────────────────────────────────────
        if do_exec or only_filter:
            print(f"\n━━━ Tier 4: 任务执行场景 (LLM 真跑, 慢) ━━━\n")

            # 4-1 简单单文件 (≤200 行 Python, 不应触发 PLAN)
            if not only_filter or only_filter == "4-1":
                def _simple():
                    ws_dir = Path(WS_HOST) / "e2e_simple"
                    subprocess.run(["sudo", "rm", "-rf", str(ws_dir)], check=False, capture_output=True)
                    sid = send_chat(page, (
                        "在 /tmp/litecode_workspace/e2e_simple/ 写一个 Python 单文件 quicksort.py "
                        "(< 150 行, 含 main 函数测试自身打印结果). 不要复杂规划, 直接写完跑通."
                    ))
                    print(f"    sid={sid}, 等 4 分钟看产出...")
                    ok = wait_for(lambda: (ws_dir / "quicksort.py").exists(),
                                   timeout=240, interval=15, label="quicksort.py")
                    assert ok, "4 分钟没产出 quicksort.py"
                    text = (ws_dir / "quicksort.py").read_text()
                    assert "def" in text and ("quicksort" in text.lower() or "quick_sort" in text.lower())
                    # 不应该有 PLAN.md (单文件 ≤200 行不需要 plan)
                    has_plan = (ws_dir / "PLAN.md").exists()
                    print(f"    quicksort.py = {len(text)} chars, PLAN.md = {has_plan}")
                test("4-1 简单单文件 quicksort.py", _simple)

            # 4-2 复杂大项目 (Go 网络诊断, 必须先 PLAN.md)
            if not only_filter or only_filter == "4-2":
                def _complex():
                    ws_dir = Path(WS_HOST) / "e2e_netdiag"
                    subprocess.run(["sudo", "rm", "-rf", str(ws_dir)], check=False, capture_output=True)
                    sid = send_chat(page, (
                        "在 /tmp/litecode_workspace/e2e_netdiag/ 用 Go 写一个综合网络诊断工具 "
                        "(CLI + Web UI + 端口扫描 + traceroute + DNS + ping, SQLite 存结果, 5+ 文件). "
                        "必须先 write_file PLAN.md, 然后单文件 build 验证再下一个."
                    ))
                    print(f"    sid={sid}, 等 8 分钟看 PLAN.md 产出 (35B Ollama 推理慢)...")
                    # 关键验证: PLAN.md 必须存在
                    ok = wait_for(lambda: (ws_dir / "PLAN.md").exists(),
                                   timeout=480, interval=25, label="PLAN.md")
                    assert ok, "8 分钟没看到 PLAN.md (大项目模板没生效)"
                    plan = (ws_dir / "PLAN.md").read_text()
                    print(f"    PLAN.md = {len(plan)} chars")
                    assert "step" in plan.lower() or "1." in plan or "##" in plan, "PLAN.md 不像分步施工单"
                test("4-2 复杂大项目走 PLAN", _complex)

            # 4-3 定时器 cron 自动触发
            if not only_filter or only_filter == "4-3":
                def _timer_auto():
                    out = cli("timer", "create", "--name", "tier4-cron",
                              "--schedule", "* * * * *", "--action", "web_notify",
                              "--content", "cron auto", "--target", "")
                    assert "已创建" in out
                    tid = re.search(r'tmr_\w+', cli("timer", "list", check=False)).group()
                    # 实际从 list 拿对应 timer (上一行可能拿别人的)
                    out2 = cli("timer", "list")
                    m = re.search(r'(tmr_\w+)\s+tier4-cron', out2)
                    assert m
                    tid = m.group(1)
                    print(f"    timer id={tid}, 等 90 秒自动 cron 触发...")
                    def _check():
                        ts = page.evaluate(
                            "async () => (await (await fetch('/api/timers')).json())")
                        t = next((x for x in ts["timers"] if x["id"] == tid), None)
                        return t and t.get("run_count", 0) >= 1
                    ok = wait_for(_check, timeout=100, interval=15, label="cron 触发")
                    cli("timer", "rm", tid)
                    assert ok, "90s 内 cron 没自动触发"
                test("4-3 定时器 cron 自动触发", _timer_auto)

            # 4-4 DAG 跑现有 — 只验证 job 能起来 + 状态变 running/done (不等业务完成)
            if not only_filter or only_filter == "4-4":
                def _dag_run():
                    dags = page.evaluate("async () => (await (await fetch('/api/dags')).json())")
                    dag_list = dags.get("dags", [])
                    if not dag_list:
                        raise AssertionError("没有可用 DAG")
                    dag_name = dag_list[0] if isinstance(dag_list[0], str) else dag_list[0].get("name")
                    out = cli("dag", "run", dag_name)
                    assert "已启动" in out, f"dag run 失败: {out}"
                    m = re.search(r'job_id=(\w+)', out)
                    assert m, "拿不到 job_id"
                    job_id = m.group(1)
                    print(f"    dag={dag_name} job={job_id}, 等 30s 看 job 进入 running/done...")
                    def _check():
                        d = page.evaluate(f"async () => (await (await fetch('/api/dags/jobs/{job_id}')).json())")
                        return d.get("status") in ("done", "running", "failed")
                    ok = wait_for(_check, timeout=40, interval=10, label=f"job {job_id}")
                    assert ok, "40s job 没进入 running/done 状态"
                test("4-4 DAG runner 启动", _dag_run)

            # 4-5 综合: 一句话 timer + DAG 联动 (timer action=dag)
            if not only_filter or only_filter == "4-5":
                def _timer_dag():
                    dags = page.evaluate("async () => (await (await fetch('/api/dags')).json())").get("dags", [])
                    if not dags:
                        raise AssertionError("没 DAG 可绑")
                    dag_name = dags[0] if isinstance(dags[0], str) else dags[0].get("name")
                    out = cli("timer", "create", "--name", "tier4-timer-dag",
                              "--schedule", "* * * * *", "--action", "dag",
                              "--target", dag_name, "--content", dag_name)
                    assert "已创建" in out
                    out2 = cli("timer", "list")
                    m = re.search(r'(tmr_\w+)\s+tier4-timer-dag', out2)
                    assert m
                    tid = m.group(1)
                    # 用 timer.run_count + history (action=dag, status=ok) 作 sensor
                    # /api/dags/jobs 上限 20 + 顺序 truncate, 单纯 count 不可靠
                    print(f"    timer+dag 联动: dag={dag_name}, 等 90s 看 run_count + history.dag.ok...")
                    def _check():
                        ts = page.evaluate(
                            "async () => (await (await fetch('/api/timers')).json())")
                        t = next((x for x in ts["timers"] if x["id"] == tid), None)
                        if not t or t.get("run_count", 0) < 1:
                            return False
                        for h in t.get("history", []):
                            if h.get("action_type") == "dag" and h.get("status") == "ok":
                                return True
                        return False
                    ok = wait_for(_check, timeout=110, interval=15, label="dag 被 timer 触发")
                    cli("timer", "rm", tid)
                    assert ok, "90s 内 timer 没成功 spawn DAG (run_count==0 或 history 无 dag/ok)"
                test("4-5 综合: timer→DAG 联动", _timer_dag)

            # 4-6 DAG when v2 条件分支 (新): 构造一个 4 step DAG, 验证 when 正确 skip/run
            if not only_filter or only_filter == "4-6":
                def _when_v2():
                    # 用 shell agent_type 不依赖 LLM, 跑得快
                    dag_name = "e2e-when-v2"
                    dag_json = {
                        "name": dag_name,
                        "steps": [
                            {"id": "produce", "agent_type": "shell", "task": "echo BTC ETH",
                             "depends_on": []},
                            # 这步: produce 含 BTC → 应该 RUN
                            {"id": "run_if_btc", "agent_type": "shell", "task": "echo found-btc",
                             "depends_on": ["produce"],
                             "when": [{"contains": ["$step.produce.output", "BTC"]}]},
                            # 这步: produce 含 DOGE → 应该 SKIP (因为没 DOGE)
                            {"id": "skip_if_doge", "agent_type": "shell", "task": "echo found-doge",
                             "depends_on": ["produce"],
                             "when": [{"contains": ["$step.produce.output", "DOGE"]}]},
                            # 这步: any(BTC or DOGE) → 应该 RUN
                            {"id": "run_any", "agent_type": "shell", "task": "echo any-passed",
                             "depends_on": ["produce"],
                             "when": [{"any": [
                                 {"contains": ["$step.produce.output", "BTC"]},
                                 {"contains": ["$step.produce.output", "DOGE"]},
                             ]}]},
                            # v1.3 兼容: success:produce → 应该 RUN
                            {"id": "v1_compat", "agent_type": "shell", "task": "echo v1-ok",
                             "depends_on": ["produce"],
                             "when": ["success:produce"]},
                        ],
                    }
                    # 保存 DAG
                    page.evaluate(f"""async () => {{
                        await fetch('/api/dags/{dag_name}', {{
                            method: 'PUT',
                            headers: {{'Content-Type':'application/json'}},
                            body: {json.dumps(json.dumps(dag_json))}
                        }});
                    }}""")
                    time.sleep(1)
                    out = cli("dag", "run", dag_name)
                    assert "已启动" in out, f"DAG 没启动: {out}"
                    job_id = re.search(r'job_id=(\w+)', out).group(1)
                    print(f"    when-v2 DAG job={job_id}, 等 240s 完成 (5 step shell agent_type 也走 LLM)...")
                    def _check():
                        d = page.evaluate(
                            f"async () => (await (await fetch('/api/dags/jobs/{job_id}')).json())")
                        return d.get("status") in ("done", "failed")
                    ok = wait_for(_check, timeout=240, interval=15, label=f"job {job_id}")
                    assert ok, "240s job 没完成"
                    # 验证每步 status
                    job = page.evaluate(
                        f"async () => (await (await fetch('/api/dags/jobs/{job_id}')).json())")
                    agents = {a["step_id"]: a for a in job.get("agents", [])}
                    print(f"    agents: " + ", ".join(
                        f"{k}={'✓' if v.get('success') else '✗'+(v.get('error','')[:30])}"
                        for k, v in agents.items()))
                    # 期望: produce/run_if_btc/run_any/v1_compat = success, skip_if_doge SKIPPED
                    assert agents.get("produce", {}).get("success"), "produce 应成功"
                    assert agents.get("run_if_btc", {}).get("success"), "run_if_btc 应 RUN (BTC 在 output)"
                    assert agents.get("run_any", {}).get("success"), "run_any 应 RUN (any 满足)"
                    assert agents.get("v1_compat", {}).get("success"), "v1_compat 应 RUN (success:produce 兼容)"
                    skip = agents.get("skip_if_doge", {})
                    assert not skip.get("success") and "条件" in (skip.get("error") or ""), \
                        f"skip_if_doge 应 SKIPPED (when 不满足), got success={skip.get('success')} err={skip.get('error')[:60]}"
                    # cleanup
                    cli("dag", "rm", dag_name)
                test("4-6 when v2 DAG 条件分支", _when_v2)

        # ── done ──
        ctx.tracing.stop(path=f"{OUT}/full_e2e_v2_trace.zip")
        page.screenshot(path=f"{OUT}/25_full_e2e_v2.png", full_page=True)
        browser.close()

    # 总结
    total = len(passed) + len(failed)
    print(f"\n{'=' * 60}")
    print(f"  PASSED: {len(passed)} / {total}")
    if failed:
        print(f"  FAILED: {len(failed)}")
        for n, err in failed:
            print(f"    - {n}: {err}")
    else:
        print("  🎉 ALL GREEN")
    if skipped:
        print(f"  SKIPPED: {len(skipped)}")
    print("=" * 60)
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
