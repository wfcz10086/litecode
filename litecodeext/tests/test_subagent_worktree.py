"""编码类子代理独立 git 工作树 (lib/agent/subagent_worktree.py) 的回归用例。

真建 git 仓库 (tmp_path), 经真实 spawn_agent handler → MultiAgentOrchestrator → _run_subagent 跑,
只把子代理的 LLM 流 (_vllm_stream) 和 execute_tool 打桩: 假 LLM 按任务文本给脚本化工具调用,
假 execute_tool 真写盘/真跑 shell, 这样路径映射与 git 合并都是真实效果。
"""
import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib.agent import subagent as sa  # noqa: E402
from lib.agent import subagent_worktree as wt  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=str(repo),
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    repo = ws / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    (repo / "a.py").write_text("x = 1\n")
    (repo / "b.py").write_text("y = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    monkeypatch.setattr(wt, "WORKSPACE_OVERRIDE", ws)

    scripts, calls = {}, []

    async def fake_stream(messages, tools):
        task = messages[1]["content"]
        it = scripts.setdefault(task, iter(SCRIPTS.get(task, [])))
        await asyncio.sleep(0.01)   # 让并行子代理交错
        rnd = next(it, None)
        if rnd is None:
            yield {"choices": [{"delta": {"content": f"done: {task}"}}]}
            return
        name, args = rnd
        yield {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": f"c{len(calls)}",
            "function": {"name": name, "arguments": json.dumps(args)}}]}}]}

    async def fake_exec(name, args, sid):
        calls.append((name, dict(args)))
        if name == "write_file":
            p = Path(args["filepath"])
            p = p if p.is_absolute() else ws / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return f"wrote {p}", None
        if name == "execute_shell":
            r = subprocess.run(args["command"], shell=True, capture_output=True, text=True, cwd=str(ws))
            return (r.stdout + r.stderr).strip() or "(exit 0)", None
        return f"fake {name}", None

    SCRIPTS = {}
    monkeypatch.setattr(sa, "_vllm_stream", fake_stream)
    monkeypatch.setattr(sa, "execute_tool", fake_exec)
    return {"ws": ws, "repo": repo, "scripts": SCRIPTS, "calls": calls}


def _spawn(args):
    from core.tools.handlers.agent import h_spawn_agent
    out, _ = asyncio.run(h_spawn_agent("", args))
    return out


def _summary(out):
    """只取结果末尾的隔离汇总段 (每个子代理输出尾部也各有一行简报)。"""
    assert "--- 子代理工作树隔离 ---" in out, out
    return out.split("--- 子代理工作树隔离 ---", 1)[1]


def _lc_branches(repo):
    return _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/lc-agent/").split()


def _wt_dirs(ws):
    base = ws / ".agent_worktrees"
    return [p for p in base.iterdir() if p.name != "_meta"] if base.exists() else []


# ── 两个 coder 并行改不同文件: 都合并, 主仓库两处改动都在, 工作树已清理 ──
def test_parallel_different_files_both_merged(env):
    repo, ws = env["repo"], env["ws"]
    env["scripts"]["改 a"] = [("write_file", {"filepath": str(repo / "a.py"), "content": "x = 2\n"}),
                             ("execute_shell", {"command": "pwd"})]
    env["scripts"]["改 b"] = [("write_file", {"filepath": "b.py", "content": "y = 2\n"})]
    out = _spawn({"parallel_tasks": [{"task": "改 a", "agent_type": "coder"},
                                     {"task": "改 b", "agent_type": "coder"}],
                  "workdir": str(repo)})
    assert (repo / "a.py").read_text() == "x = 2\n"
    assert (repo / "b.py").read_text() == "y = 2\n"
    assert _summary(out).count("] 已合并") == 2, out
    assert _lc_branches(repo) == []
    assert _wt_dirs(ws) == []
    assert len(_git(repo, "worktree", "list").splitlines()) == 1
    # 写入路径被映射进工作树 (绝对路径 + 相对路径), shell 前置 cd 到工作树
    writes = [a["filepath"] for n, a in env["calls"] if n == "write_file"]
    assert all("/.agent_worktrees/" in w for w in writes), writes
    shell = [a["command"] for n, a in env["calls"] if n == "execute_shell"][0]
    assert shell.startswith("cd ") and "/.agent_worktrees/coder-" in shell
    # 主工作区干净 (工作树目录已写进 info/exclude 或不在仓库内)
    assert _git(repo, "status", "--porcelain") == ""
    assert "合并子代理" in _git(repo, "log", "--oneline", "-5")


