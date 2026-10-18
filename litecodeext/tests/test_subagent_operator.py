"""远程操作员子代理 (operator) 回归用例: 多台 Windows 并行、节点独占、截图回流、证据判定。

经真实 spawn_agent handler → 编排器 → _run_subagent → 真实 windows_desktop / windows_system handler 跑,
只打桩两处: 子代理 LLM 流 (_vllm_stream, 按任务脚本给工具调用) 与 Windows 节点通信
(lib.windows_desktop.execute, 每次动作生成一张真 PNG 截图放在工作区 uploads 下)。不连任何真实节点。
"""
import asyncio
import json
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from lib.agent import subagent as sa  # noqa: E402
from lib.agent import subagent_evidence as ev  # noqa: E402
from lib.agent import subagent_operator as op  # noqa: E402
from lib.config import CFG  # noqa: E402
import lib.config as _lcfg  # noqa: E402
from core.tools.handlers import windows_desktop as wd  # noqa: E402
from core.tools.handlers.windows_desktop import h_windows_desktop  # noqa: E402
from core.tools.handlers.windows_system import h_windows_system  # noqa: E402
import core.tool_dispatch as _td_mod  # noqa: E402

_ORIG_ADD_ARTIFACT = _td_mod._add_artifact      # fixture 会把它打成 no-op, 产出物用例要用真实实现


