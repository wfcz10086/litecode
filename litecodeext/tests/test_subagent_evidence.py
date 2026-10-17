"""子代理交付证据 (lib/agent/subagent_evidence.py) 的回归用例。

经真实 spawn_agent handler / spawn_dispatch → _run_subagent 跑, 只把子代理 LLM 流 (_vllm_stream)
和 execute_tool 打桩: 假 LLM 按任务文本给脚本化的工具调用/文本, 假 execute_tool 真写盘真读盘,
execute_shell 按命令返回预设输出 (模拟执行器: 失败加 ERROR[EXIT=N] 前缀)。
"""
import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib.agent import subagent as sa  # noqa: E402
from lib.agent import subagent_evidence as ev  # noqa: E402
from lib.agent import subagent_worktree as wt  # noqa: E402
from lib.config import CFG  # noqa: E402

PYTEST_OK = "tests/test_a.py ...\n3 passed in 0.05s"
PYTEST_FAIL = "ERROR[TEST_FAIL]: tests/test_a.py .F.\nFAILED tests/test_a.py::t2\n1 failed, 2 passed in 0.07s"


def _git(repo, *args):
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=str(repo),
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(wt, "WORKSPACE_OVERRIDE", ws)
    monkeypatch.setitem(CFG, "agent", {**(CFG.get("agent") or {}), "subagent_evidence": True})
    sa._SUBAGENT_CACHE.clear()
    scripts, its, seen, calls = {}, {}, {}, []
    shell_out = {"pytest -q": PYTEST_OK, "pytest -q bad": PYTEST_FAIL}

    async def fake_stream(messages, tools):
        task = messages[1]["content"]
        seen.setdefault(task, []).append([dict(m) for m in messages])
        it = its.setdefault(task, iter(scripts.get(task, [])))
        await asyncio.sleep(0.005)
        step = next(it, None)
        if step is None or step[0] == "text":
            yield {"choices": [{"delta": {"content": step[1] if step else f"done: {task}"}}]}
            return
        _, name, args = step
        yield {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": f"c{len(calls)}",
            "function": {"name": name, "arguments": json.dumps(args)}}]}}]}

    async def fake_exec(name, args, sid):
        calls.append((name, dict(args)))
        if name in ("write_file", "read_file"):
            p = Path(args["filepath"])
            p = p if p.is_absolute() else ws / p
            if name == "write_file":
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(args["content"])
                return f"wrote {p}", None
            return (p.read_text() if p.exists() else f"ERROR: not found {p}"), None
        if name == "execute_shell":
            cmd = args["command"].split("&& ")[-1].strip()
            if cmd in shell_out:
                return shell_out[cmd], None
            r = subprocess.run(args["command"], shell=True, capture_output=True, text=True, cwd=str(ws))
            out = (r.stdout + r.stderr).strip()
            return (out or "(exit 0)") if r.returncode == 0 else f"ERROR[EXIT={r.returncode}]: {out}", None
        if name == "search_code":
            return "a.py:1: x = 1", None
        return f"fake {name}", None

    monkeypatch.setattr(sa, "_vllm_stream", fake_stream)
    monkeypatch.setattr(sa, "execute_tool", fake_exec)
    return SimpleNamespace(ws=ws, scripts=scripts, seen=seen, calls=calls)


def _spawn(args):
    from core.tools.handlers.agent import h_spawn_agent
    out, _ = asyncio.run(h_spawn_agent("", args))
    return out


def _nudges(env, task):
    """该子代理最后一次 LLM 调用时历史里的 [SYSTEM-EVIDENCE] 注入条数。"""
    hist = env.seen[task][-1]
    return sum(1 for m in hist if m["role"] == "user" and str(m.get("content", "")).startswith(ev.NUDGE_PREFIX))


def _t(name):
    return f"{name} {uuid.uuid4().hex[:6]}"