# ── DAG 两个无依赖 coder 并发 (经 DAGOrchestrator + auto critic): 也隔离并都合并 ──
def test_dag_concurrent_coders_isolated(env):
    repo = env["repo"]
    nonce = uuid.uuid4().hex[:8]   # DAG checkpoint 按步骤内容哈希缓存, 同内容重跑会直接恢复, 塞 nonce
    ta, tb = f"dag a {nonce}", f"dag b {nonce}"
    env["scripts"][ta] = [("write_file", {"filepath": "a.py", "content": "x = 'dag'\n"})]
    env["scripts"][tb] = [("write_file", {"filepath": "b.py", "content": "y = 'dag'\n"})]
    out = _spawn({"dag_tasks": [{"step_id": "s1", "task": ta, "agent_type": "coder", "max_retries": 0},
                                {"step_id": "s2", "task": tb, "agent_type": "coder", "max_retries": 0}],
                  "workdir": str(repo)})
    assert _summary(out).count("] 已合并") == 2, out
    assert (repo / "a.py").read_text() == "x = 'dag'\n" and (repo / "b.py").read_text() == "y = 'dag'\n"
    assert _lc_branches(repo) == [] and _wt_dirs(env["ws"]) == []


# ── 同一文件同一行: 一个合并成功, 一个冲突, 保留分支与工作树, discard 能清理 ──
def test_parallel_same_line_conflict_then_discard(env):
    repo, ws = env["repo"], env["ws"]
    env["scripts"]["改1"] = [("write_file", {"filepath": str(repo / "a.py"), "content": "x = 100\n"})]
    env["scripts"]["改2"] = [("write_file", {"filepath": str(repo / "a.py"), "content": "x = 200\n"})]
    out = _spawn({"parallel_tasks": [{"task": "改1", "agent_type": "coder"},
                                     {"task": "改2", "agent_type": "coder"}],
                  "workdir": str(repo)})
    summ = _summary(out)
    assert summ.count("] 已合并") == 1 and summ.count("] 合并冲突") == 1, out
    assert "冲突文件: a.py" in summ and "lc-agent/coder-" in summ
    assert (repo / "a.py").read_text() in ("x = 100\n", "x = 200\n")
    assert _git(repo, "status", "--porcelain") == ""          # merge --abort 后干净
    branches = _lc_branches(repo)
    assert len(branches) == 1 and len(_wt_dirs(ws)) == 1
    listing = asyncio.run(wt.agent_worktree_tool({"action": "list"}))
    wid = branches[0][len("lc-agent/"):]
    assert wid in listing and "合并冲突" in listing
    diff = asyncio.run(wt.agent_worktree_tool({"action": "diff", "id": wid}))
    assert "a.py" in diff
    res = asyncio.run(wt.agent_worktree_tool({"action": "merge", "id": wid}))
    assert "合并冲突" in res                                   # 再合仍冲突, 仍保留
    res = asyncio.run(wt.agent_worktree_tool({"action": "discard", "id": wid}))
    assert "已丢弃" in res
    assert _lc_branches(repo) == [] and _wt_dirs(ws) == []
    assert "没有待处理" in asyncio.run(wt.agent_worktree_tool({"action": "list"}))


# ── 路径映射单元: 绝对/相对/仓库外/execute_shell/目录工具/apply_blocks ──
def test_path_mapping_units(env):
    repo, ws = env["repo"], env["ws"]
    (repo / "sub").mkdir()
    ctx = wt.WorktreeCtx("coder-x", "coder", repo, ws / ".agent_worktrees" / "coder-x",
                         repo / "sub", "lc-agent/coder-x", "HEAD")
    w = str(ws / ".agent_worktrees" / "coder-x")
    assert ctx.map_path(str(repo / "a.py")) == w + "/a.py"
    assert ctx.map_path(str(repo)) == w
    assert ctx.map_path("m.py") == w + "/sub/m.py"                  # 相对 → 工作树中的 workdir
    assert ctx.map_path("../a.py") == w + "/a.py"
    assert ctx.map_path("/etc/hosts") == "/etc/hosts"               # 仓库外不动
    assert ctx.map_path(w + "/a.py") == w + "/a.py"                 # 已在工作树不重复映射
    assert ctx.map_path(str(repo) + "x/a.py") == str(repo) + "x/a.py"  # 前缀相同的兄弟目录不误伤
    cmd = ctx.map_command(f"python {repo}/a.py && cat '{repo}/b.py' {repo}x/c")
    assert cmd.startswith(f"cd {w}/sub && ")
    assert f"python {w}/a.py" in cmd and f"'{w}/b.py'" in cmd and f"{repo}x/c" in cmd
    token = wt._ACTIVE.set(ctx)
    try:
        assert wt.rewrite_tool_args("get_tree", {})["path"] == w + "/sub"
        assert wt.rewrite_tool_args("read_file", {"filepath": "a.py"})["filepath"] == w + "/sub/a.py"
        blocks = f"{repo}/a.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 3\n>>>>>>> REPLACE"
        assert wt.rewrite_tool_args("apply_blocks", {"blocks": blocks})["blocks"].startswith(w + "/a.py\n")
        assert wt.rewrite_tool_args("web_search", {"query": str(repo)}) == {"query": str(repo)}
    finally:
        wt._ACTIVE.reset(token)
    # 未挂工作树 (主代理/其他子代理): 原样返回
    assert wt.rewrite_tool_args("write_file", {"filepath": "a.py"}) == {"filepath": "a.py"}