def _t(name):
    return f"{name} {uuid.uuid4().hex[:6]}"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from PIL import Image
    ws = tmp_path / "ws"
    (ws / "uploads").mkdir(parents=True)
    monkeypatch.setattr(_lcfg, "WORKSPACE", ws)
    monkeypatch.setitem(CFG, "agent", {**(CFG.get("agent") or {}), "subagent_evidence": True,
                                       "operator_lock_wait": 5})
    import core.tool_dispatch as _td
    monkeypatch.setattr(_td, "_add_artifact", lambda *a, **k: None)
    sa._SUBAGENT_CACHE.clear()
    wd._LAST_SHOT.clear()
    st = SimpleNamespace(ws=ws, scripts={}, its={}, seen={}, tools={}, node_calls=[], exec_calls=[],
                         active=set(), max_active=0, hooks={}, n=0)
    lock = threading.Lock()

    def fake_execute(args, workspace):          # 模拟 Windows 节点: 每个动作返回一张新截图
        with lock:
            st.n += 1
            n = st.n
        st.node_calls.append(dict(args))
        if args.get("action") in ("inspect_file", "list_dir", "run_powershell", "upload", "processes"):
            return {"ok": True, "alias": args.get("alias"), "action": args["action"],
                    "path": args.get("path", ""), "content": f"文件真值 {args.get('alias')}"}
        p = ws / "uploads" / f"shot_{args.get('alias')}_{n}.png"
        Image.new("RGB", (64, 48), (n * 20 % 255, 80, 120)).save(p)
        return {"ok": True, "alias": args.get("alias"), "action": args.get("action"),
                "state": "ok", "image": str(p), "pad": "x" * 4000}     # 超过子代理 3000 字截断

    monkeypatch.setattr("lib.windows_desktop.execute", fake_execute)

    def key_of(task):
        return next((k for k in st.scripts if task.startswith(k)), task)

    async def fake_stream(messages, tools):
        k = key_of(messages[1]["content"])
        st.seen.setdefault(k, []).append([dict(m) for m in messages])
        st.tools[k] = [t["function"]["name"] for t in tools]
        st.active.add(k)
        st.max_active = max(st.max_active, len(st.active))
        try:
            await asyncio.sleep(0.02)
            hook = st.hooks.get((k, len(st.seen[k])))
            if hook:
                await hook()
        finally:
            st.active.discard(k)
        it = st.its.setdefault(k, iter(st.scripts.get(k, [])))
        step = next(it, None)
        if step is None or step[0] == "text":
            yield {"choices": [{"delta": {"content": step[1] if step else f"done: {k}"}}]}
            return
        _, name, args = step
        yield {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": f"c{uuid.uuid4().hex[:6]}",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}}]}

    async def fake_exec(name, args, sid):
        st.exec_calls.append((sid, name, dict(args)))
        if name == "windows_desktop":
            return await h_windows_desktop(sid, args)
        if name == "windows_system":
            return await h_windows_system(sid, args)
        return f"fake {name}", None

    monkeypatch.setattr(sa, "_vllm_stream", fake_stream)
    monkeypatch.setattr(sa, "execute_tool", fake_exec)
    yield st
    assert not op._OWNER and not op._SID_ALIAS          # 每个用例结束锁都已释放


def _spawn(args, sid="main"):
    from core.tools.handlers.agent import h_spawn_agent
    out, _ = asyncio.run(h_spawn_agent(sid, args))
    return out


def _gui_script(alias):
    """截图 → 点击 (不带 reference_image, 由 handler 自动补) → 打字 → ctrl+s 保存 → inspect_file 回读 → 总结"""
    return [("tool", "windows_desktop", {"alias": alias, "action": "screenshot"}),
            ("tool", "windows_desktop", {"alias": alias, "action": "click", "x": 10, "y": 10}),
            ("tool", "windows_desktop", {"alias": alias, "action": "type_text", "text": "A 完成"}),
            ("tool", "windows_desktop", {"alias": alias, "action": "hotkey", "keys": "ctrl+s"}),
            ("tool", "windows_system", {"alias": alias, "action": "inspect_file", "path": "C:/r.txt"}),
            ("text", f"{alias} 已保存并回读")]


# ── 两个操作员分别操作 vm-a / vm-b 并行完成, 各自只调自己的 alias; 截图不串; 截图回流 ──
def test_two_operators_parallel_each_own_alias(env):
    ta, tb = _t("A 机任务"), _t("B 机任务")
    env.scripts[ta], env.scripts[tb] = _gui_script("vm-a"), _gui_script("vm-b")
    wd._LAST_SHOT[("main", "vm-a")] = "/old/main_vm_a.png"      # 主代理手里 vm-a 的旧截图
    wd._LAST_SHOT[("main", "vm-c")] = "/old/main_vm_c.png"
    out = _spawn({"parallel_tasks": [
        {"agent_type": "operator", "alias": "vm-a", "task": ta},
        {"agent_type": "operator", "alias": "vm-b", "task": tb}]})
    assert env.max_active == 2                                    # 真并行
    assert out.startswith("[已验证] 全部 2 个子代理均有机器证据"), out
    assert "vm-a 已保存并回读" in out and "vm-b 已保存并回读" in out
    # 各子代理 sid 只调了自己的 alias
    by_sid = {}
    for sid, name, args in env.exec_calls:
        assert sid.startswith("subagent_operator_")
        by_sid.setdefault(sid, set()).add(args["alias"])
    assert sorted(map(tuple, by_sid.values())) == [("vm-a",), ("vm-b",)]
    # 点击自动补的 reference_image 是本子代理自己那台电脑的最新截图, 不串
    clicks = [c for c in env.node_calls if c["action"] == "click"]
    assert len(clicks) == 2
    for c in clicks:
        assert Path(c["reference_image"]).name.startswith(f"shot_{c['alias']}_")
    # 拿锁时清掉主代理 vm-a 的旧截图 (画面已变), 不相干的 vm-c 保留; 子代理自己的截图结束时清掉
    assert ("main", "vm-a") not in wd._LAST_SHOT and wd._LAST_SHOT[("main", "vm-c")] == "/old/main_vm_c.png"
    assert not any(str(k[0]).startswith("subagent_") for k in wd._LAST_SHOT)
    # 截图回流: 截图之后的下一轮模型输入里有多模态图片消息 (且只保留最新一张)
    for k in (ta, tb):
        hist = env.seen[k][1]                                     # 第 2 次 LLM 调用 (截图之后)
        imgs = [m for m in hist if m["role"] == "user" and isinstance(m["content"], list)]
        assert len(imgs) == 1
        assert imgs[0]["content"][0]["text"].startswith("[DESKTOP_OBSERVATION]")
        assert imgs[0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        last = env.seen[k][-1]
        assert sum(1 for m in last if m["role"] == "user" and isinstance(m["content"], list)) == 1
        assert f"[操作员节点 alias={'vm-a' if k == ta else 'vm-b'}]" in env.seen[k][0][1]["content"]


# ── 同 alias 冲突: 并行 / DAG 无依赖 → 拒绝派发; 管道 / DAG 有依赖 → 允许; 缺 alias → 拒绝 ──
def test_same_alias_conflict_rejected_and_missing_alias(env):
    t1, t2 = _t("同机1"), _t("同机2")
    out = _spawn({"parallel_tasks": [{"agent_type": "operator", "alias": "vm-a", "task": t1},
                                     {"agent_type": "operator", "alias": "vm-a", "task": t2}]})
    assert out.startswith("ERROR: spawn_agent 拒绝派发") and "都要操作 vm-a" in out, out
    assert not env.seen                                           # 一个子代理都没跑
    out = _spawn({"agent_type": "operator", "task": _t("没给电脑")})
    assert out.startswith("ERROR: spawn_agent 派发 operator 必须指定") and not env.seen
    _, err = op.prepare_spawn_args({"dag_tasks": [
        {"step_id": "s1", "agent_type": "operator", "alias": "vm-a", "task": "x"},
        {"step_id": "s2", "agent_type": "operator", "alias": "vm-a", "task": "y"}]})
    assert err and "s1 与 s2" in err
    new, err = op.prepare_spawn_args({"dag_tasks": [
        {"step_id": "s1", "agent_type": "operator", "alias": "vm-a", "task": "x"},
        {"step_id": "s2", "agent_type": "operator", "alias": "vm-a", "task": "y", "depends_on": ["s1"]}]})
    assert err is None and new["dag_tasks"][1]["task"].endswith("[操作员节点 alias=vm-a]")
    new, err = op.prepare_spawn_args({"alias": "vm-a", "pipeline_tasks": [
        {"agent_type": "operator", "task": "x"}, {"agent_type": "operator", "task": "y"}]})
    assert err is None and all(t["alias"] == "vm-a" for t in new["pipeline_tasks"])
    plain = {"task": "x", "agent_type": "coder"}
    assert op.prepare_spawn_args(plain) == (plain, None)          # 不含 operator 零影响
    # 管道串行跑同一台电脑: 两个都完成
    p1, p2 = _t("管道1"), _t("管道2")
    env.scripts[p1] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"}), ("text", "p1 ok")]
    env.scripts[p2] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"}), ("text", "p2 ok")]
    out = _spawn({"alias": "vm-a", "pipeline_tasks": [{"agent_type": "operator", "task": p1},
                                                      {"agent_type": "operator", "task": p2}]})
    assert "2/2 成功" in out and "p2 ok" in out and env.max_active == 1 and len(env.seen) == 2


# ── 子代理调用错误 alias 被拒; 未填 alias 自动补成分配的; 不能调 execute_shell / write_file ──
def test_wrong_alias_and_forbidden_tools_rejected(env):
    t = _t("越权")
    env.scripts[t] = [("tool", "windows_system", {"alias": "vm-b", "action": "list_dir", "path": "C:/"}),
                      ("tool", "windows_desktop", {"action": "screenshot"}),
                      ("tool", "execute_shell", {"command": "rm -rf /tmp/x"}),
                      ("tool", "write_file", {"filepath": "/etc/x", "content": "y"}),
                      ("text", "结束")]
    _spawn({"agent_type": "operator", "alias": "vm-a", "task": t})
    assert {"windows_desktop", "windows_system", "read_file"} <= set(env.tools[t])
    assert not {"execute_shell", "write_file", "patch_file"} & set(env.tools[t])
    tool_msgs = [m["content"] for m in env.seen[t][-1] if m["role"] == "tool"]
    assert "只分配了电脑 vm-a, 不能操作 vm-b" in tool_msgs[0]
    assert "operator 不能调用 execute_shell" in tool_msgs[2] and "operator 不能调用 write_file" in tool_msgs[3]
    assert [c["alias"] for c in env.node_calls] == ["vm-a"]      # vm-b 从未被调用; 缺省 alias 补成 vm-a
    assert not any(n in ("execute_shell", "write_file") for _, n, _ in env.exec_calls)


# ── 运行时独占: 操作员占用期间主代理对同一 alias 立即报错 (不等待), 其它电脑不受影响; 结束后恢复 ──
def test_main_agent_blocked_while_operator_holds(env):
    t = _t("占用")
    env.scripts[t] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"}), ("text", "好了")]
    seen = {}

    async def probe():
        seen["a"], _ = await h_windows_desktop("main", {"alias": "vm-a", "action": "screenshot"})
        seen["sys"], _ = await h_windows_system("main", {"alias": "vm-a", "action": "list_dir", "path": "C:/"})
        seen["b"], _ = await h_windows_desktop("main", {"alias": "vm-b", "action": "screenshot"})
        seen["owner"] = dict(op.owner_of("vm-a") or {})
    env.hooks[(t, 2)] = probe
    _spawn({"agent_type": "operator", "alias": "vm-a", "task": t})
    assert seen["a"].startswith("ERROR: windows_desktop 节点 vm-a 正被操作员子代理独占"), seen["a"]
    assert seen["sys"].startswith("ERROR: windows_system 节点 vm-a 正被操作员子代理独占")
    assert not seen["b"].startswith("ERROR") and seen["owner"]["sid"].startswith("subagent_operator_")
    text, _ = asyncio.run(h_windows_desktop("main", {"alias": "vm-a", "action": "screenshot"}))
    assert not text.startswith("ERROR")                           # 释放后主代理可正常操作


# ── 跨派发/跨会话抢同一台: 操作员按 asyncio.Lock 排队串行; 等锁超时 → [子代理异常] 不死锁 ──
def test_cross_dispatch_lock_serializes_and_times_out(env, monkeypatch):
    t1, t2 = _t("会话1"), _t("会话2")
    for t in (t1, t2):
        env.scripts[t] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"}), ("text", "ok")]

    async def both():
        return await asyncio.gather(sa._run_subagent(f"{t1}\n\n{op.marker('vm-a')}", "operator"),
                                    sa._run_subagent(f"{t2}\n\n{op.marker('vm-a')}", "operator"))
    r = asyncio.run(both())
    assert r == ["ok", "ok"] and env.max_active == 1             # 排队, 从未同时操作
    monkeypatch.setitem(CFG, "agent", {**CFG["agent"], "operator_lock_wait": 0.05})
    t3, t4 = _t("长任务"), _t("等不到")
    env.scripts[t3] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"}), ("text", "长任务完成")]

    async def slow():
        await asyncio.sleep(0.3)
    env.hooks[(t3, 1)] = slow

    async def contend():
        a = asyncio.create_task(sa._run_subagent(f"{t3}\n\n{op.marker('vm-a')}", "operator"))
        await asyncio.sleep(0.01)
        b = await sa._run_subagent(f"{t4}\n\n{op.marker('vm-a')}", "operator")
        return await a, b
    ra, rb = asyncio.run(contend())
    assert ra == "长任务完成" and rb.startswith("[子代理异常] operator: 电脑 vm-a 正被另一个操作员")
    assert t4 not in env.seen
    assert sa._run_subagent and asyncio.run(sa._run_subagent("没有标记", "operator")).startswith(
        "[子代理异常] operator 没有分配 Windows 电脑")


# ── 证据: 界面改文件未回读 → 补证一次; 拒绝补证 → 未验证; 回读后 → 已验证 ──
def test_evidence_file_change_requires_readback(env):
    t = _t("改文件不回读")
    env.scripts[t] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "hotkey", "keys": "ctrl+s"}),
                      ("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"}),
                      ("text", "保存好了, 合计 4163"), ("text", "确实保存好了")]
    out = _spawn({"agent_type": "operator", "alias": "vm-a", "task": t})
    assert out.startswith("[未验证: 界面/上传改过文件 1 次, 但最后一次改动之后没有用 inspect_file"), out
    nudges = [m for m in env.seen[t][-1] if m["role"] == "user" and str(m["content"]).startswith(ev.NUDGE_PREFIX)]
    assert len(nudges) == 1 and "inspect_file" in nudges[0]["content"] and "execute_shell" not in nudges[0]["content"]
    t2 = _t("补证回读")
    env.scripts[t2] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "click_text", "text": "保存"}),
                       ("text", "保存好了"),
                       ("tool", "windows_system", {"alias": "vm-a", "action": "inspect_file", "path": "C:/s.xlsx"}),
                       ("text", "回读核对: 文件真值 vm-a")]
    out = _spawn({"agent_type": "operator", "alias": "vm-a", "task": t2})
    assert out.startswith("[已验证]") and "回读核对: 文件真值 vm-a" in out, out
    assert "改动后已回读: windows_system inspect_file C:/s.xlsx" in out and "补证提示: 已注入 1 次" in out