# ── 解析: pytest / unittest / npm(jest/mocha) / go / cargo 计数 + 退出码 + 命令分类 ──
def test_parse_counts_exit_and_classify():
    p = ev.parse_test_counts("collected 6\n===== 3 passed, 1 failed, 2 skipped in 0.12s =====")
    assert (p["framework"], p["passed"], p["failed"], p["skipped"]) == ("pytest", 3, 1, 2)
    assert ev.parse_test_counts("12 passed, 1 warning in 3.10s")["passed"] == 12
    u = ev.parse_test_counts("Ran 5 tests in 0.002s\n\nFAILED (failures=1, errors=1)")
    assert (u["passed"], u["failed"], u["errors"]) == (3, 1, 1)
    j = ev.parse_test_counts("> jest\nTests:       1 failed, 4 passed, 5 total\nTime: 1s")
    assert (j["framework"], j["passed"], j["failed"]) == ("jest", 4, 1)
    m = ev.parse_test_counts("> mocha\n\n  7 passing (20ms)\n  2 failing")
    assert (m["passed"], m["failed"]) == (7, 2)
    g = ev.parse_test_counts("ok  \texample.com/a\t0.01s\nFAIL\texample.com/b\t0.02s\nFAIL")
    assert (g["framework"], g["passed"], g["failed"]) == ("go", 1, 1)
    gv = ev.parse_test_counts("=== RUN TestA\n--- PASS: TestA (0.00s)\n--- PASS: TestB (0.00s)\nPASS\nok  \tx\t0.1s")
    assert (gv["passed"], gv["failed"]) == (2, 0)
    c = ev.parse_test_counts("test result: ok. 5 passed; 0 failed; 1 ignored;\ntest result: ok. 2 passed; 0 failed; 0 ignored;")
    assert (c["passed"], c["failed"]) == (7, 0)
    assert ev.parse_test_counts("random output") is None
    assert [ev.infer_exit(x) for x in ("ERROR[EXIT=2]: boom", "ERROR[TEST_FAIL]: x", "(exit 0)",
                                       "hello", "ERROR: tool failed", "x\n[AUTO-BG] bg")] == [2, 1, 0, 0, -1, None]
    cls = ev.classify_command
    assert cls("pytest -q tests") == cls("python3 -m pytest -x") == cls("npm test") == "test"
    assert cls("go test ./...") == cls("cargo test") == cls("make test") == cls("npx jest") == "test"
    assert cls("cd /x && python -m unittest discover | tail -3") == "test"
    assert cls("npm run build") == cls("go build ./...") == cls("make") == "build"
    assert cls("python main.py") == cls("node app.js") == cls("bash run.sh") == "run"
    assert cls("python -m py_compile a.py") == "syntax"
    assert cls("ls -la") is None and cls("cat a.py") is None


# ── 写类只写不测 → 注入一次补证提示 → 补跑测试 → verified=true ──
def test_write_only_nudged_then_tests_verified(env):
    t = _t("写功能")
    env.scripts[t] = [("tool", "write_file", {"filepath": "a.py", "content": "x = 1\n"}),
                      ("text", "完成, 已验证, 测试全过"),
                      ("tool", "execute_shell", {"command": "pytest -q"}),
                      ("text", "补证后: 3 passed")]
    out = _spawn({"task": t, "agent_type": "coder"})
    assert out.startswith("[已验证]\n"), out
    assert "补证后: 3 passed" in out and "完成, 已验证" not in out     # 交付的是补证后的总结
    assert "verified=true" in out and "pytest: 3 passed / 0 failed" in out
    assert "补证提示: 已注入 1 次" in out
    assert _nudges(env, t) == 1
    nudge = [m for m in env.seen[t][-1] if str(m.get("content", "")).startswith(ev.NUDGE_PREFIX)][0]["content"]
    assert "没有运行任何测试/构建/运行命令" in nudge and "a.py" in nudge
    assert "[SYSTEM-EVIDENCE]" not in out.split("【证据】")[0]      # 注入文案不进交付正文


# ── 拒不补证: 只给一次机会, 然后照常结束, verified=false, 不死循环 ──
def test_refuses_evidence_unverified_no_loop(env):
    t = _t("拒绝补证")
    env.scripts[t] = [("tool", "write_file", {"filepath": "b.py", "content": "y = 1\n"}),
                      ("text", "做完了"), ("text", "我确定做完了"), ("text", "还是做完了")]
    out = _spawn({"task": t, "agent_type": "coder"})
    assert out.startswith("[未验证: 改动 1 个文件但没有运行任何测试/构建/运行命令, 改动文件未回读: b.py]"), out
    assert "我确定做完了" in out and "verified=false" in out
    assert len(env.seen[t]) == 3 and _nudges(env, t) == 1           # 写 → 文本(注入) → 文本(结束)