# ── 非 git 目录: 不隔离, 结果里说明 ──
def test_non_git_workdir_not_isolated(env):
    plain = env["ws"] / "plain"
    plain.mkdir()
    env["scripts"]["p1"] = [("write_file", {"filepath": str(plain / "1.txt"), "content": "1"})]
    env["scripts"]["p2"] = [("write_file", {"filepath": str(plain / "2.txt"), "content": "2"})]
    out = _spawn({"parallel_tasks": [{"task": "p1", "agent_type": "coder"},
                                     {"task": "p2", "agent_type": "coder"}],
                  "workdir": str(plain)})
    assert "不是 git 仓库" in out and "未隔离" in out
    assert (plain / "1.txt").exists() and (plain / "2.txt").exists()
    assert _wt_dirs(env["ws"]) == []


# ── isolation=none: 旧行为 (共用目录, 不建工作树, 结果无隔离段) ──
def test_isolation_none_keeps_old_behavior(env):
    repo = env["repo"]
    env["scripts"]["n1"] = [("write_file", {"filepath": str(repo / "a.py"), "content": "x = 7\n"})]
    env["scripts"]["n2"] = [("write_file", {"filepath": str(repo / "b.py"), "content": "y = 7\n"})]
    out = _spawn({"parallel_tasks": [{"task": "n1", "agent_type": "coder"},
                                     {"task": "n2", "agent_type": "coder"}],
                  "workdir": str(repo), "isolation": "none"})
    assert "子代理工作树隔离" not in out
    assert not (env["ws"] / ".agent_worktrees").exists()
    assert all("/.agent_worktrees/" not in a.get("filepath", "") for _, a in env["calls"])
    assert "a.py" in _git(repo, "status", "--porcelain")           # 直接改在主工作区, 未提交


# ── 主工作区有未提交改动: 不自动合并, 提示; 提交后 agent_worktree merge 成功 ──
def test_dirty_main_not_auto_merged(env):
    repo, ws = env["repo"], env["ws"]
    (repo / "b.py").write_text("y = 'dirty'\n")
    env["scripts"]["d1"] = [("write_file", {"filepath": str(repo / "a.py"), "content": "x = 9\n"})]
    env["scripts"]["d2"] = [("write_file", {"filepath": str(repo / "c.py"), "content": "z = 9\n"})]
    out = _spawn({"parallel_tasks": [{"task": "d1", "agent_type": "coder"},
                                     {"task": "d2", "agent_type": "coder"}],
                  "workdir": str(repo)})
    assert "未提交改动" in out and "不含这些改动" in out
    assert _summary(out).count("] 未合并(主工作区有改动)") == 2, out
    assert (repo / "a.py").read_text() == "x = 1\n"
    assert len(_lc_branches(repo)) == 2 and len(_wt_dirs(ws)) == 2
    _git(repo, "commit", "-qam", "user wip")
    for b in _lc_branches(repo):
        res = asyncio.run(wt.agent_worktree_tool({"action": "merge", "id": b[len("lc-agent/"):]}))
        assert "已合并" in res, res
    assert (repo / "a.py").read_text() == "x = 9\n" and (repo / "c.py").exists()
    assert _lc_branches(repo) == [] and _wt_dirs(ws) == []


# ── 单个子代理 (auto) 不隔离; isolation=worktree 时单个也隔离; 无改动直接清理 ──
def test_single_agent_auto_vs_forced(env):
    repo, ws = env["repo"], env["ws"]
    assert not wt.plan_isolation({"task": "t", "agent_type": "coder", "workdir": str(repo)}, single=True).enabled
    env["scripts"]["s1"] = [("write_file", {"filepath": "new.py", "content": "n = 1\n"})]
    out = _spawn({"task": "s1", "agent_type": "coder", "workdir": str(repo), "isolation": "worktree"})
    assert "已合并" in out and (repo / "new.py").read_text() == "n = 1\n"
    env["scripts"]["s2"] = [("execute_shell", {"command": "ls"})]
    out = _spawn({"task": "s2", "agent_type": "coder", "workdir": str(repo), "isolation": "worktree"})
    assert "无改动" in out and _lc_branches(repo) == [] and _wt_dirs(ws) == []