def test_evidence_rules_unit():
    def col():
        return ev.EvidenceCollector("operator", write_role=ev._is_write_role("operator"))
    assert ev._is_write_role("operator")
    from lib.agent.subagent_worktree import is_write_role
    assert not is_write_role("operator")                          # 不为操作员建服务器 git 工作树
    c = col()                                                     # 只做界面操作 + 最后截图确认 → 已验证
    c.observe("windows_desktop", {"action": "click", "x": 1, "y": 1}, '{"ok": true}')
    c.observe("windows_desktop", {"action": "ocr"}, '{"ok": true}')
    assert c.verdict()[0] and "最后已用截图/OCR" in c.verdict()[1]
    c = col()                                                     # 操作后没确认 → 未验证
    c.observe("windows_desktop", {"action": "screenshot"}, '{"ok": true}')
    c.observe("windows_desktop", {"action": "type_text", "text": "hi"}, '{"ok": true}')
    assert not c.verdict()[0] and "没有截图/OCR" in c.verdict()[1]
    c = col()                                                     # 改文件后只截图不回读 → 仍未验证
    c.observe("windows_desktop", {"action": "drag", "x": 1, "y": 1, "end_x": 2, "end_y": 2}, '{"ok": true}')
    c.observe("windows_desktop", {"action": "screenshot"}, '{"ok": true}')
    assert not c.verdict()[0]
    c.observe("windows_system", {"action": "run_powershell", "script": "Get-Content a"}, '{"ok": true}')
    assert c.verdict()[0]
    c.observe("windows_system", {"action": "upload", "source": "a.txt"}, '{"ok": true}')   # 上传也算改文件
    assert not c.verdict()[0]
    c.observe("windows_system", {"action": "list_dir", "path": "C:/"}, "ERROR: windows_system 超时")  # 失败的回读不算
    assert not c.verdict()[0]
    c = col()                                                     # 只观察 → 走通用取证规则
    c.observe("windows_system", {"action": "system_info"}, '{"ok": true}')
    assert c.verdict()[0] and "windows_system.system_info×1" in c.verdict()[1]
    c = col()                                                     # 在微信里打字回车不是文件改动
    c.observe("windows_desktop", {"action": "hotkey", "keys": "enter"}, '{"ok": true}')
    assert c.op_file_changes == 0 and c.op_actions == 1


