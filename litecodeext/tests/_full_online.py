#!/usr/bin/env python3
"""test_full 在线场景执行器: TestResult / run_scenario / generate_report。"""

import json
import os
import time
import traceback
from typing import Optional

from _full_common import (  # noqa: F401
    requests, TRACE_LOG,
    G, R, Y, C, B, D, NC,
)
from _full_scenarios import SCENARIOS


class TestResult:
    def __init__(self, scenario: dict):
        self.scenario = scenario
        self.success = False
        self.error: Optional[str] = None
        self.response_text = ""
        self.tool_calls: list = []
        self.elapsed = 0.0
        self.checks: dict = {}  # {check_name: pass/fail}
        self.raw_chunks = 0

    def to_dict(self) -> dict:
        return {
            "id": self.scenario["id"],
            "name": self.scenario["name"],
            "tags": self.scenario["tags"],
            "success": self.success,
            "error": self.error,
            "elapsed_s": round(self.elapsed, 1),
            "response_chars": len(self.response_text),
            "tool_calls": len(self.tool_calls),
            "tool_names": list(set(t.split(":")[0] for t in self.tool_calls)),
            "checks": self.checks,
            "chunks_received": self.raw_chunks,
        }


def run_scenario(
    scenario: dict,
    server_url: str,
    token: str,
    model: str,
    session_id: str,
    dry_run: bool = False,
) -> TestResult:
    """执行单个测试场景, 返回 TestResult。全程 try-except 保护。"""
    result = TestResult(scenario)
    sid = scenario["id"]
    name = scenario["name"]
    # [v1.4] 全局超时倍率 + 下限保护
    # - 老 agent 一轮可能要 60s+ (thinking + 多个 tool + memory 压缩),
    #   原 30/60s timeout 全部 timeout, 误报 "无法连接 server".
    # - 用 TEST_TIMEOUT_MULT 倍率统一放大 (默认 5x), 单个测试也可在 scenario 里覆盖.
    # - 最小 180s 兜底, 即便倍率 1x 也不会少于 180s.
    _mult = float(os.environ.get("TEST_TIMEOUT_MULT", "5"))
    _raw_timeout = scenario.get("timeout", 120)
    timeout = max(180, int(_raw_timeout * _mult))

    print(f"\n{C}{'='*60}{NC}")
    print(f"{B}[{sid}/{len(SCENARIOS)}] {name}{NC}  {D}{scenario['description']}{NC}")
    print(f"{D}tags: {', '.join(scenario['tags'])}{NC}")

    if dry_run:
        print(f"{Y}  [DRY-RUN] 跳过{NC}")
        result.success = True
        result.checks = {"dry_run": True}
        return result

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "stream": True,
        "messages": [{"role": "user", "content": scenario["message"]}],
        "user": session_id,
    }

    t0 = time.time()
    full_text = ""
    tool_calls_seen = []

    try:
        with requests.post(
            f"{server_url}/v1/chat/completions",
            headers=headers, json=payload,
            # [v1.4] connect 30s (vLLM cold-start 可能 10s+), read=timeout (含倍率)
            stream=True, timeout=(30, timeout),
        ) as resp:
            if resp.status_code != 200:
                result.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                print(f"  {R}[FAIL] {result.error}{NC}")
                return result

            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if line.startswith(":"):
                    continue  # heartbeat
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break

                result.raw_chunks += 1
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})

                    # 文本内容
                    ct = delta.get("content", "")
                    if ct:
                        full_text += ct

                    # 工具调用状态 — 排除 server 自带的 "preparing" 占位
                    # (litecode_server.py:935 yield 一条 detail="推理中…" key="preparing"
                    #  作为预热提示, 不是真工具调用, 不该计入 no_tool_calls 断言)
                    for key in ("task_exec", "data_collect", "task_analysis"):
                        if key in delta:
                            ev = delta[key]
                            if ev.get("status") == "executing" and ev.get("key") != "preparing":
                                detail = ev.get("detail", "")
                                tool_calls_seen.append(detail)
                                tool_short = detail.split(":")[0] if ":" in detail else detail
                                print(f"  {D}[tool] {tool_short}{NC}", flush=True)

                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    except requests.exceptions.ChunkedEncodingError as e:
        # [v1.4] 服务器在 streaming 中途断开 — 不是没连上, 而是连上后断了
        result.error = f"STREAM_DROPPED: {e}"
        print(f"  {R}[FAIL] 服务端流被切断 (server 中途关连接, 非连接失败){NC}")
        return result
    except requests.exceptions.ConnectionError as e:
        # [v1.4] 真的连不上 (DNS/refused/server 没起)
        result.error = f"CONNECTION_ERROR: {e}"
        print(f"  {R}[FAIL] 无法连接 server: {str(e)[:120]}{NC}")
        return result
    except requests.exceptions.ReadTimeout:
        result.error = f"READ_TIMEOUT after {timeout}s"
        print(f"  {Y}[TIMEOUT] read timeout {timeout}s — agent 可能仍在跑, 增大 TEST_TIMEOUT_MULT 重试{NC}")
    except requests.exceptions.Timeout:
        result.error = f"TIMEOUT after {timeout}s"
        print(f"  {Y}[TIMEOUT] {timeout}s{NC}")
    except Exception as e:
        result.error = f"EXCEPTION: {type(e).__name__}: {e}"
        print(f"  {R}[ERROR] {e}{NC}")
        traceback.print_exc()

    finally:
        result.elapsed = time.time() - t0
        result.response_text = full_text
        result.tool_calls = tool_calls_seen

    # ── 验证 expect 条件 ──
    expect = scenario.get("expect", {})
    checks = {}

    if expect.get("has_text"):
        checks["has_text"] = len(full_text.strip()) > 0

    if "min_chars" in expect:
        checks["min_chars"] = len(full_text) >= expect["min_chars"]

    if expect.get("no_tool_calls"):
        checks["no_tool_calls"] = len(tool_calls_seen) == 0

    if "tool_names_include" in expect:
        seen_names = set()
        for tc in tool_calls_seen:
            name_part = tc.split(":")[0].strip()
            seen_names.add(name_part)
        required = set(expect["tool_names_include"])
        checks["tool_names"] = required.issubset(seen_names)
        if not checks["tool_names"]:
            checks["tool_names_detail"] = f"need={required}, got={seen_names}"

    if "text_contains_any" in expect:
        text_lower = full_text.lower()
        found = any(kw.lower() in text_lower for kw in expect["text_contains_any"])
        checks["text_contains"] = found

    if "text_not_contains" in expect:
        text_lower = full_text.lower()
        bad = [kw for kw in expect["text_not_contains"] if kw.lower() in text_lower]
        checks["text_not_contains"] = len(bad) == 0
        if bad:
            checks["text_not_contains_detail"] = f"不应包含: {bad}"

    if "min_tool_calls" in expect:
        checks["min_tool_calls"] = len(tool_calls_seen) >= expect["min_tool_calls"]

    if "max_tool_calls" in expect:
        checks["max_tool_calls"] = len(tool_calls_seen) <= expect["max_tool_calls"]

    # [v1.0.6] file_exists: 验证场景声称要创建的文件确实被创建 (单文件或列表)
    if "file_exists" in expect:
        import os as _os_chk
        paths = expect["file_exists"]
        if isinstance(paths, str):
            paths = [paths]
        missing = [p for p in paths if not _os_chk.path.exists(p)]
        checks["file_exists"] = len(missing) == 0
        if missing:
            checks["file_exists_detail"] = f"缺失文件: {missing}"

    # [v1.1] file_runs_ok: 真的跑一下 python/shell 脚本, 看退出码
    # 解决了 scenario 7 误报问题 — 测试以前只查字面关键词, 但 buggy.py 修完还是坏的
    if "file_runs_ok" in expect:
        import subprocess as _subp, os as _os_run
        specs = expect["file_runs_ok"]
        if isinstance(specs, str):
            specs = [specs]
        failed = []
        for spec in specs:
            path = spec if isinstance(spec, str) else spec["path"]
            if not _os_run.path.exists(path):
                failed.append(f"{path}: 文件不存在")
                continue
            cmd = (["python3", path] if path.endswith(".py") else
                   ["bash", path] if path.endswith(".sh") else
                   ["go", "run", path] if path.endswith(".go") else ["python3", path])
            try:
                p = _subp.run(cmd, capture_output=True, timeout=30, text=True,
                              cwd=_os_run.path.dirname(path) or ".")
                if p.returncode != 0:
                    failed.append(f"{path}: exit={p.returncode} stderr={p.stderr[:120]!r}")
            except _subp.TimeoutExpired:
                failed.append(f"{path}: timeout 30s")
            except Exception as _e:
                failed.append(f"{path}: {type(_e).__name__}: {_e}")
        checks["file_runs_ok"] = len(failed) == 0
        if failed:
            checks["file_runs_ok_detail"] = "; ".join(failed)

    # [v1.0.6] file_contains: 验证文件里含关键字 (格式: {path: [keywords]})
    if "file_contains" in expect:
        _all_ok = True
        _bad = []
        for path, kws in expect["file_contains"].items():
            try:
                content = open(path, errors="replace").read().lower()
                kws_l = [k.lower() for k in (kws if isinstance(kws, list) else [kws])]
                missing_kws = [k for k in kws_l if k not in content]
                if missing_kws:
                    _all_ok = False
                    _bad.append(f"{path}: 缺 {missing_kws}")
            except Exception as _e:
                _all_ok = False
                _bad.append(f"{path}: 读失败 ({_e})")
        checks["file_contains"] = _all_ok
        if _bad:
            checks["file_contains_detail"] = "; ".join(_bad)

    result.checks = checks
    result.success = all(
        v is True for k, v in checks.items()
        if not k.endswith("_detail")
    )

    # 打印结果
    status = f"{G}PASS{NC}" if result.success else f"{R}FAIL{NC}"
    print(f"\n  [{status}] {result.elapsed:.1f}s  text={len(full_text)}c  tools={len(tool_calls_seen)}  chunks={result.raw_chunks}")
    for k, v in checks.items():
        if k.endswith("_detail"):
            continue
        mark = f"{G}v{NC}" if v else f"{R}x{NC}"
        print(f"    {mark} {k}")
    if result.error:
        print(f"    {R}error: {result.error[:100]}{NC}")

    return result


