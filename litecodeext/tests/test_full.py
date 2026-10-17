#!/usr/bin/env python3
"""
test_full.py — LiteCode v1.0 全链路测试 (25 精选场景)
==========================================================
分组:
  A. 基线 (2)     — QA / 模型自知
  B. 搜索 (4)     — web_search 直连 / 免费 API / 错误语义 / 浏览器
  C. 代码 (5)     — 写+跑+修 / 多步链路 / SQLite / Go / 批量文件
  D. 子代理 (4)   — 单/Pipeline/DAG/并行
  E. 长文 (3)     — 大纲 / 切片 / 多文件项目
  F. 记忆 (2)     — save_memory / update_profile
  G. 技能/基建 (3)— skill / CJK PDF / 定时器
  H. 错误 (1)     — 连续错误自愈
  I. 日期 (1)     — 2026 年感知

验证机制 (v1.0.6):
  - has_text / min_chars / no_tool_calls — 基础输出
  - tool_names_include / min_tool_calls / max_tool_calls — 工具调用
  - text_contains_any / text_not_contains — 关键词匹配
  - file_exists (新) — 验证输出文件真的创建了
  - file_contains (新) — 验证文件内容含关键字

运行:
  python3 test_full.py                    # 跑全部 25 个
  python3 test_full.py --scenario 3       # 单场景
  python3 test_full.py --tag search       # 按 tag 过滤 (search/code/spawn_agent/...)
  python3 test_full.py --offline          # 只跑离线单元测试
  python3 test_full.py --dry-run          # 只打印不发请求

产出:
  - stdout: 实时进度
  - logs/test_full_report.md: 结构化报告
  - logs/iteration_full.log: server 侧 itrace 自动写入
"""

import argparse
import json
import os
import sys
import time
import traceback

# [split] 原 3589 行单文件已拆分: 共享配置 → _full_common.py, 场景 → _full_scenarios.py,
#         在线执行器/报告 → _full_online.py, 离线单测 → test_full_part_*.py。本文件保留入口/CLI 不变。
from _full_common import (  # noqa: F401
    requests, BASE, DEFAULT_URL, DEFAULT_TOKEN, DEFAULT_MODEL, REPORT_FILE, TRACE_LOG,
    G, R, Y, C, B, D, NC,
)
from _full_scenarios import SCENARIOS
from _full_online import TestResult, run_scenario, generate_report
from test_full_part_v10_core import _offline_v10_core
from test_full_part_v10_fixes import _offline_v10_fixes
from test_full_part_l1_l2 import _offline_l1_smoke, _offline_l2_mid
from test_full_part_l3_l4 import _offline_l3_high, _offline_l4_complex


# ══════════════════════════════════════════════════════════════
# v1.0 OFFLINE UNIT TESTS (无需 server, 直接跑)
# ══════════════════════════════════════════════════════════════

