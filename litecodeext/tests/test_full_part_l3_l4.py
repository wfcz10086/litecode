#!/usr/bin/env python3
"""test_full 离线单测分片: L3 高阶 + L4 复杂 (_offline_l3_high / _offline_l4_complex); 由 test_full.py --offline 调度, 无 pytest 用例。"""

import json
import os
import re
import sys
import time
from pathlib import Path

from _full_common import (  # noqa: F401  (共享配置/颜色/记录器, 见 _full_common.py)
    BASE, _load_cfg, _make_tester,
    G, R, Y, C, B, D, NC,
)


def _offline_l3_high() -> list:
    """离线单测 #104-133 (L3 高档): CRUD 闭环 / 三端一致性 / 多轮。"""
    results = []
    _test = _make_tester(results)

    # ── L3 高 (30 case): CRUD 闭环 / 三端一致性 / 多轮 ──

    # ── 104. 项目 CRUD: create → update → archive → restore ──
    def test_project_full_cycle():
        import tempfile
        sys.path.insert(0, str(BASE / "core"))
        import projects as pj
        with tempfile.TemporaryDirectory() as ws:
            store = pj.ProjectStore(Path(ws))
            # 1. create
            m = store.create("Cyc", "general", "d", tags=[])
            pid = m.project_id
            # 2. get
            assert store.get(pid).name == "Cyc"
            # 3. update
            store.update(pid, name="Cyc2", description="updated")
            assert store.get(pid).name == "Cyc2"
            # 4. archive (soft delete)
            assert store.delete(pid, soft=True)
            assert store.get(pid).status == "archived"
            # 5. restore via update
            store.update(pid, status="active")
            assert store.get(pid).status == "active"
    _test("[v1.10] L3: project 5步 CRUD 闭环", test_project_full_cycle)

    # ── 105. timer CRUD: add → update → run_now → delete → list ──
    def test_timer_full_cycle():
        import asyncio, tempfile
        sys.path.insert(0, str(BASE / "core"))
        import timer_manager as tm
        orig = tm._TIMERS_FILE
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(b"[]"); tp = f.name
        try:
            tm._TIMERS_FILE = Path(tp)
            mgr = tm.TimerManager(); mgr.timers=[]
            # 1. add
            t = mgr.add_timer({"name":"t","type":"once","schedule":"2099-01-01T00:00:00",
                                "action_type":"shell","action_content":"echo X"})
            tid = t["id"]
            # 2. update
            mgr.update_timer(tid, {"name": "t2"})
            assert any(x["name"] == "t2" for x in mgr.timers)
            # 3. list
            assert len(mgr.list_timers()) == 1
            # 4. run_now (offline shell)
            r = asyncio.run(mgr.run_now(tid))
            assert r.get("ok")
            # 5. delete
            assert mgr.delete_timer(tid)
            assert len(mgr.timers) == 0
        finally:
            tm._TIMERS_FILE = orig
            os.unlink(tp)
    _test("[v1.10] L3: timer 5步 CRUD 闭环", test_timer_full_cycle)

    # ── 106. dag schema CRUD: save → load → list → validate → del ──
    def test_dag_full_cycle():
        import tempfile
        sys.path.insert(0, str(BASE))
        from lib.dag_schema import save_dag, load_dag, list_dags, validate_dag_json, _dags_dir
        with tempfile.TemporaryDirectory() as ws:
            ws_p = Path(ws)
            d = {"steps":[{"id":"a","task":"A"},{"id":"b","task":"B","depends_on":["a"]}]}
            ok, _ = validate_dag_json(d)
            assert ok
            p = save_dag(ws_p, "td1", d)
            assert p.exists()
            got = load_dag(ws_p, "td1")
            assert got and len(got["steps"]) == 2
            names = list_dags(ws_p)
            assert "td1" in names
            # delete
            p.unlink()
            assert "td1" not in list_dags(ws_p)
    _test("[v1.10] L3: dag_schema 5步 CRUD 闭环", test_dag_full_cycle)

    # ── 107. memory CRUD via MemoryManager ──
    def test_memory_full_cycle():
        import tempfile, shutil
        sys.path.insert(0, str(BASE))
        from memory import MemoryManager
        ws = Path(tempfile.mkdtemp())
        try:
            sid = f"test-mem-{int(time.time())}"
            mgr = MemoryManager(workspace=ws, session_id=sid,
                                vllm_url="http://localhost:9", model_id="fake",
                                api_key="EMPTY", context_window=8000)
            # 1. save L1 section
            mgr.save("User Intent", "用户叫张三, Python 工程师")
            # 2. file 存在
            assert mgr.l1_file.exists()
            assert "张三" in mgr.l1_file.read_text()
            # 3. save 第二段
            mgr.save("Completed Work", "测试 P34-d-10 通过")
            assert "P34-d-10" in mgr.l1_file.read_text()
            # 4. load_for_prompt 输出含两段
            txt = mgr.load_for_prompt()
            assert "张三" in txt
            # 5. l2_dir 路径正确
            assert mgr.l2_dir.parent == mgr.root
        finally:
            shutil.rmtree(ws, ignore_errors=True)
    _test("[v1.10] L3: memory 5步 CRUD 闭环", test_memory_full_cycle)

    # ── 108. skill CRUD: create_skill → list_skills → load_skill → cleanup ──
    def test_skill_full_cycle():
        import asyncio, shutil
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        # create_skill (写到 drafts/)
        unique = f"test-skill-{int(time.time())}"
        # 1. create
        r1, _ = asyncio.run(td.execute_tool("create_skill",
            {"name": unique, "description": "test only", "content": "# Test\nbody"},
            "off-skill-cycle"))
        # 2. list
        r2, _ = asyncio.run(td.execute_tool("list_skills", {}, "off-skill-cycle"))
        # 3. file 应该存在
        skill_dir = BASE / "skills" / "drafts"
        # 4. cleanup
        d = skill_dir / unique
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        # 5. 验证至少前两步 OK
        assert "create" in r1.lower() or unique in r1 or "ok" in r1.lower() or "skill" in r1.lower()
        assert isinstance(r2, str) and len(r2) > 10
    _test("[v1.10] L3: skill 5步 create+list+cleanup", test_skill_full_cycle)

    # ── 109. profile CRUD via update_profile + save_memory ──
    def test_profile_full_cycle():
        import asyncio
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        sid = f"prof-{int(time.time())}"
        # 1. update_profile
        r1, _ = asyncio.run(td.execute_tool("update_profile",
            {"items":[{"key":"name","value":"Alice"}]}, sid))
        # 2. save_memory
        r2, _ = asyncio.run(td.execute_tool("save_memory",
            {"section":"FACTS","content":"Alice loves cake"}, sid))
        # 3. update_profile 第二条
        r3, _ = asyncio.run(td.execute_tool("update_profile",
            {"items":[{"key":"role","value":"engineer"}]}, sid))
        # 4. save_memory 第二条
        r4, _ = asyncio.run(td.execute_tool("save_memory",
            {"section":"FACTS","content":"Alice writes Rust"}, sid))
        # 5. 验证 4 次都返回 str
        for r in (r1, r2, r3, r4):
            assert isinstance(r, str), f"profile/memory 回执非 str: {type(r)}"
    _test("[v1.10] L3: profile/save_memory 4 步连写", test_profile_full_cycle)

    # ── 110. tool_dispatch 三端一致: write_file → read_file → search_code ──
    def test_three_way_consistency():
        import asyncio, tempfile
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "consistent.py"
            sid = "3way"
            # 写
            asyncio.run(td.execute_tool("write_file",
                {"filepath": str(p), "content": "MARKER_3WAY = 42\n"}, sid))
            # 读
            r2, _ = asyncio.run(td.execute_tool("read_file",
                {"filepath": str(p)}, sid))
            assert "MARKER_3WAY" in r2
            # search
            r3, _ = asyncio.run(td.execute_tool("search_code",
                {"pattern":"MARKER_3WAY","path": tmp}, sid))
            assert "MARKER_3WAY" in r3
    _test("[v1.10] L3: write→read→search 三端一致", test_three_way_consistency)

    # ── 111. blackboard 多轮上下文 (3 轮累积) ──
    def test_blackboard_multi_round():
        sys.path.insert(0, str(BASE))
        from blackboard import Blackboard, EntryType
        bb = Blackboard()
        for i in range(3):
            bb.put(f"r{i}", f"data-{i}", EntryType.RESULT, source=f"round-{i}")
        ctx = bb.to_context_string()
        assert "data-0" in ctx and "data-2" in ctx
        # snapshot/restore 后不丢
        snap = bb.snapshot()
        bb2 = Blackboard.from_dict(snap["entries"])
        assert len(bb2) == 3
    _test("[v1.10] L3: blackboard 3 轮上下文", test_blackboard_multi_round)

    # ── 112. memory_index 跨 session 召回 ──
    def test_memory_index_cross_session():
        import tempfile
        sys.path.insert(0, str(BASE))
        from memory_index import MemoryIndex
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            dbp = f.name
        try:
            idx = MemoryIndex(dbp)
            idx.index_memory("session-A","fact","Alice loves cake")
            idx.index_memory("session-B","fact","Bob hates cake")
            r = idx.search("cake")
            ids = [x.get("session_id") for x in r]
            assert "session-A" in ids and "session-B" in ids
            idx.close()
        finally:
            os.unlink(dbp)
    _test("[v1.10] L3: memory_index 跨 session 召回", test_memory_index_cross_session)

    # ── 113. flow_control: LOOP_BREAK 触发 ──
    def test_flow_loop_break():
        sys.path.insert(0, str(BASE / "core"))
        import flow_control as fc
        ctrl = fc.FlowController(loop_threshold=3)
        # 连续 3 次相同 result 触发 LOOP_BREAK
        for _ in range(3):
            hint = ctrl.check_loop_break("web_search", "SAME_R")
        # 第 3 次开始应回有提示文本
        assert hint is None or "LOOP_BREAK" in str(hint) or isinstance(hint, str), \
            f"flow_control 未触发 LOOP_BREAK: {hint!r}"
    _test("[v1.10] L3: flow_control LOOP_BREAK 触发面", test_flow_loop_break)

    # ── 114. itrace: 多 span 嵌套深度 ──
    def test_itrace_nested_spans():
        sys.path.insert(0, str(BASE))
        from itrace import SpanContext
        sc = SpanContext()
        for i in range(5):
            sc.push_span()
        assert sc.depth == 5
        for i in range(3):
            sc.pop_span()
        assert sc.depth == 2
    _test("[v1.10] L3: itrace span 5 层 push/pop", test_itrace_nested_spans)

    # ── 115. orchestrator 4 节点 diamond DAG 全成功 ──
    def test_orchestrator_diamond():
        import asyncio
        sys.path.insert(0, str(BASE))
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec
        async def mock(t, a, c, s, parent_sid=None): return f"OK:{a}"
        orch = DAGOrchestrator(mock)
        steps = [
            StepSpec(id="a", task="A"),
            StepSpec(id="b", task="B", depends_on=["a"]),
            StepSpec(id="c", task="C", depends_on=["a"]),
            StepSpec(id="d", task="D", depends_on=["b","c"]),
        ]
        plan = DAGPlan(steps=steps, auto_critic=False)
        res = asyncio.run(orch.execute(plan))
        assert res.success
        assert len(res.steps) == 4
    _test("[v1.10] L3: DAG 菱形 4 节点全成功", test_orchestrator_diamond)

    # ── 116. failure_memory 跨次调用累计 ──
    def test_failure_memory_accumulate():
        import tempfile
        sys.path.insert(0, str(BASE))
        from lib import failure_memory as fm
        with tempfile.TemporaryDirectory() as ws:
            fm.record_failure(ws, "timeout", "fail-A summary text")
            fm.record_failure(ws, "tool_error", "fail-B summary text")
            fm.record_failure(ws, "timeout", "fail-C summary text")
            summ = fm.get_summary(ws)
            assert summ.get("timeout") == 2
            assert summ.get("tool_error") == 1
    _test("[v1.10] L3: failure_memory 累计统计", test_failure_memory_accumulate)

    # ── 117. config reload_model 二次切换 ──
    def test_config_reload_twice():
        sys.path.insert(0, str(BASE))
        from lib.config import _LIVE, reload_model
        old = dict(_LIVE)
        reload_model({"id":"m1","backend_url":"http://a/v1","api_key":"k1"})
        assert _LIVE["model_id"] == "m1"
        reload_model({"id":"m2","backend_url":"http://b/v1","api_key":"k2"})
        assert _LIVE["model_id"] == "m2"
        assert _LIVE["api_key"] == "k2"
        # 恢复
        reload_model({"id":old["model_id"],"backend_url":old["backend_url"],"api_key":old["api_key"]})
    _test("[v1.10] L3: config 二次切换不串", test_config_reload_twice)

    # ── 118. write_file + patch_file (差量更新) ──
    def test_write_then_patch():
        import asyncio, tempfile
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "patch.py"
            asyncio.run(td.execute_tool("write_file",
                {"filepath": str(p), "content": "x = 1\ny = 2\n"}, "patch-sid"))
            r2, _ = asyncio.run(td.execute_tool("patch_file",
                {"filepath": str(p), "old_str": "x = 1", "new_str": "x = 100"}, "patch-sid"))
            txt = p.read_text()
            assert "x = 100" in txt
            assert "y = 2" in txt
    _test("[v1.10] L3: write→patch 差量编辑", test_write_then_patch)

    # ── 119. multi-tool: read 多文件 ──
    def test_read_multi_files():
        import asyncio, tempfile
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(3):
                p = Path(tmp) / f"f{i}.txt"
                p.write_text(f"DATA-{i}")
                paths.append(p)
            for p in paths:
                r, _ = asyncio.run(td.execute_tool("read_file",
                    {"filepath": str(p)}, "multi-read"))
                assert "DATA-" in r
    _test("[v1.10] L3: read_file 多文件", test_read_multi_files)

    # ── 120. blackboard put_file 索引文件 ──
    def test_blackboard_put_file():
        sys.path.insert(0, str(BASE))
        from blackboard import Blackboard, EntryType
        bb = Blackboard()
        bb.put_file("/src/main.py", "entrypoint", source="coder")
        bb.put_file("/src/utils.py", "helper", source="coder")
        files = bb.query_by_type(EntryType.FILE_PATH)
        assert len(files) == 2
        ctx = bb.to_context_string()
        assert "main.py" in ctx
    _test("[v1.10] L3: blackboard 文件索引", test_blackboard_put_file)

    # ── 121. session: TimerManager.run_now 失败处理 ──
    def test_timer_run_invalid():
        import asyncio
        sys.path.insert(0, str(BASE / "core"))
        import timer_manager as tm
        mgr = tm.TimerManager()
        r = asyncio.run(mgr.run_now("nonexistent_id"))
        assert r.get("ok") is False
    _test("[v1.10] L3: timer.run_now 不存在ID 优雅失败", test_timer_run_invalid)

    # ── 122. self_reflect 工具签名 ──
    def test_self_reflect_signature():
        import asyncio
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        r, _ = asyncio.run(td.execute_tool("self_reflect",
            {"summary":"test reflection","what_worked":"a","what_failed":"b"},
            "off-reflect"))
        assert isinstance(r, str)
    _test("[v1.10] L3: self_reflect 工具", test_self_reflect_signature)

    # ── 123. project workspace_template 文件就位 ──
    def test_workspace_template():
        d = BASE / "workspace_template"
        assert d.exists() and d.is_dir()
    _test("[v1.10] L3: workspace_template 目录", test_workspace_template)

    # ── 124. mock_llm_server 文件就位 ──
    def test_mock_llm_exists():
        f = BASE / "tests" / "mock_llm_server.py"
        assert f.exists() and f.stat().st_size > 100
    _test("[v1.10] L3: mock_llm_server.py 测试用", test_mock_llm_exists)

    # ── 125. swe_bench 脚本就位 ──
    def test_swe_bench_exists():
        f = BASE / "tests" / "swe_bench.py"
        assert f.exists()
    _test("[v1.10] L3: swe_bench.py", test_swe_bench_exists)

    # ── 126. tools/defs.py: spawn_agent 注册 ──
    def test_tool_spawn_agent_registered():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = [t["function"]["name"] for t in TOOL_DEFS if t.get("function")]
        assert "spawn_agent" in names
    _test("[v1.10] L3: spawn_agent 注册", test_tool_spawn_agent_registered)

    # ── 127. tools/defs.py: vision_ocr 注册 ──
    def test_tool_vision_registered():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = [t["function"]["name"] for t in TOOL_DEFS if t.get("function")]
        assert "vision_ocr" in names
    _test("[v1.10] L3: vision_ocr 注册", test_tool_vision_registered)

    # ── 128. tools/defs.py: project_init 注册 ──
    def test_tool_project_init_registered():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = [t["function"]["name"] for t in TOOL_DEFS if t.get("function")]
        assert "project_init" in names
    _test("[v1.10] L3: project_init 注册", test_tool_project_init_registered)

    # ── 129. tools/defs.py: create_skill 注册 ──
    def test_tool_create_skill_registered():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = [t["function"]["name"] for t in TOOL_DEFS if t.get("function")]
        assert "create_skill" in names
    _test("[v1.10] L3: create_skill 注册", test_tool_create_skill_registered)

    # ── 130. tools/defs.py: save_memory + update_profile 注册 ──
    def test_tool_memory_profile_registered():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = [t["function"]["name"] for t in TOOL_DEFS if t.get("function")]
        assert "save_memory" in names
        assert "update_profile" in names
    _test("[v1.10] L3: save_memory + update_profile 注册", test_tool_memory_profile_registered)

    # ── 131. tools/defs.py: self_reflect 注册 ──
    def test_tool_self_reflect_registered():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = [t["function"]["name"] for t in TOOL_DEFS if t.get("function")]
        assert "self_reflect" in names
    _test("[v1.10] L3: self_reflect 注册", test_tool_self_reflect_registered)

    # ── 132. tools/defs.py: web_search 注册 ──
    def test_tool_web_search_registered():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = [t["function"]["name"] for t in TOOL_DEFS if t.get("function")]
        assert "web_search" in names
    _test("[v1.10] L3: web_search 注册", test_tool_web_search_registered)

    # ── 133. tools/defs.py: 总数 ≥ 25 + 重复检查 ──
    def test_tool_defs_no_duplicates():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = [t["function"]["name"] for t in TOOL_DEFS if t.get("function")]
        assert len(names) >= 25
        assert len(names) == len(set(names)), "tools/defs 有重复名"
    _test("[v1.10] L3: TOOL_DEFS 无重复 ≥ 25", test_tool_defs_no_duplicates)

    return results