# ── 观察类重复调用 (每步截图) 不触发子代理死循环检测; 主派发路径 spawn_dispatch 同样拒绝冲突 ──
def test_repeated_screenshots_not_loop_and_dispatch_path(env):
    t = _t("反复截图")
    env.scripts[t] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"})] * 6 + [("text", "看完了")]
    out = _spawn({"agent_type": "operator", "alias": "vm-a", "task": t})
    assert "loop-detect" not in out and "看完了" in out
    from lib.agent.spawn_dispatch import _dispatch_spawn_agent
    res = {"effective_timeout": 1}

    async def drive():
        args = {"parallel_tasks": [{"agent_type": "operator", "alias": "vm-a", "task": "1"},
                                   {"agent_type": "operator", "alias": "vm-a", "task": "2"}]}
        async for _ in _dispatch_spawn_agent(args, SimpleNamespace(session_id="main", tracer=None),
                                             None, asyncio.Queue(), res):
            pass
    asyncio.run(drive())
    assert res["result"].startswith("ERROR: spawn_agent 拒绝派发")


# ═══════════════ 2026-10-18: 操作员截图/下载登记为父会话产出物 ═══════════════

@pytest.fixture
def arts(env, monkeypatch):
    """恢复真实 _add_artifact (fixture 把它打成了 no-op), 记录器替代 lib.session.add_artifact; download 返回真文件。"""
    import core.tool_dispatch as _td
    import lib.windows_desktop as _lwd
    rec = []
    monkeypatch.setattr(_td, "_add_artifact", _ORIG_ADD_ARTIFACT)
    monkeypatch.setattr(_td, "_add_artifact_fn", lambda sid, data: rec.append((sid, dict(data))))
    prev = _lwd.execute

    def fake(args, workspace):
        if args.get("action") == "download":
            p = env.ws / "uploads" / Path(str(args.get("path", "x.txt")).replace("\\", "/")).name
            p.write_text("dl")
            env.node_calls.append(dict(args))
            return {"ok": True, "alias": args.get("alias"), "action": "download", "path": str(p)}
        return prev(args, workspace)
    monkeypatch.setattr(_lwd, "execute", fake)
    return rec