def run_offline_tests() -> list:
    """v1.0 核心模块离线单元测试, 不需要 server 运行。"""
    print(f"\n{B}v1.0 Offline Unit Tests{NC}")
    print(f"{D}(不需要 server, 直接测试 Python 模块){NC}\n")

    results = []
    results += _offline_v10_core()
    results += _offline_v10_fixes()
    results += _offline_l1_smoke()
    results += _offline_l2_mid()
    results += _offline_l3_high()
    results += _offline_l4_complex()

    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed
    print(f"\n{B}Offline Results: {G}{passed}{NC}/{len(results)} passed" +
          (f"  {R}{failed} failed{NC}" if failed else ""))

    return results


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="LiteCode v1.0 Full Test Suite")
    ap.add_argument("--server", default=DEFAULT_URL, help="Server URL")
    ap.add_argument("--token", default=DEFAULT_TOKEN, help="Auth token")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Model ID")
    ap.add_argument("--scenario", type=int, help="Run only scenario N")
    ap.add_argument("--session", default=None, help="Session ID (default: auto)")
    ap.add_argument("--dry-run", action="store_true", help="Print scenarios without running")
    ap.add_argument("--skip", type=str, help="Skip scenario IDs, comma-separated (e.g. '5,6')")
    ap.add_argument("--offline", action="store_true", help="Run only offline unit tests (no server needed)")
    ap.add_argument("--tag", type=str, help="[v1.0.6] Only run scenarios with this tag (e.g. --tag search)")
    ap.add_argument("--tier", type=str, choices=["smoke","standard","heavy","all"],
                    default="standard",
                    help="[v1.1] smoke=5 快速冒烟 / standard=25 常规 (不含 heavy) / heavy=8 深度 / all=全部")
    ap.add_argument("--test-switch", action="store_true", help="Run model switch 401 test (API-level)")
    args = ap.parse_args()

    # ── Offline 模式: 只跑单元测试 ──
    if args.offline:
        # [v1.9 P37-a] 设置 OFFLINE 标记 — 让 memory/network 路径快速 fail
        # 避免 test_memory_fallback 等测试用 192.0.2.1 触发 OS TCP SYN 127s 超时
        os.environ["LITECODE_TEST_OFFLINE"] = "1"
        os.environ["OPENCLAW_TEST_OFFLINE"] = "1"  # 兼容旧名
        offline_results = run_offline_tests()
        passed = sum(1 for r in offline_results if r["pass"])
        failed = len(offline_results) - passed

        # 写报告
        report_lines = [
            f"# LiteCode v1.0 Offline Test Report",
            f"",
            f"- Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Tests: {len(offline_results)}",
            f"- Passed: {passed}",
            f"- Failed: {failed}",
            f"",
            f"| # | Test | Status | Time | Error |",
            f"|---|------|--------|------|-------|",
        ]
        for i, r in enumerate(offline_results, 1):
            s = "PASS" if r["pass"] else "FAIL"
            report_lines.append(f"| {i} | {r['name']} | {s} | {r['elapsed']}s | {r['error'][:60]} |")

        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text("\n".join(report_lines))
        print(f"\n  Report: {REPORT_FILE}")

        sys.exit(1 if failed else 0)

    server_url = args.server
    session_id = args.session or f"test-{int(time.time())}"
    skip_ids = set(int(x) for x in args.skip.split(",")) if args.skip else set()

    # ── 连通性检查 ──
    if not args.dry_run:
        print(f"{C}Checking server: {server_url}{NC}")
        try:
            r = requests.get(
                f"{server_url}/health",
                headers={"Authorization": f"Bearer {args.token}"},
                timeout=5,
            )
            if r.status_code != 200:
                print(f"{R}Server returned {r.status_code}{NC}")
                sys.exit(1)
            info = r.json()
            print(f"{G}Server online: {info.get('model', '?')}{NC}")
        except Exception as e:
            print(f"{R}Cannot connect to server: {e}{NC}")
            print(f"{Y}Start server first: python3 litecode_server.py{NC}")
            sys.exit(1)

    # ── [v1.0] 模型切换 401 测试 (API级别, 独立于 SSE 场景) ──
    if args.test_switch and not args.dry_run:
        print(f"\n{B}[v1.0] Model Switch 401 Test{NC}")
        _h = {"Authorization": f"Bearer {args.token}", "Content-Type": "application/json"}
        try:
            # 获取模型列表
            r = requests.get(f"{server_url}/v1/model/list", headers=_h, timeout=5)
            models = r.json().get("models", [])
            original = r.json().get("active", "")
            if len(models) < 2:
                print(f"  {Y}⚠ 只有 1 个模型，无法测试切换。请在 config.json 添加第二个模型{NC}")
            else:
                target = [m for m in models if m["id"] != original][0]
                print(f"  切换: {original} → {target['id']}")

                # 切换
                r2 = requests.post(f"{server_url}/v1/model/switch", headers=_h,
                                   json={"model_id": target["id"]}, timeout=10)
                assert r2.status_code == 200 and r2.json().get("ok"), f"切换失败: {r2.text[:200]}"

                # 立即发消息 — 旧版这里 401
                print(f"  切换后立即发消息...")
                r3 = requests.post(f"{server_url}/v1/chat/completions", headers=_h,
                                   json={"model": target["id"], "stream": False,
                                         "messages": [{"role": "user", "content": "回复OK两个字"}],
                                         "user": "test-switch"},
                                   timeout=30)
                if r3.status_code == 401:
                    print(f"  {R}❌ 401 BUG 未修复！切换后请求被拒绝{NC}")
                    print(f"  {R}   响应: {r3.text[:200]}{NC}")
                elif r3.status_code == 200:
                    print(f"  {G}✅ 切换后对话正常 (HTTP 200){NC}")
                else:
                    print(f"  {Y}⚠ HTTP {r3.status_code} (非401，可能是模型不可达){NC}")

                # 切回
                requests.post(f"{server_url}/v1/model/switch", headers=_h,
                              json={"model_id": original}, timeout=10)
                print(f"  已切回: {original}")
        except Exception as e:
            print(f"  {R}❌ 测试异常: {e}{NC}")
            traceback.print_exc()

    # ── 清理旧 trace log ──
    if TRACE_LOG.exists() and not args.dry_run:
        # 不删除, 追加模式. 但写一个分隔符
        with TRACE_LOG.open("a") as f:
            f.write(f"\n{'#'*80}\n# TEST_FULL RUN: {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'#'*80}\n")

    # ── 选择场景 ── [v1.1] --tier: smoke / standard / heavy / all
    if args.scenario:
        scenarios = [s for s in SCENARIOS if s["id"] == args.scenario]
        if not scenarios:
            print(f"{R}Scenario {args.scenario} not found (valid: 1-{len(SCENARIOS)}){NC}")
            sys.exit(1)
    elif getattr(args, "tag", None):
        wanted = args.tag
        scenarios = [s for s in SCENARIOS
                     if wanted in s.get("tags", []) and s["id"] not in skip_ids]
        if not scenarios:
            print(f"{R}tag '{wanted}' matched no scenarios{NC}")
            sys.exit(1)
    else:
        tier = getattr(args, "tier", "standard")
        if tier == "smoke":
            # 5 个快速冒烟: 基线 + 错误语义 + 写跑修 + SQLite + 日期感知
            scenarios = [s for s in SCENARIOS
                         if "smoke" in s.get("tags", []) and s["id"] not in skip_ids]
        elif tier == "heavy":
            # 仅 heavy 场景
            scenarios = [s for s in SCENARIOS
                         if "heavy" in s.get("tags", []) and s["id"] not in skip_ids]
        elif tier == "all":
            scenarios = [s for s in SCENARIOS if s["id"] not in skip_ids]
        else:  # standard (default): 排除 heavy 档
            scenarios = [s for s in SCENARIOS
                         if "heavy" not in s.get("tags", []) and s["id"] not in skip_ids]

    # 先跑离线测试
    if not args.dry_run:
        offline_results = run_offline_tests()
        offline_failed = sum(1 for r in offline_results if not r["pass"])
        if offline_failed:
            print(f"\n{R}⚠️ {offline_failed} offline tests failed. Fix before running online tests.{NC}")
            # 不退出, 继续跑在线测试

    print(f"\n{B}LiteCode v1.0 Full Test Suite{NC}")
    print(f"{D}Scenarios: {len(scenarios)}  Session: {session_id}  Server: {server_url}{NC}")
    print(f"{D}Report: {REPORT_FILE}{NC}")
    print(f"{D}Trace:  {TRACE_LOG}{NC}")

    # ── 执行 ──
    t_total = time.time()
    results: list[TestResult] = []

    for scenario in scenarios:
        try:
            r = run_scenario(
                scenario=scenario,
                server_url=server_url,
                token=args.token,
                model=args.model,
                session_id=session_id,
                dry_run=args.dry_run,
            )
            results.append(r)
        except Exception as e:
            # 即使单个场景完全崩溃, 也不影响其他场景
            print(f"\n{R}[CRASH] Scenario {scenario['id']}: {e}{NC}")
            traceback.print_exc()
            crash_result = TestResult(scenario)
            crash_result.error = f"CRASH: {type(e).__name__}: {e}"
            results.append(crash_result)

    elapsed_total = time.time() - t_total

    # ── 生成报告 ──
    report = generate_report(results, elapsed_total)
    REPORT_FILE.write_text(report)

    # ── 打印汇总 ──
    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed

    print(f"\n{C}{'='*60}{NC}")
    print(f"{B}RESULTS{NC}")
    print(f"  Total:  {len(results)}")
    print(f"  Passed: {G}{passed}{NC}")
    print(f"  Failed: {R}{failed}{NC}" if failed else f"  Failed: {G}0{NC}")
    print(f"  Time:   {elapsed_total:.1f}s")
    print(f"  Report: {REPORT_FILE}")

    # ── Trace log 完整性 ──
    if TRACE_LOG.exists():
        log_text = TRACE_LOG.read_text(errors="replace")
        starts = log_text.count("[TRACE_START]")
        ends = log_text.count("[TRACE_END]")
        print(f"  Trace:  {TRACE_LOG} ({TRACE_LOG.stat().st_size} bytes)")
        print(f"          TRACE_START={starts}  TRACE_END={ends}  {'PAIRED' if starts==ends else 'MISMATCH'}")
    else:
        print(f"  Trace:  {Y}not found (itrace disabled?){NC}")

    if failed:
        print(f"\n{R}Some tests failed. Check report for details.{NC}")
        sys.exit(1)
    else:
        print(f"\n{G}All tests passed.{NC}")
        sys.exit(0)


if __name__ == "__main__":
    main()
