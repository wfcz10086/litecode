#!/usr/bin/env python3
"""
e2e-self-test runner — single entry point for "test each sub-feature" via Actor.

Modes:
  quick   — 5 core features (~90s)
  full    — all 12 chapter sub-features (~30min)
  feature — one named sub-feature

Output:
  /tmp/litecode_workspace/e2e_self_test_<ts>/cases/*.json
  /tmp/litecode_workspace/e2e_self_test_<ts>/shots/*.png
  /tmp/litecode_workspace/e2e_self_test_<ts>/summary.json
"""
import json, os, sys, time, datetime as dt, traceback, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lib.actor import Actor
from lib import cases as C


def grab_docker_logs(tail=60):
    try:
        out = subprocess.check_output(
            ["docker", "logs", "--tail", str(tail), "litecode"],
            stderr=subprocess.STDOUT, timeout=10).decode("utf-8", errors="replace")
        return out[-1500:]
    except Exception as e:
        return f"docker logs err: {e}"


def safely(case_name, case_fn, results, shot_dir):
    t0 = time.time()
    try:
        ok, reason, shots = case_fn()
        t1 = time.time()
        results.append({
            "case": case_name,
            "result": "PASS" if ok else "FAIL",
            "duration_ms": int((t1 - t0) * 1000),
            "reason": str(reason)[:200],
            "shots": shots,
        })
        print(f"  {('✅' if ok else '❌')} {case_name} {int((t1-t0)*1000)}ms — {reason[:80]}",
              flush=True)
    except Exception as e:
        t1 = time.time()
        results.append({
            "case": case_name,
            "result": "FAIL",
            "duration_ms": int((t1 - t0) * 1000),
            "reason": f"EXC: {type(e).__name__}: {str(e)[:120]}",
            "shots": [],
            "traceback": traceback.format_exc()[:500],
            "docker_log_tail": grab_docker_logs(),
        })
        print(f"  ❌ {case_name} EXC: {e}", flush=True)


# ─── feature → case fn map ─────────────────────────────────
def get_feature_cases(actor, shot_dir):
    return {
        # ── A: single-feature ─────────────────────────────────
        "health":             ("A01", lambda: C.case_health(actor, shot_dir)),
        "panel_stats":        ("A_panels", lambda: C.case_panel(actor, shot_dir, "stats")),
        "panel_memory":       ("A_panels", lambda: C.case_panel(actor, shot_dir, "memory")),
        "panel_timer":        ("A_panels", lambda: C.case_panel(actor, shot_dir, "timer")),
        "panel_dag":          ("A_panels", lambda: C.case_panel(actor, shot_dir, "dag")),
        "panel_settings":     ("A_panels", lambda: C.case_panel(actor, shot_dir, "settings")),
        "panel_project":      ("A_panels", lambda: C.case_panel(actor, shot_dir, "project")),
        "panel_wechat":       ("A_panels", lambda: C.case_panel(actor, shot_dir, "wechat")),
        "panel_sfiles":       ("A_panels", lambda: C.case_panel(actor, shot_dir, "sfiles")),
        "model_switch_35b":   ("A19", lambda: C.case_model_switch(actor, shot_dir, "Qwen3.5-35B")),
        "chat_simple":        ("A09", lambda: C.case_chat(actor, shot_dir, "你好,30字内自我介绍",
                                                            expect_keyword=None, label="chat_simple")),
        "chat_qa_date":       ("A09b", lambda: C.case_chat(actor, shot_dir, "今天日期是?",
                                                            expect_keyword="20", label="chat_date")),
        "timer_cron_agent":   ("A22", lambda: C.case_timer_cron_agent(actor, shot_dir)),
        "timer_once_shell":   ("A23", lambda: C.case_timer_once_shell(actor, shot_dir)),
        "upload_image":       ("A12", lambda: C.case_upload(actor, shot_dir)),
        "dag_manual_save":    ("A35", lambda: C.case_dag_manual(actor, shot_dir)),
        "dag_run_button":     ("A37", lambda: C.case_dag_run_button_state(actor, shot_dir)),
        "project_map_status": ("A43", lambda: C.case_project_map(actor, shot_dir)),
        "inject_traversal":   ("A59", lambda: C.case_inject_path_traversal(actor, shot_dir)),
        "inject_no_auth":     ("A62", lambda: C.case_inject_no_auth(actor, shot_dir)),
        # ── new auxiliary single feature ────────────────────
        "stats":              ("A56", lambda: C.case_stats(actor, shot_dir)),
        "workspace_list":     ("A51", lambda: C.case_workspace_list(actor, shot_dir)),
        "wechat_status":      ("A49", lambda: C.case_wechat_status(actor, shot_dir)),
        "config_get":         ("A58", lambda: C.case_config_get_patch(actor, shot_dir)),
        "dags_list":          ("A_dag_list", lambda: C.case_dags_list(actor, shot_dir)),
        "dags_jobs_list":     ("A41", lambda: C.case_dags_jobs_list(actor, shot_dir)),
        "skills_list":        ("B21", lambda: C.case_skills_list(actor, shot_dir)),
        # ── B: per-tool via chat ────────────────────────────
        "tool_get_tree":      ("B16", lambda: C.case_tool_via_chat(actor, shot_dir, "get_tree",
                                  "用 get_tree 列 workspace 目录树前 10 个文件", timeout=90)),
        "tool_search_code":   ("B18", lambda: C.case_tool_via_chat(actor, shot_dir, "search_code",
                                  "用 search_code 搜 'TODO' 列前 5 条", timeout=90)),
        "tool_execute_shell": ("B01", lambda: C.case_tool_via_chat(actor, shot_dir, "execute_shell",
                                  "用 execute_shell 跑 'pip show fastapi' 给我版本号", timeout=90)),
        "tool_save_memory":   ("B24", lambda: C.case_tool_via_chat(actor, shot_dir, "save_memory",
                                  "save_memory 把'我喜欢简洁回答' 存到记忆", timeout=90)),
        "tool_spawn_explorer":("B22a", lambda: C.case_tool_via_chat(actor, shot_dir, "spawn_explorer",
                                  "用 spawn_agent 调 explorer 扫 litecodeext/core 找 sqlite 引用", timeout=240)),
        # ── D: chain integration ────────────────────────────
        "chain_search_save_recall": ("D01", lambda: C.chain_search_save_recall(actor, shot_dir)),
        "chain_write_map_query":    ("D02", lambda: C.chain_write_map_query(actor, shot_dir)),
        "chain_dag_run_view_output":("D07", lambda: C.chain_dag_run_view_output(actor, shot_dir)),
        "chain_chat_create_dag":    ("D08", lambda: C.chain_chat_create_dag(actor, shot_dir)),
        "chain_chat_create_timer":  ("D09", lambda: C.chain_chat_create_timer(actor, shot_dir)),
        "chain_error_search_fix":   ("D10", lambda: C.chain_error_search_fix(actor, shot_dir)),
    }