# ── 测试失败 (管道吞掉退出码也能从计数识别) → 补证提示也不救 → 未验证 ──
def test_failing_tests_unverified(env):
    t = _t("测试失败")
    env.scripts[t] = [("tool", "write_file", {"filepath": "c.py", "content": "z = 1\n"}),
                      ("tool", "execute_shell", {"command": "pytest -q bad"}),
                      ("text", "全部通过"), ("text", "真的通过")]
    out = _spawn({"task": t, "agent_type": "coder"})
    assert out.startswith("[未验证: 改动 1 个文件但测试未通过: pytest -q bad (exit 1, failed=1)"), out
    assert "pytest: 2 passed / 1 failed" in out


# ── 写后回读 → verified=true, 不注入 ──
def test_write_then_readback_verified(env):
    t = _t("回读")
    env.scripts[t] = [("tool", "write_file", {"filepath": "d.py", "content": "w = 1\n"}),
                      ("tool", "read_file", {"filepath": "d.py"}),
                      ("text", "写好并回读")]
    out = _spawn({"task": t, "agent_type": "coder"})
    assert out.startswith("[已验证]"), out
    assert "均已回读" in out and "d.py(已回读)" in out
    assert _nudges(env, t) == 0 and len(env.seen[t]) == 3
    # shell 读回 (cat) 同样算回读; 写后再改会清掉回读标记
    col = ev.EvidenceCollector("coder", write_role=True)
    col.observe("write_file", {"filepath": "/p/e.py"}, "wrote")
    col.observe("execute_shell", {"command": "cat /p/e.py"}, "w = 1")
    assert col.verdict()[0]
    col.observe("patch_file", {"filepath": "/p/e.py"}, "patched")
    assert not col.verdict()[0]


# ── 只读子代理: 有搜索取证 → 已验证; 纯自述 → 未验证且不注入 (只读角色无补证) ──
def test_readonly_search_vs_self_report(env):
    t1, t2 = _t("查找"), _t("自述")
    env.scripts[t1] = [("tool", "search_code", {"pattern": "x"}), ("text", "x 在 a.py:1")]
    env.scripts[t2] = [("text", "我记得 x 在 a.py 第 1 行")]
    out1 = _spawn({"task": t1, "agent_type": "explorer"})
    out2 = _spawn({"task": t2, "agent_type": "explorer"})
    assert out1.startswith("[已验证]") and "有工具取证: search_code×1" in out1, out1
    assert out2.startswith("[未验证: 没有任何工具取证, 结论仅为模型自述]"), out2
    assert len(env.seen[t2]) == 1 and _nudges(env, t2) == 0


# ── 主代理收到的结果: 单个 (spawn_dispatch 主循环路径) 带标记; 并行汇总列出每个子代理判定 ──
def test_main_agent_markers_single_and_parallel(env):
    from lib.agent import spawn_dispatch as sd
    t = _t("主循环单个")
    env.scripts[t] = [("tool", "write_file", {"filepath": "f.py", "content": "f = 1\n"}),
                      ("tool", "execute_shell", {"command": "pytest -q"}), ("text", "ok")]

    async def _go(args):
        out = {"effective_timeout": 0}
        async for _ in sd._dispatch_spawn_agent(args, SimpleNamespace(session_id="ev-sid", tracer=None),
                                                None, asyncio.Queue(), out):
            pass
        return out["result"]
    res = asyncio.run(_go({"task": t, "agent_type": "coder"}))
    assert res.startswith("[已验证]\n") and "【证据】" in res, res

    pa, pb = _t("并行好"), _t("并行坏")
    env.scripts[pa] = [("tool", "search_code", {"pattern": "y"}), ("text", "找到 y")]
    env.scripts[pb] = [("text", "凭印象: 合计 4163")]
    res = asyncio.run(_go({"parallel_tasks": [{"task": pa, "agent_type": "explorer"},
                                              {"task": pb, "agent_type": "analyst"}]}))
    assert res.startswith("[未验证: 1/2 个子代理未验证"), res
    summ = res.split("--- 子代理证据判定 ---", 1)[1]
    assert f"[已验证] explorer「{pa}」" in summ
    assert f"[未验证: 没有任何工具取证, 结论仅为模型自述] analyst「{pb}」" in summ