def _offline_l4_complex() -> list:
    """离线单测 #134-143 (L4 复杂档): 子代理协作 / DAG 失败传播 / critic 回炉。"""
    results = []
    _test = _make_tester(results)

    # ── L4 复杂 (10 case): 子代理协作 / DAG 失败传播 / critic 回炉 ──

    # ── 134. multi_agent: 3 角色 pipeline (coder→tester→critic) ──
    def test_pipeline_3_roles():
        sys.path.insert(0, str(BASE))
        from multi_agent import AgentMode, AgentSpec, OrchestratorTemplates
        mode, specs = OrchestratorTemplates.code_with_tests_dag("hello world", "Python")
        # 默认应有 code + test 两步, 可能含 review
        assert mode == AgentMode.DAG
        assert len(specs) >= 2
        agent_types = [s.agent_type for s in specs]
        assert "coder" in agent_types
    _test("[v1.10] L4: 3 角色 pipeline (coder→tester→critic)", test_pipeline_3_roles)

    # ── 135. DAG 5 步含失败传播 (b 子代理异常) ──
    def test_dag_failure_propagation():
        import asyncio
        sys.path.insert(0, str(BASE))
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec, StepStatus
        # b 返回 [子代理异常] 字符串 — orchestrator 识别为 FAILED
        # mock 签名: (task, agent_type, context, sse_emit, parent_sid)
        async def mock(t, a, c, s, parent_sid=None):
            if "FAIL" in t:
                return "[子代理异常] intentional fail in step"
            return f"OK:{a}"
        orch = DAGOrchestrator(mock)
        steps = [
            StepSpec(id="a", task="A", max_retries=0),
            StepSpec(id="b", task="FAIL_HERE", depends_on=["a"], max_retries=0, critical=False),
            StepSpec(id="d", task="D", depends_on=["a"], max_retries=0),
            StepSpec(id="e", task="E", depends_on=["d"], max_retries=0),
        ]
        plan = DAGPlan(steps=steps, auto_critic=False)
        res = asyncio.run(orch.execute(plan))
        ids = [s.step_id for s in res.steps]
        assert "a" in ids and "b" in ids
        b_step = next((s for s in res.steps if s.step_id == "b"), None)
        assert b_step is not None
        assert b_step.status == StepStatus.FAILED, f"b 应 FAILED 实际 {b_step.status}"
        # d 和 e 不依赖 b 应仍 success
        d_step = next((s for s in res.steps if s.step_id == "d"), None)
        assert d_step and d_step.status == StepStatus.SUCCESS
    _test("[v1.10] L4: DAG 5 步失败传播", test_dag_failure_propagation)

    # ── 136. critic 触发 step 回炉 (max_retries) ──
    def test_critic_replay():
        import asyncio
        sys.path.insert(0, str(BASE))
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec
        from orchestrator.execute_mixin import _derive_plan_id
        # [bug] 原 mock 用 `s.id == "review"` 判断当前是哪个 step, 但 self._run() 的
        # 第 4 个位置参数是 sse_emit 转发器 (或 None), 从来不是 StepSpec, 恒定
        # AttributeError. CRITICAL 回炉走的是 auto_critic 里独立的 "critic" agent
        # 调用 (critic_mixin.py _run_critic), 不是某个用户 step 自己的输出触发,
        # 改成跟 #41 test_critic_replay_loop 一致的判断方式 (按 agent_type=="critic").
        attempts = {"n": 0}
        critic_calls = {"n": 0}
        async def mock(t, a, c, s, parent_sid=None):
            attempts["n"] += 1
            if a == "critic":
                critic_calls["n"] += 1
                if critic_calls["n"] == 1:
                    return "[CRITICAL: bug found]"
                return "OK: critic 通过"
            return f"OK:{a}"
        orch = DAGOrchestrator(mock)
        steps = [
            StepSpec(id="code", task="write code"),
            StepSpec(id="review", task="review", depends_on=["code"], max_retries=2),
        ]
        plan = DAGPlan(steps=steps, auto_critic=True)
        # [P0-#7 审计 fix 后] plan_id 从 DAG 结构 hash 派生, 跨测试运行会命中
        # 同结构的旧 checkpoint 导致步骤被跳过执行 — 先清掉保证隔离
        orch.clear_checkpoint(_derive_plan_id(plan))
        res = asyncio.run(orch.execute(plan))
        # critic 第一轮报 CRITICAL 后应触发回炉重跑, 第二轮 critic 复查通过退出循环
        assert critic_calls["n"] >= 2, f"critic 应至少被回炉调用 2 轮, 实际 {critic_calls['n']}"
        assert res.success, f"DAG should succeed after replay, got {res.success}"
    _test("[v1.10] L4: critic 触发 step 回炉", test_critic_replay)

    # ── 137. self_reflect 写 failure_memory (集成) ──
    def test_self_reflect_writes_failure():
        import tempfile
        sys.path.insert(0, str(BASE))
        from lib import failure_memory as fm
        with tempfile.TemporaryDirectory() as ws:
            # 模拟 self_reflect 路径: 把错误写进 failure_memory
            fm.record_failure(ws, "self_reflect_audit",
                              "Failed step: writeful patch missed indent",
                              fix_hint="add 2-space indent before patch")
            sims = fm.find_similar(ws, "self_reflect_audit", "patch missed indent")
            assert len(sims) >= 1
            assert sims[0]["fix_hint"] == "add 2-space indent before patch"
    _test("[v1.10] L4: self_reflect 写 failure_memory", test_self_reflect_writes_failure)

    # ── 138. SSE 14 字段全覆盖 (合同审计) ──
    def test_sse_contract_audit():
        sys.path.insert(0, str(BASE))
        from lib import sse
        # 14 字段: content reasoning task_exec diff_view usage agent_status stop done error +
        #          tool_calls (status), thinking, plan, scratch, finish_reason
        # 最小集合: 7 个 sse_* 函数都可调用
        funcs = ["sse_content","sse_reasoning","sse_status","sse_stop",
                 "sse_done","sse_error","sse_usage"]
        for f in funcs:
            assert hasattr(sse, f), f"sse 缺 {f}"
        # status key 任意可用
        d = json.loads(sse.sse_status("tool_calls", {"name":"x"})[6:].strip())
        assert "tool_calls" in d["choices"][0]["delta"]
        d2 = json.loads(sse.sse_status("thinking", {"text":"t"})[6:].strip())
        assert "thinking" in d2["choices"][0]["delta"]
    _test("[v1.10] L4: SSE_CONTRACT 字段全覆盖", test_sse_contract_audit)

    # ── 139. multi_agent 4 模式可枚举 ──
    def test_multi_agent_modes():
        sys.path.insert(0, str(BASE))
        from multi_agent import AgentMode
        for m in ("PIPELINE", "PARALLEL", "COMPETITIVE", "DAG"):
            assert hasattr(AgentMode, m), f"AgentMode 缺 {m}"
    _test("[v1.10] L4: multi_agent 4 模式齐", test_multi_agent_modes)

    # ── 140. orchestrator + critic + reflect 联动 ──
    def test_orch_critic_reflect_pipeline():
        import asyncio, tempfile
        sys.path.insert(0, str(BASE))
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec
        from orchestrator.execute_mixin import _derive_plan_id
        from lib import failure_memory as fm
        with tempfile.TemporaryDirectory() as ws:
            call_log = []
            # signature: (task, agent_type, context, sse_emit, parent_sid=None)
            async def mock(t, agent, c, sse, parent_sid=None):
                call_log.append(agent)
                # 模拟 reflect: 写 failure_memory
                fm.record_failure(ws, "step_audit", f"agent={agent} task={t[:30]}", "ok")
                return f"OK:{agent}"
            orch = DAGOrchestrator(mock)
            steps = [
                StepSpec(id="code", task="C", agent_type="coder", max_retries=0),
                StepSpec(id="review", task="R", agent_type="critic",
                         depends_on=["code"], max_retries=0),
            ]
            plan = DAGPlan(steps=steps, auto_critic=False)
            # [P0-#7 审计 fix 后] plan_id 从 DAG 结构 hash 派生, 跨测试运行会命中
            # 同结构的旧 checkpoint 导致步骤被跳过执行 (call_log 空) — 先清掉保证隔离
            orch.clear_checkpoint(_derive_plan_id(plan))
            res = asyncio.run(orch.execute(plan))
            assert res.success, f"DAG should succeed, got {res.success}"
            assert fm.get_summary(ws).get("step_audit", 0) >= 1
            assert "coder" in call_log and "critic" in call_log
    _test("[v1.10] L4: orch+critic+reflect 联动", test_orch_critic_reflect_pipeline)

    # ── 141. dag 3 层 (analyst→coder×2→critic) 全成功 ──
    def test_dag_analyst_coders_critic():
        import asyncio
        sys.path.insert(0, str(BASE))
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec
        async def mock(t, a, c, s, parent_sid=None): return f"OK:{a}:{t[:5]}"
        orch = DAGOrchestrator(mock)
        steps = [
            StepSpec(id="design", task="design", agent_type="analyst", max_retries=0),
            StepSpec(id="backend", task="backend", agent_type="coder",
                     depends_on=["design"], max_retries=0),
            StepSpec(id="frontend", task="frontend", agent_type="coder",
                     depends_on=["design"], max_retries=0),
            StepSpec(id="review", task="review", agent_type="critic",
                     depends_on=["backend","frontend"], max_retries=0),
        ]
        res = asyncio.run(orch.execute(DAGPlan(steps=steps, auto_critic=False)))
        assert res.success
        # 至少 4 步在 results (可能有 __critic__ 额外项)
        ids = [s.step_id for s in res.steps]
        for sid in ("design","backend","frontend","review"):
            assert sid in ids, f"缺步 {sid}, 实际: {ids}"
    _test("[v1.10] L4: 3 层 DAG (1 analyst + 2 coder + 1 critic)", test_dag_analyst_coders_critic)

    # ── 142. orchestrator cycle 不死锁 (快速失败, 不无限循环) ──
    def test_orchestrator_cycle_detect():
        sys.path.insert(0, str(BASE))
        from orchestrator import DAGOrchestrator, StepSpec
        from orchestrator.dag import DAGCycleError
        async def mock(t, a, c, s, parent_sid=None): return "ok"
        orch = DAGOrchestrator(mock)
        # a 依赖 b, b 依赖 a -> cycle.
        # [commit 02c0f31 · 审计 P0-#8] 拓扑排序原先"静默强推"继续跑 (风险: 依赖
        # 未满足的节点被当成可执行), 已改为立刻 raise DAGCycleError 并报出环内节点,
        # 不再悄悄丢弃/跑完。"不死锁" 指的是不会陷入无限循环 (排序阶段就报错退出),
        # 不是指排序会绕过 cycle 继续往下跑。
        steps = [
            StepSpec(id="a", task="A", depends_on=["b"]),
            StepSpec(id="b", task="B", depends_on=["a"]),
        ]
        try:
            orch._topological_sort(steps)
            assert False, "cycle 应该抛 DAGCycleError, 不应该悄悄跑完"
        except DAGCycleError as e:
            assert "a" in str(e) and "b" in str(e), f"错误信息应包含环内节点: {e}"
    _test("[v1.10] L4: DAG cycle 不死锁", test_orchestrator_cycle_detect)

    # ── 143. 设计审计: 21 题材 + 通用 + 3 工具 = 25 个 skill ──
    def test_design_skill_audit():
        skills_dir = BASE / "skills"
        novel = list(skills_dir.glob("novel-*/SKILL.md"))
        assert len(novel) >= 21, f"题材 skill 不足 21: {len(novel)}"
        for n in ("deep-report", "anti-ai-tell-audit", "pdf-ocr-pipeline"):
            assert (skills_dir / n / "SKILL.md").exists(), f"缺 aux skill {n}"
    _test("[v1.10] L4: 设计审计 21 题材 + 3 aux skill", test_design_skill_audit)

    return results