def test_operator_artifacts_registered_to_parent(env, arts, monkeypatch):
    monkeypatch.setattr(op, "MAX_SHOTS", 2)        # 默认 6; 调小以免脚本长到触发子代理上下文压缩
    t = _t("截图下载登记")
    shot = ("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"})
    env.scripts[t] = ([shot] * 4 + [
        ("tool", "windows_system", {"alias": "vm-a", "action": "download", "path": "C:/r/报表.xlsx"}),
        ("tool", "windows_system", {"alias": "vm-a", "action": "download", "path": "C:/r/报表.xlsx"}),   # 同一文件再取一次
        ("tool", "windows_system", {"alias": "vm-a", "action": "download", "path": "C:/r/log.txt"}),
        ("tool", "windows_system", {"alias": "vm-a", "action": "inspect_file", "path": "C:/r/log.txt"}),
        ("text", "下载完成并已核对")])
    out = _spawn({"agent_type": "operator", "alias": "vm-a", "task": t}, sid="parent-sess")
    parent = [r for s, r in arts if s == "parent-sess"]
    shots = [r for r in parent if r["op"] == "screenshot"]
    dls = [r for r in parent if r["op"] == "download"]
    assert len([c for c in env.node_calls if c.get("action") == "screenshot"]) == 4
    # 截图只登记最后 MAX_SHOTS 张 (按时间顺序), 下载全部登记且按路径去重
    assert len(shots) == 2, parent
    produced = sorted((p for p in (env.ws / "uploads").glob("shot_vm-a_*.png")),
                      key=lambda p: int(p.stem.rsplit("_", 1)[1]))
    assert [Path(r["filepath"]).name for r in shots] == [p.name for p in produced[-2:]]
    assert sorted(Path(r["filepath"]).name for r in dls) == ["log.txt", "报表.xlsx"]
    assert len({r["filepath"] for r in parent}) == len(parent)                  # 去重
    assert all(r["source"] == "operator" and r["alias"] == "vm-a" and r["subagent"].startswith("subagent_operator_")
               for r in parent)
    assert "[产出物已登记到主会话: 截图 2 张 (最近 2 张), 下载/文件 2 个]" in out, out
    assert "- download: " in out and "报表.xlsx" in out
    assert not op._ARTS
    assert op.MAX_SHOTS == 2 and len(op.select_artifacts(
        [{"filepath": f"/{i}", "op": "screenshot"} for i in range(9)], max_shots=6)) == 6