# ── 开关关闭: 与原行为零差异 (无标记无证据, 不注入) ──
def test_switch_off_zero_change(env, monkeypatch):
    monkeypatch.setitem(CFG, "agent", {**(CFG.get("agent") or {}), "subagent_evidence": False})
    t = _t("关闭")
    env.scripts[t] = [("tool", "write_file", {"filepath": "g.py", "content": "g = 1\n"}), ("text", "完成")]
    out = _spawn({"task": t, "agent_type": "coder"})
    assert out == "完成", out
    assert len(env.seen[t]) == 2 and _nudges(env, t) == 0
    assert ev.start_collector("coder") is None
    plan = ev.plan_evidence()
    fn = object()
    assert plan.wrap(fn) is fn and plan.decorate("x") == "x"


# ── 补证不超 max_iter: 最后一轮准备结束不再注入 ──
def test_nudge_respects_max_iter(env, monkeypatch):
    monkeypatch.setitem(sa._SUBAGENT_CONFIGS["coder"], "max_iter", 2)
    t = _t("迭代上限")
    env.scripts[t] = [("tool", "write_file", {"filepath": "h.py", "content": "h = 1\n"}), ("text", "完成")]
    out = _spawn({"task": t, "agent_type": "coder"})
    assert len(env.seen[t]) == 2 and _nudges(env, t) == 0
    assert out.startswith("[未验证:") and "完成" in out


# ── 与工作树隔离共存: 结果里同时有工作树信息与证据 (提交 hash / 合并状态) ──
def test_coexists_with_worktree(env):
    repo = env.ws / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    ta, tb = _t("wt 测"), _t("wt 不测")
    env.scripts[ta] = [("tool", "write_file", {"filepath": "a.py", "content": "x = 2\n"}),
                       ("tool", "execute_shell", {"command": "pytest -q"}), ("text", "a 改好")]
    env.scripts[tb] = [("tool", "write_file", {"filepath": "b.py", "content": "y = 2\n"}),
                       ("text", "b 改好"), ("text", "b 就是好了")]
    out = _spawn({"parallel_tasks": [{"task": ta, "agent_type": "coder"},
                                     {"task": tb, "agent_type": "coder"}], "workdir": str(repo)})
    assert out.startswith("[未验证: 1/2 个子代理未验证"), out
    assert "--- 子代理工作树隔离 ---" in out and out.count("] 已合并") >= 2
    summ = out.split("--- 子代理证据判定 ---", 1)[1]
    assert f"[已验证] coder「{ta}」" in summ and f"coder「{tb}」" in summ
    assert summ.count("工作树 coder-") == 2 and "分支 lc-agent/coder-" in summ and "合并提交" in summ
    head = _git(repo, "log", "--format=%H", "-1", "--", "a.py")[:12]
    assert head in summ                                    # 证据里的提交 hash 与仓库真实一致
    assert (repo / "a.py").read_text() == "x = 2\n" and (repo / "b.py").read_text() == "y = 2\n"
    assert _nudges(env, tb) == 1 and _nudges(env, ta) == 0


# ── 证据小节长度上限 + windows_system 回读摘要 ──
def test_section_limit_and_windows_readback():
    col = ev.EvidenceCollector("analyst", write_role=False)
    col.observe("windows_system", {"action": "inspect_file", "path": "C:/t.xlsx"}, "Sheet1 B10=1964 (SUM(B2:B9))")
    for i in range(40):
        col.observe("execute_shell", {"command": f"python run_{i}.py " + "x" * 100}, "y" * 300)
    ok, why = col.verdict()
    assert ok and "windows_system.inspect_file×1" in why
    sec = col.render(ok, why)
    assert len(sec) <= ev.SECTION_MAX and sec.startswith("【证据】")
    assert "Windows 回读: inspect_file C:/t.xlsx: Sheet1 B10=1964" in sec