QUICK_FEATURES = [
    "health", "panel_dag", "panel_settings", "panel_timer",
    "model_switch_35b", "chat_simple", "dag_run_button",
    "stats", "dags_list", "dags_jobs_list",
]

NORMAL_FEATURES = QUICK_FEATURES + [
    "chat_qa_date", "timer_cron_agent", "timer_once_shell",
    "upload_image", "dag_manual_save", "project_map_status",
    "inject_traversal", "inject_no_auth",
    "tool_get_tree", "tool_search_code", "tool_execute_shell",
    "chain_write_map_query",
]


def run(mode="quick", feature=None, base="http://127.0.0.1:18790", pwd="CHANGE_ME_PASSWORD"):
    ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = Path(f"/tmp/litecode_workspace/e2e_self_test_{ts}")
    shot_dir = out_dir / "shots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cases").mkdir(parents=True, exist_ok=True)
    print(f"[self-test] mode={mode} feature={feature} → {out_dir}", flush=True)

    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080},
                                   ignore_https_errors=True)
        page = ctx.new_page()
        page.set_default_timeout(15000)
        actor = Actor(page, base=base)
        actor.login(pwd)
        actor.shot("login.png", shot_dir)

        feats = get_feature_cases(actor, shot_dir)
        if mode == "quick":
            keys = QUICK_FEATURES
        elif mode == "normal":
            keys = NORMAL_FEATURES
        elif mode == "full":
            keys = list(feats.keys())
        elif mode == "feature":
            if feature not in feats:
                print(f"unknown feature: {feature}; available: {list(feats.keys())}",
                      flush=True)
                sys.exit(2)
            keys = [feature]
        else:
            print(f"bad mode: {mode}", flush=True)
            sys.exit(2)

        for k in keys:
            ch, fn = feats[k]
            safely(k, fn, results, shot_dir)

        browser.close()

    summary = {
        "mode": mode,
        "feature": feature,
        "ts": ts,
        "total": len(results),
        "pass": sum(1 for r in results if r["result"] == "PASS"),
        "fail": sum(1 for r in results if r["result"] == "FAIL"),
        "out_dir": str(out_dir),
        "results": results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[self-test] DONE pass={summary['pass']}/{summary['total']} "
          f"fail={summary['fail']}", flush=True)
    print(f"[self-test] summary: {out_dir}/summary.json", flush=True)
    return summary


# Public API for agent invocation
def run_quick(base="http://127.0.0.1:18790", pwd="CHANGE_ME_PASSWORD"):
    return run(mode="quick", base=base, pwd=pwd)


def run_full(base="http://127.0.0.1:18790", pwd="CHANGE_ME_PASSWORD"):
    return run(mode="full", base=base, pwd=pwd)


def run_feature(feature, base="http://127.0.0.1:18790", pwd="CHANGE_ME_PASSWORD"):
    return run(mode="feature", feature=feature, base=base, pwd=pwd)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["quick", "normal", "full", "feature"], default="quick", nargs="?")
    ap.add_argument("--feature", help="single feature name (mode=feature)")
    ap.add_argument("--base", default="http://127.0.0.1:18790")
    ap.add_argument("--pwd", default="CHANGE_ME_PASSWORD")
    args = ap.parse_args()
    sm = run(mode=args.mode, feature=args.feature, base=args.base, pwd=args.pwd)
    sys.exit(0 if sm["fail"] == 0 else 1)