# ── auto 判定: parallel / DAG 并发 / DAG 串行 / pipeline / 只读角色 ──
def test_concurrency_detection():
    cw = wt._concurrent_writers
    assert cw({"parallel_tasks": [{"agent_type": "coder"}, {"agent_type": "tester"}]}) == 2
    assert cw({"parallel_tasks": [{"agent_type": "coder"}, {"agent_type": "explorer"}]}) == 1
    assert cw({"pipeline_tasks": [{"agent_type": "coder"}, {"agent_type": "coder"}]}) == 0
    dag_seq = [{"step_id": "a", "agent_type": "coder"},
               {"step_id": "b", "agent_type": "coder", "depends_on": ["a"]}]
    assert cw({"dag_tasks": dag_seq}) == 1
    dag_par = [{"step_id": "d", "agent_type": "explorer"},
               {"step_id": "a", "agent_type": "coder", "depends_on": ["d"]},
               {"step_id": "b", "agent_type": "writer", "depends_on": ["d"]},
               {"step_id": "c", "agent_type": "critic", "depends_on": ["a", "b"]}]
    assert cw({"dag_tasks": dag_par}) == 2
    assert not wt.is_write_role("critic") and not wt.is_write_role("tool")
    assert wt.is_write_role("coder") and wt.is_write_role("tester") and wt.is_write_role("writer")


# ── 清理: 超过 24h 的已合并/已丢弃元数据被删, 未合并的保留 ──
def test_cleanup_stale(env):
    repo = env["repo"]
    for wid, st in (("old-merged", "merged"), ("old-conflict", "conflict")):
        wt._save_meta({"id": wid, "repo": str(repo), "path": str(env["ws"] / wid),
                       "branch": "lc-agent/" + wid, "status": st})
    for wid in ("old-merged", "old-conflict"):
        p = wt._meta_dir() / f"{wid}.json"
        m = json.loads(p.read_text())
        m["updated"] -= 2 * 24 * 3600
        p.write_text(json.dumps(m))
    assert wt.cleanup_stale() == 1
    assert wt.load_meta("old-merged") is None and wt.load_meta("old-conflict") is not None


# ── WORKSPACE 本身就是仓库根: 工作树目录落在仓库内, 写进 info/exclude, 主工作区保持干净 ──
def test_workspace_is_repo_root(env, monkeypatch):
    repo = env["repo"]
    monkeypatch.setattr(wt, "WORKSPACE_OVERRIDE", repo)
    env["scripts"]["r1"] = [("write_file", {"filepath": str(repo / "a.py"), "content": "x = 'r'\n"}),
                            ("execute_shell", {"command": f"cat {repo}/a.py"})]
    env["scripts"]["r2"] = [("write_file", {"filepath": "b.py", "content": "y = 'r'\n"})]
    out = _spawn({"parallel_tasks": [{"task": "r1", "agent_type": "coder"}, {"task": "r2", "agent_type": "coder"}],
                  "workdir": str(repo)})
    assert _summary(out).count("] 已合并") == 2, out
    assert "/.agent_worktrees/" in (repo / ".git" / "info" / "exclude").read_text()
    assert _git(repo, "status", "--porcelain") == ""
    shell = [a["command"] for n, a in env["calls"] if n == "execute_shell"][0]
    assert f"cat {repo}/.agent_worktrees/coder-" in shell        # 映射后不会被二次替换


# ── 生产主循环的派发路径 (spawn_dispatch → handle_spawn_agent_enhanced) 也带隔离汇总 ──
def test_main_loop_dispatch_path(env):
    from types import SimpleNamespace
    from lib.agent import spawn_dispatch as sd
    repo = env["repo"]
    env["scripts"]["m1"] = [("write_file", {"filepath": "a.py", "content": "x = 'm'\n"})]
    env["scripts"]["m2"] = [("write_file", {"filepath": "b.py", "content": "y = 'm'\n"})]
    args = {"parallel_tasks": [{"task": "m1", "agent_type": "coder"}, {"task": "m2", "agent_type": "coder"}],
            "workdir": str(repo)}

    async def _go():
        out = {"effective_timeout": 0}
        ctx = SimpleNamespace(session_id="wt-test-sid", tracer=None)
        async for _ in sd._dispatch_spawn_agent(args, ctx, None, asyncio.Queue(), out):
            pass
        return out["result"]
    res = asyncio.run(_go())
    assert _summary(res).count("] 已合并") == 2, res
    assert (repo / "a.py").read_text() == "x = 'm'\n" and (repo / "b.py").read_text() == "y = 'm'\n"