def test_operator_artifacts_registered_on_timeout_and_no_parent(env, arts):
    t = _t("超时也登记")
    env.scripts[t] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"}), ("text", "不会到这")]

    async def hang():
        await asyncio.sleep(5)
    env.hooks[(t, 2)] = hang                                    # 截图之后的第 2 轮 LLM 调用卡住

    async def run():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sa._run_subagent(f"{t}\n\n{op.marker('vm-a')}", "operator",
                                                    parent_sid="p2"), timeout=1.0)
    asyncio.run(run())
    p2 = [r for s, r in arts if s == "p2"]
    assert len(p2) == 1 and p2[0]["op"] == "screenshot" and not op._ARTS
    # 没有父会话 (DAG 运行时等): 不登记, 结果不加说明
    t2 = _t("无父会话")
    env.scripts[t2] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "screenshot"}), ("text", "看完")]
    n = len(arts)
    r = asyncio.run(sa._run_subagent(f"{t2}\n\n{op.marker('vm-a')}", "operator"))
    assert r == "看完"
    assert not [x for x in arts[n:] if x[1].get("source") == "operator"] and not op._ARTS


def test_select_artifacts_unit():
    recs = [{"filepath": f"/s{i}.png", "op": "screenshot"} for i in range(10)]
    recs += [{"filepath": "/d.txt", "op": "download"}, {"filepath": "/s9.png", "op": "screenshot"},
             {"filepath": "/d.txt", "op": "download"}]
    got = op.select_artifacts(recs)
    assert [r["filepath"] for r in got] == ["/s4.png", "/s5.png", "/s6.png", "/s7.png", "/s8.png", "/s9.png", "/d.txt"]
    assert op.select_artifacts([]) == []
    op.note_artifact("not-running", {"filepath": "/x", "op": "screenshot"})   # 非运行中的操作员 sid: 忽略
    assert "not-running" not in op._ARTS


