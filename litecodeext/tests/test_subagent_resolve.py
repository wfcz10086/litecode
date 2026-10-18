"""子代理工作树合并冲突自动解决 (lib/agent/subagent_resolve.py) 的回归用例。

确定性解析单元 + 真实 git 仓库端到端: 经真实 spawn_agent handler 跑两个并行 coder, 子代理与解冲突子代理的
LLM 流 (_vllm_stream) 一律打桩, 假 execute_tool 真写盘, 路径映射 / git 反向合并 / 校验 / 回退都是真实效果。
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib.agent import subagent as sa  # noqa: E402
from lib.agent import subagent_resolve as sr  # noqa: E402
from lib.agent import subagent_worktree as wt  # noqa: E402
from lib.agent.subagent_resolve import resolve_append_conflicts  # noqa: E402

RESOLVER_MARK = "[合并冲突解决]"


# ═══════════════════════════ 确定性解析单元 ═══════════════════════════

def test_append_at_eof_keeps_both_ours_first():
    txt = ("# B\n- 0\n"
           "<<<<<<< HEAD\n- A\n||||||| merged common ancestors\n=======\n- B\n>>>>>>> abc123\n")
    assert resolve_append_conflicts(txt) == "# B\n- 0\n- A\n- B\n"


def test_append_before_list_close_and_shared_base_prefix():
    txt = ("__all__ = [\n    'x',\n"
           "<<<<<<< ours\n    'a',\n||||||| base\n=======\n    'b',\n>>>>>>> theirs\n]\n")
    assert resolve_append_conflicts(txt) == "__all__ = [\n    'x',\n    'a',\n    'b',\n]\n"
    # 祖先段非空但两边都以它开头再各自追加 → 祖先 + 两边追加
    txt2 = "<<<<<<< o\nend\nA\n||||||| b\nend\n=======\nend\nB\n>>>>>>> t\n"
    assert resolve_append_conflicts(txt2) == "end\nA\nB\n"
    # 追加块后面还有普通行 (不在末尾)
    txt3 = "<<<<<<< o\nA\n||||||| b\n=======\nB\n>>>>>>> t\nmore\n"
    assert resolve_append_conflicts(txt3) is None          # 块后是普通行 → 不确定


def test_same_line_modification_returns_none():
    txt = "<<<<<<< HEAD\nx = 1\n||||||| base\nx = 0\n=======\nx = 2\n>>>>>>> t\n"
    assert resolve_append_conflicts(txt) is None
    # 追加但后面还有普通代码 (不在末尾/列表收尾) → 不确定
    mid = "<<<<<<< HEAD\na()\n||||||| base\n=======\nb()\n>>>>>>> t\nmain()\n"
    assert resolve_append_conflicts(mid) is None
    # 非 diff3 格式 (没有祖先段) → 不猜
    assert resolve_append_conflicts("<<<<<<< HEAD\nA\n=======\nB\n>>>>>>> t\n") is None
    # 未闭合
    assert resolve_append_conflicts("<<<<<<< HEAD\nA\n||||||| b\n=======\nB\n") is None
    # 无冲突标记原样返回
    assert resolve_append_conflicts("plain\n") == "plain\n"


# ═══════════════════════════ 端到端环境 ═══════════════════════════

def _git(repo, *args):
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=str(repo),
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from lib.config import CFG
    ws = tmp_path / "ws"
    repo = ws / "proj"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@t")
    (repo / "a.py").write_text("def f():\n    return 1\n")
    (repo / "BACKLOG.md").write_text("# BACKLOG\n- 0\n")
    (repo / "other.txt").write_text("o\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    monkeypatch.setattr(wt, "WORKSPACE_OVERRIDE", ws)
    monkeypatch.setitem(CFG, "agent", {**(CFG.get("agent") or {}), "worktree_auto_resolve": True,
                                       "worktree_verify_cmd": ""})

    iters, calls, resolver_tasks = {}, [], []
    scripts = {"resolver": []}

    async def fake_stream(messages, tools):
        task = messages[1]["content"]
        key = "resolver" if task.startswith(RESOLVER_MARK) else task
        if key == "resolver" and task not in iters:
            resolver_tasks.append(task)
        it = iters.setdefault(task, iter(list(scripts.get(key, []))))
        await asyncio.sleep(0.01)
        rnd = next(it, None)
        if rnd is None:
            yield {"choices": [{"delta": {"content": f"done: {key}"}}]}
            return
        name, args = rnd
        yield {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": f"c{len(calls)}", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}

    async def fake_exec(name, args, sid):
        calls.append((name, dict(args)))
        if name == "write_file":
            p = Path(args["filepath"])
            p = p if p.is_absolute() else ws / p
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"])
            return f"wrote {p}", None
        return f"fake {name}", None

    monkeypatch.setattr(sa, "_vllm_stream", fake_stream)
    monkeypatch.setattr(sa, "execute_tool", fake_exec)
    return {"ws": ws, "repo": repo, "scripts": scripts, "calls": calls,
            "resolver_tasks": resolver_tasks, "cfg": CFG}


def _spawn(env, ta, tb):
    from core.tools.handlers.agent import h_spawn_agent
    out, _ = asyncio.run(h_spawn_agent("", {
        "parallel_tasks": [{"task": ta, "agent_type": "coder"}, {"task": tb, "agent_type": "coder"}],
        "workdir": str(env["repo"])}))
    return out


def _summary(out):
    assert "--- 子代理工作树隔离 ---" in out, out
    return out.split("--- 子代理工作树隔离 ---", 1)[1]


def _lc_branches(repo):
    return _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/lc-agent/").split()


def _assert_main_clean(repo):
    assert _git(repo, "status", "--porcelain") == ""
    gd = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    assert not (gd / "MERGE_HEAD").exists()


def _backlog_tasks(env):
    repo = env["repo"]
    env["scripts"]["加 A"] = [("write_file", {"filepath": str(repo / "BACKLOG.md"),
                                             "content": "# BACKLOG\n- 0\n- A\n"})]
    env["scripts"]["加 B"] = [("write_file", {"filepath": "BACKLOG.md", "content": "# BACKLOG\n- 0\n- B\n"})]
    return "加 A", "加 B"


def _same_line_tasks(env):
    repo = env["repo"]
    env["scripts"]["改 2"] = [("write_file", {"filepath": str(repo / "a.py"), "content": "def f():\n    return 2\n"})]
    env["scripts"]["改 3"] = [("write_file", {"filepath": "a.py", "content": "def f():\n    return 3\n"})]
    return "改 2", "改 3"


# ── 两个子代理都往 BACKLOG 末尾追加: 确定性两边保留, 自动合并成功, 不派解冲突子代理 ──
def test_backlog_append_auto_merged(env):
    repo = env["repo"]
    out = _spawn(env, *_backlog_tasks(env))
    summ = _summary(out)
    assert summ.count("] 已合并") == 2, out
    assert "冲突已自动解决: BACKLOG.md (确定性)" in out, out
    text = (repo / "BACKLOG.md").read_text()
    assert "- A\n" in text and "- B\n" in text and "<<<<<<<" not in text
    assert env["resolver_tasks"] == []
    assert _lc_branches(repo) == []
    _assert_main_clean(repo)


# ── 同一函数同一行: mock 解冲突子代理把冲突文件写好 (经工作树路径映射) → 校验通过 → 合并 ──
def test_same_line_resolved_by_mock_resolver(env):
    repo = env["repo"]
    env["scripts"]["resolver"] = [("write_file", {"filepath": str(repo / "a.py"),
                                                  "content": "def f():\n    return 2 + 3\n"})]
    out = _spawn(env, *_same_line_tasks(env))
    assert _summary(out).count("] 已合并") == 2, out
    assert "冲突已自动解决: a.py (子代理)" in out, out
    assert (repo / "a.py").read_text() == "def f():\n    return 2 + 3\n"
    assert len(env["resolver_tasks"]) == 1
    task = env["resolver_tasks"][0]
    assert "- a.py" in task and "本侧 diff 摘要" in task and "对侧 diff 摘要" in task and "return" in task
    rw = [a["filepath"] for n, a in env["calls"] if n == "write_file"][-1]
    assert "/.agent_worktrees/coder-" in rw                       # 解冲突子代理写的是工作树
    assert sa._SUBAGENT_CONFIGS[sr.RESOLVER_TYPE]["max_iter"] == 20
    assert _lc_branches(repo) == []
    _assert_main_clean(repo)


# ── mock 解冲突子代理什么都没改 (标记残留) → 回退: 保留分支, 工作树回到反向合并前, 主工作区干净 ──
def test_resolver_leaves_markers_falls_back(env):
    repo = env["repo"]
    out = _spawn(env, *_same_line_tasks(env))
    summ = _summary(out)
    assert summ.count("] 已合并") == 1 and summ.count("] 合并冲突") == 1, out
    assert "自动解决失败: a.py 仍有冲突标记" in out, out
    _assert_main_clean(repo)
    (branch,) = _lc_branches(repo)
    meta = wt.load_meta(branch[len("lc-agent/"):])
    wtp = Path(meta["path"])
    assert _git(wtp, "status", "--porcelain") == ""
    assert _git(wtp, "rev-parse", "HEAD") == meta["commit"]        # 反向合并已撤销
    assert not (Path(_git(wtp, "rev-parse", "--absolute-git-dir")) / "MERGE_HEAD").exists()
    assert (repo / "a.py").read_text() in ("def f():\n    return 2\n", "def f():\n    return 3\n")


# ── 生产实测回归 (2026-10-18): 解冲突时跑代码生成 __pycache__ 不算越界, 缓存也不进提交 ──
def test_resolver_python_cache_not_counted_as_out_of_scope(env):
    repo = env["repo"]
    env["scripts"]["resolver"] = [
        ("write_file", {"filepath": str(repo / "a.py"), "content": "def f():\n    return 2 + 3\n"}),
        ("write_file", {"filepath": str(repo / "__pycache__" / "a.cpython-312.pyc"), "content": "junk"}),
        ("write_file", {"filepath": str(repo / ".pytest_cache" / "v" / "x"), "content": "junk"}),
    ]
    out = _spawn(env, *_same_line_tasks(env))
    assert _summary(out).count("] 已合并") == 2, out
    assert "冲突已自动解决: a.py (子代理)" in out, out
    tracked = _git(repo, "ls-files").split()
    assert not any("__pycache__" in f or ".pytest_cache" in f for f in tracked), tracked
    _assert_main_clean(repo)


def test_worktree_commit_excludes_python_cache(env):
    repo = env["repo"]
    env["scripts"]["改 2"] = [
        ("write_file", {"filepath": str(repo / "a.py"), "content": "def f():\n    return 2\n"}),
        ("write_file", {"filepath": str(repo / "pkg" / "__pycache__" / "m.cpython-312.pyc"), "content": "j"}),
        ("write_file", {"filepath": str(repo / "stray.pyc"), "content": "j"})]
    env["scripts"]["只有缓存"] = [
        ("write_file", {"filepath": str(repo / "__pycache__" / "a.cpython-312.pyc"), "content": "j"})]
    out = _spawn(env, "改 2", "只有缓存")
    tracked = _git(repo, "ls-files").split()
    assert (repo / "a.py").read_text() == "def f():\n    return 2\n", out
    assert not any(wt.is_junk(f) for f in tracked), tracked
    assert wt.is_junk("pkg/__pycache__/m.cpython-312.pyc") and wt.is_junk("x.pyc")
    assert not wt.is_junk("pycache_notes.md") and not wt.is_junk("src/app.py")
    _assert_main_clean(repo)


# ── 解冲突子代理改出语法错误 / 改了冲突文件以外的文件 → 回退 ──
@pytest.mark.parametrize("script,reason", [
    ([("write_file", {"filepath": "a.py", "content": "def f(:\n    return 5\n"})], "语法错误"),
    ([("write_file", {"filepath": "a.py", "content": "def f():\n    return 5\n"}),
      ("write_file", {"filepath": "other.txt", "content": "hacked\n"})], "冲突文件以外"),
])
def test_resolver_bad_output_falls_back(env, script, reason):
    repo = env["repo"]
    env["scripts"]["resolver"] = script
    out = _spawn(env, *_same_line_tasks(env))
    assert _summary(out).count("] 合并冲突") == 1 and reason in out, out
    _assert_main_clean(repo)
    assert (repo / "other.txt").read_text() == "o\n"
    assert len(_lc_branches(repo)) == 1


# ── 配了验证命令: 失败 → 回退 (原因写明退出码); 成功 → 合并 ──
def test_verify_cmd_failure_falls_back(env, monkeypatch):
    repo, CFG = env["repo"], env["cfg"]
    monkeypatch.setitem(CFG, "agent", {**CFG["agent"], "worktree_verify_cmd": "grep -q '^- A' BACKLOG.md && exit 3"})
    out = _spawn(env, *_backlog_tasks(env))
    summ = _summary(out)
    assert summ.count("] 合并冲突") == 1 and "验证命令退出码 3" in out, out
    _assert_main_clean(repo)
    assert len(_lc_branches(repo)) == 1


def test_verify_cmd_success_merges(env, monkeypatch):
    repo, CFG = env["repo"], env["cfg"]
    monkeypatch.setitem(CFG, "agent", {**CFG["agent"], "worktree_verify_cmd": "test -f BACKLOG.md"})
    out = _spawn(env, *_backlog_tasks(env))
    assert _summary(out).count("] 已合并") == 2, out
    _assert_main_clean(repo)


# ── 开关关闭: 旧行为 (冲突直接保留); 之后 agent_worktree resolve 手动触发同一流程 ──
def test_switch_off_keeps_old_behavior_then_manual_resolve(env, monkeypatch):
    repo, CFG = env["repo"], env["cfg"]
    monkeypatch.setitem(CFG, "agent", {**CFG["agent"], "worktree_auto_resolve": False})
    out = _spawn(env, *_backlog_tasks(env))
    summ = _summary(out)
    assert summ.count("] 合并冲突") == 1 and "冲突文件: BACKLOG.md" in summ, out
    assert "自动解决" not in out
    _assert_main_clean(repo)
    (branch,) = _lc_branches(repo)
    wid = branch[len("lc-agent/"):]
    res = asyncio.run(wt.agent_worktree_tool({"action": "resolve", "id": wid}))
    assert "已合并" in res and "冲突已自动解决: BACKLOG.md (确定性)" in res, res
    text = (repo / "BACKLOG.md").read_text()
    assert "- A\n" in text and "- B\n" in text
    assert _lc_branches(repo) == []
    _assert_main_clean(repo)
    assert "无需处理" in asyncio.run(wt.agent_worktree_tool({"action": "resolve", "id": wid}))


# ── 主工作区脏: 维持保护, resolve 不自动合并也不在主工作区留半合并 ──
def test_manual_resolve_respects_dirty_main(env, monkeypatch):
    repo, CFG = env["repo"], env["cfg"]
    monkeypatch.setitem(CFG, "agent", {**CFG["agent"], "worktree_auto_resolve": False})
    _spawn(env, *_backlog_tasks(env))
    (branch,) = _lc_branches(repo)
    (repo / "other.txt").write_text("dirty\n")
    res = asyncio.run(wt.agent_worktree_tool({"action": "resolve", "id": branch[len("lc-agent/"):]}))
    assert "未合并(主工作区有改动)" in res, res
    assert _git(repo, "status", "--porcelain") == "M other.txt"
    assert not (Path(_git(repo, "rev-parse", "--absolute-git-dir")) / "MERGE_HEAD").exists()
    assert env["resolver_tasks"] == []