# ══════════════════════════════════════════════════════════════
# REPORT GENERATOR
# ══════════════════════════════════════════════════════════════

def generate_report(results: list[TestResult], elapsed_total: float) -> str:
    """生成 Markdown 测试报告。"""
    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed

    lines = [
        f"# LiteCode v1.0 Test Report",
        f"",
        f"- Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Total elapsed: {elapsed_total:.1f}s",
        f"- Scenarios: {len(results)}",
        f"- Passed: {passed}",
        f"- Failed: {failed}",
        f"- Pass rate: {passed/max(len(results),1)*100:.0f}%",
        f"",
        f"## Summary Table",
        f"",
        f"| # | Scenario | Status | Time | Text | Tools | Error |",
        f"|---|----------|--------|------|------|-------|-------|",
    ]

    for r in results:
        s = "PASS" if r.success else "FAIL"
        err = (r.error or "")[:40]
        lines.append(
            f"| {r.scenario['id']} | {r.scenario['name']} | {s} | "
            f"{r.elapsed:.1f}s | {len(r.response_text)}c | "
            f"{len(r.tool_calls)} | {err} |"
        )

    lines.append("")
    lines.append("## Detail")
    lines.append("")

    for r in results:
        lines.append(f"### [{r.scenario['id']}] {r.scenario['name']}")
        lines.append(f"")
        lines.append(f"**Status**: {'PASS' if r.success else 'FAIL'}")
        lines.append(f"**Elapsed**: {r.elapsed:.1f}s")
        lines.append(f"**Response**: {len(r.response_text)} chars")
        lines.append(f"**Tool calls**: {len(r.tool_calls)}")
        if r.tool_calls:
            tool_names = {}
            for tc in r.tool_calls:
                n = tc.split(":")[0].strip()
                tool_names[n] = tool_names.get(n, 0) + 1
            lines.append(f"**Tools used**: {tool_names}")
        lines.append(f"**Checks**: {r.checks}")
        if r.error:
            lines.append(f"**Error**: {r.error[:200]}")
        # 输出前 500 字符
        if r.response_text:
            preview = r.response_text[:500].replace("\n", "\n> ")
            lines.append(f"\n<details><summary>Response preview</summary>\n\n> {preview}\n\n</details>")
        lines.append("")

    # ── 日志完整性检查 ──
    lines.append("## Trace Log Integrity")
    lines.append("")
    if TRACE_LOG.exists():
        log_text = TRACE_LOG.read_text(errors="replace")
        trace_starts = log_text.count("[TRACE_START]")
        trace_ends = log_text.count("[TRACE_END]")
        iter_begins = log_text.count("[ITER_BEGIN]")
        tool_execs = log_text.count("[TOOL_EXEC]")
        # v1.0 新增追踪标签
        dag_starts = log_text.count("[DAG_STEP_START]")
        dag_ends = log_text.count("[DAG_STEP_END]")
        critic_events = log_text.count("[CRITIC_")
        bb_writes = log_text.count("[BLACKBOARD_WRITE]")
        has_trace_id = '"trace_id"' in log_text
        has_span_id = '"span_id"' in log_text
        lines.append(f"- TRACE_START count: {trace_starts}")
        lines.append(f"- TRACE_END count: {trace_ends}")
        lines.append(f"- ITER_BEGIN count: {iter_begins}")
        lines.append(f"- TOOL_EXEC count: {tool_execs}")
        lines.append(f"- Paired (start==end): {'YES' if trace_starts == trace_ends else 'NO - MISMATCH'}")
        lines.append(f"- Log file size: {TRACE_LOG.stat().st_size} bytes")
        lines.append(f"- **v1.0 trace_id present**: {'YES' if has_trace_id else 'NO'}")
        lines.append(f"- **v1.0 span_id present**: {'YES' if has_span_id else 'NO'}")
        lines.append(f"- **v1.0 DAG steps**: {dag_starts} start / {dag_ends} end")
        lines.append(f"- **v1.0 Critic events**: {critic_events}")
        lines.append(f"- **v1.0 Blackboard writes**: {bb_writes}")
    else:
        lines.append("- Trace log NOT FOUND (itrace disabled or server not reached)")

    lines.append("")
    lines.append("[ANALYSIS_READY]")
    lines.append("")

    return "\n".join(lines)