# ── 生产实测回归 (2026-10-18 WPS 验收): 任务点名 op_wps_test.xlsx, 操作员被桌面旧文件带偏,
#    做了界面操作 + 截图 + 回读了别的文件 (销售表.xlsx), 却被判 [已验证]。现在点名文件必须在最后一次改动后被确认。 ──
def test_evidence_task_named_file_must_be_confirmed():
    task = "用 WPS 表格新建表格, A4 输入 =SUM(A1:A3), 另存为 桌面\\op_wps_test.xlsx, 最后 inspect_file 回读"
    assert ev.task_targets(task) == ["op_wps_test.xlsx"]

    def col():
        c = ev.start_collector("operator", task)
        assert c is not None and c.op_targets == ["op_wps_test.xlsx"]
        return c
    ok = '{"ok": true}'
    c = col()                                                     # 复现: 回读的是别的文件 → 未验证
    c.observe("windows_desktop", {"action": "click", "x": 1, "y": 1}, ok)
    c.observe("windows_desktop", {"action": "screenshot"}, ok)
    c.observe("windows_system", {"action": "inspect_file", "path": "D:\\LiteCode归档\\销售表.xlsx"}, ok)
    v = c.verdict()
    assert not v[0] and "op_wps_test.xlsx" in v[1], v
    c = col()                                                     # 点名文件读失败 (不存在) 不算确认
    c.observe("windows_desktop", {"action": "hotkey", "keys": "ctrl+s"}, ok)
    c.observe("windows_system", {"action": "inspect_file", "path": "C:\\Users\\u\\Desktop\\op_wps_test.xlsx"},
              "ERROR: windows_system RuntimeError: 读取远端文件失败: 文件不存在")
    assert not c.verdict()[0]
    c.observe("windows_system", {"action": "inspect_file", "path": "C:\\Users\\u\\Desktop\\op_wps_test.xlsx"},
              '{"ok": true, "cells": {"A4": 49.75}}')
    assert c.verdict()[0], c.verdict()
    c.observe("windows_desktop", {"action": "hotkey", "keys": "ctrl+s"}, ok)   # 之后又保存 → 需重新确认
    c.observe("windows_system", {"action": "inspect_file", "path": "C:\\other.xlsx"}, ok)
    assert not c.verdict()[0] and "op_wps_test.xlsx" in c.verdict()[1]
    c.observe("windows_system", {"action": "list_dir", "path": "C:\\Users\\u\\Desktop"},
              '{"ok": true, "entries": [{"name": "op_wps_test.xlsx", "size": 9238}]}')
    assert c.verdict()[0]
    c = ev.start_collector("operator", "在桌面新建op_test.txt 写入 hi")   # 中文紧贴文件名
    c.observe("windows_desktop", {"action": "hotkey", "keys": "ctrl+s"}, ok)
    c.observe("windows_system", {"action": "inspect_file", "path": "C:/Users/u/Desktop/myop_test.txt"}, ok)
    assert not c.verdict()[0]                                     # 仅后缀相同的别的文件不算
    c.observe("windows_system", {"action": "inspect_file", "path": "C:/Users/u/Desktop/op_test.txt"}, ok)
    assert c.verdict()[0]
    c = ev.start_collector("operator", "截图看看桌面, 打开 www.example.com")  # 没点名文件 → 旧规则
    c.observe("windows_desktop", {"action": "click", "x": 1, "y": 1}, ok)
    c.observe("windows_desktop", {"action": "screenshot"}, ok)
    assert c.op_targets == [] and c.verdict()[0]
    c = ev.start_collector("coder", "改 a.txt")                    # 非操作员不受影响
    assert c is not None and c.op_targets == []


def test_evidence_named_file_end_to_end(env):
    t = _t("另存为 桌面\\op_e2e.xlsx")
    env.scripts[t] = [("tool", "windows_desktop", {"alias": "vm-a", "action": "hotkey", "keys": "ctrl+s"}),
                      ("tool", "windows_system", {"alias": "vm-a", "action": "inspect_file", "path": "C:/旧/销售表.xlsx"}),
                      ("text", "已保存并回读, 合计 49.75"), ("text", "确实完成")]
    out = _spawn({"agent_type": "operator", "alias": "vm-a", "task": t})
    assert out.startswith("[未验证: 任务点名的文件 op_e2e.xlsx"), out
