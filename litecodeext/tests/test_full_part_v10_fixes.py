#!/usr/bin/env python3
"""test_full 离线单测分片: v1.0 修复回归 (_offline_v10_fixes); 由 test_full.py --offline 调度, 无 pytest 用例。"""

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


def _offline_v10_fixes() -> list:
    """离线单测 #21-43: v1.0/v1.1 修复类回归 + skill 存在性检查。"""
    import importlib
    results = []
    _test = _make_tester(results)

    # ══════════════════════════════════════════════════════════════
    # v1.0 FIX OFFLINE TESTS
    # ══════════════════════════════════════════════════════════════

    # ── 21. 推理字段 3 后端兼容 (v1.0) ──
    def test_reasoning_multi_backend():
        # 行为断言: 三种后端字段都能取出推理文本 (原为源码字符串匹配)
        sys.path.insert(0, str(BASE))
        from lib.agent.llm_call import delta_reasoning
        assert delta_reasoning({"reasoning_content": "a"}) == "a", "缺 vLLM 旧版 / Ollama v1 reasoning_content"
        assert delta_reasoning({"reasoning": "b"}) == "b", "缺 vLLM 新版 reasoning"
        assert delta_reasoning({"thinking": "c"}) == "c", "缺 Ollama native thinking"
        assert delta_reasoning({"content": "答案"}) == ""
        assert delta_reasoning({"reasoning_content": None, "reasoning": "b"}) == "b"
    _test("[v1.0] reasoning 字段 3 后端兼容 (vLLM旧/新/Ollama)", test_reasoning_multi_backend)

    # ── 22. 服务器 memory 后台异步化 (v1.0 修复微信延迟，v1.5 拆为 3 worker) ──
    def test_memory_bg_thread():
        # 行为断言: 记忆更新再慢 (这里每个 worker 卡 2s), 回合收尾也必须立即返回,
        # 三个 worker (compress/fast/smart) 都在后台线程里跑到。原为源码字符串匹配。
        import threading
        from types import SimpleNamespace
        sys.path.insert(0, str(BASE))
        from lib.agent import turn_persist as tp
        gate, ran = threading.Event(), []

        class SlowMgr:
            def check_and_compact(self, msgs, _force_async=False):
                ran.append("compress"); gate.wait(2)

            def on_task_end(self, msgs):
                ran.append("fast"); gate.wait(2)

        def slow_smart(*a):
            ran.append("smart"); gate.wait(2)

        patched = {"HAS_MEMORY": True, "_save_history": lambda *a: None,
                   "_mem_get": lambda **k: SlowMgr(), "_smart_auto_memory": slow_smart,
                   "_auto_update_user": lambda **k: None, "_auto_update_tools": lambda **k: None}
        saved = {k: getattr(tp, k) for k in patched}
        try:
            for k, v in patched.items():
                setattr(tp, k, v)
            ctx = SimpleNamespace(session_id="t-membg", sid_tag="t-membg",
                                  base_history=[], new_msgs=[{"role": "user", "content": "hi"}])
            t0 = time.time()
            tp._turn_persist(ctx, SimpleNamespace(), SimpleNamespace(project_root=None), SimpleNamespace())
            elapsed = time.time() - t0
            assert elapsed < 1.0, f"回合收尾被记忆更新阻塞 {elapsed:.1f}s — 微信回复延迟 bug 回归"
            deadline = time.time() + 2
            while len(ran) < 3 and time.time() < deadline:
                time.sleep(0.02)
            assert sorted(ran) == ["compress", "fast", "smart"], f"后台 worker 未全部启动: {ran}"
        finally:
            gate.set()
            for k, v in saved.items():
                setattr(tp, k, v)
    _test("[v1.0] memory 异步化: 修复微信延迟 82 秒（v1.5 拆 3 worker）", test_memory_bg_thread)

    # ── 23. subagent 防御性 <think> 剥离 (v1.0) ──
    def test_subagent_think_strip():
        sa_file = BASE / "lib/agent/subagent.py"
        content = sa_file.read_text()
        assert "<think>.*?</think>" in content, \
            "subagent 缺少防御性 <think> 剥离"
    _test("[v1.0] subagent: 防御性剥离 <think> 标签", test_subagent_think_strip)

    # ── 24. AGENT_PROMPT 不再建议字面 [THINK] (v1.0) ──
    def test_prompt_no_literal_think():
        pm_file = BASE / "AGENT_PROMPT.md"
        content = pm_file.read_text()
        # 应该明确告诉模型不要输出 [THINK] 字面标签
        assert "不要" in content and "[THINK]" in content, \
            "AGENT_PROMPT 应明确禁止输出字面 [THINK] 标签"
        # [PLAN] 保留用于复杂任务规划
        assert "[PLAN]" in content
    _test("[v1.0] AGENT_PROMPT: 禁止字面 [THINK] 泄漏", test_prompt_no_literal_think)

    # ── 25. Web UI 推理折叠显示 (v1.0) ──
    def test_webui_reasoning():
        # [v1.0] Web UI 已拆分为 web_ui.html + web_assets/*, 分别检查
        html_file = BASE / "web_ui.html"
        css_file = BASE / "web_assets" / "app.css"
        combined = html_file.read_text()
        if css_file.exists():
            combined += css_file.read_text()
        for js_file in sorted((BASE / "web_assets").glob("*.js")):
            combined += js_file.read_text()
        # CSS 应该已有
        assert ".rsb" in combined, "缺 .rsb 折叠面板 CSS"
        assert ".rsh" in combined, "缺 .rsh 折叠标题 CSS"
        # JS 应该处理 delta.reasoning
        assert "delta.reasoning" in combined, "Web UI JS 未处理 delta.reasoning"
        # 最终渲染应该保留 rsb (变量名或 class 均可)
        assert "rsbHtml" in combined or "rsb.live" in combined, "Web UI 最终渲染未保留推理块"
    _test("[v1.0] Web UI: 推理内容折叠展示", test_webui_reasoning)

    # ── 26. extract_content vLLM/Ollama 兼容 (v1.0) ──
    def test_extract_content_compat():
        sys.path.insert(0, str(BASE))
        from lib.thinking_adapter import extract_content

        # 情况1: vLLM 有 parser — content 干净
        m1 = {"content": "最终答案", "reasoning_content": "思考过程..."}
        assert extract_content(m1) == "最终答案", "vLLM 干净 content 处理错误"

        # 情况2: vLLM 无 parser — content 带 <think>
        m2 = {"content": "<think>先分析一下...然后</think>实际答案"}
        assert extract_content(m2) == "实际答案", \
            f"<think> 剥离失败: got={extract_content(m2)!r}"

        # 情况3: content 为空, 答案在 reasoning_content (极端)
        m3 = {"content": "", "reasoning_content": "答案内容"}
        assert extract_content(m3) == "答案内容", "reasoning_content fallback 失败"

        # 情况4: content 带 </think> 但无开标签 (Qwen3-Thinking-2507 情况)
        m4 = {"content": "早期思考内容</think>\n\n实际答案"}
        assert extract_content(m4) == "实际答案", \
            f"无开标签 </think> 处理错误: got={extract_content(m4)!r}"

        # 情况5: Ollama /api/chat 字段
        m5 = {"content": "", "thinking": "思考", "reasoning_content": ""}
        # content 和 reasoning_content 都空 → 用 thinking
        # 但 thinking 是思考不是答案, extract_content 会返回它（作为 fallback）
        # 这种情况通常不会发生在 /v1/ 端点
        assert extract_content(m5) == "思考"
    _test("[v1.0] extract_content: vLLM/Ollama 4 种场景兼容", test_extract_content_compat)

    # ── 27. inject_for_compress 跟随当前模型配置 (v1.2) ──
    # [v1.2-fix] 原本想一律关闭 thinking, 但部分 vLLM 部署 (Qwen3.5 + Reasoner parser)
    # 强制 enable_thinking=True, 关掉会 400。改为跟随当前激活模型配置。
    def test_inject_for_compress_follows_config():
        sys.path.insert(0, str(BASE))
        from lib.thinking_adapter import inject_for_compress, init as _init_th, get as _get_th

        # 先存当前配置以便恢复
        _cur = _get_th()
        _orig = {"backend_type": _cur.backend_type, "enable_thinking": _cur.enabled,
                 "thinking_budget": _cur.budget}

        try:
            # vLLM + 配置禁用 thinking → payload 也应该禁用
            _init_th({"backend_type": "vllm", "enable_thinking": False, "thinking_budget": 8192})
            p1 = {"max_tokens": 4096, "temperature": 0.1}
            inject_for_compress(p1)
            assert p1.get("chat_template_kwargs", {}).get("enable_thinking") is False, \
                f"vLLM 配置 disabled 时 compress 应禁用 thinking, 实际: {p1}"

            # [2026-08-30 行为变更] vLLM + 配置启用 thinking → compress 仍**强制关闭**。
            # 旧行为是"跟随激活模型配置"(顾虑老 Qwen3.5 部署关了会 400), 实测该顾虑
            # 对当前部署不成立, 而跟随配置会造成**记忆压缩全面失效**:
            #   压缩 max_tokens 仅 4096, 开思考时模型把预算全烧在思考上
            #   (finish_reason=length), content 产出 0 字符 → extract_content 回退
            #   把"思考文本"当答案 → 思考是自由散文无 ## 分节 → 质检 FAIL → 整份记忆丢弃。
            # 真实复现 (wx-xxxx 会话, 18798 字符 prompt):
            #   开思考: 思考 13298c / content 0c / tokens=4096 / finish=length → FAIL
            #   关思考: 思考     0c / content 4624c / tokens=1317 / finish=stop  → OK
            # 该会话连续 4 次压缩失败, 记忆退化到只剩 444 字节。见 commit 0691c8a。
            _init_th({"backend_type": "vllm", "enable_thinking": True, "thinking_budget": 8192})
            p1b = {"max_tokens": 4096, "temperature": 0.1}
            inject_for_compress(p1b)
            assert p1b.get("chat_template_kwargs", {}).get("enable_thinking") is False, \
                f"compress 必须强制关 thinking (防思考吃光 4096 预算致 content 为空), 实际: {p1b}"

            # Ollama + 配置禁用 → payload think=False
            _init_th({"backend_type": "ollama", "enable_thinking": False, "thinking_budget": 8192})
            p2 = {"max_tokens": 4096}
            inject_for_compress(p2)
            assert p2.get("think") is False, f"Ollama 配置 disabled 时 compress 应禁用 think, 实际: {p2}"

            # OpenAI 场景 (GLM-5) - 不应报错, payload 不变
            _init_th({"backend_type": "openai", "enable_thinking": False, "thinking_budget": 8192})
            p3 = {"max_tokens": 4096, "temperature": 0.1}
            inject_for_compress(p3)
            # openai 不支持 thinking 参数, 不应添加也不应报错
            assert "chat_template_kwargs" not in p3 or p3["chat_template_kwargs"] != True
        finally:
            # 恢复原配置
            _init_th(_orig)
    _test("[v1.0] compress: thinking 强制关闭 (防吃光预算致 content 空)", test_inject_for_compress_follows_config)

    # ══════════════════════════════════════════════════════════════
    # v1.0 OFFLINE TESTS  (僵尸进程 / 自主学习 / Skill 草稿)
    # ══════════════════════════════════════════════════════════════

    # ── 28. bg_procs 按 sid 分桶 ──
    def test_bg_procs_session_bucket():
        sys.path.insert(0, str(BASE))
        # 使用专用 importer 避免污染其他测试
        if 'tool_dispatch' in sys.modules:
            importlib.reload(sys.modules['tool_dispatch'])
        import tool_dispatch as td
        td._bg_procs.clear()
        td._register_bg('11111', 'cmd-a', '/tmp/a.log', sid='sess-A', keep=False)
        td._register_bg('22222', 'cmd-b', '/tmp/b.log', sid='sess-A', keep=True)
        td._register_bg('33333', 'cmd-c', '/tmp/c.log', sid='sess-B', keep=False)
        assert 'sess-A' in td._bg_procs and len(td._bg_procs['sess-A']) == 2
        assert 'sess-B' in td._bg_procs and len(td._bg_procs['sess-B']) == 1
        # _unregister_bg 跨桶查找
        td._unregister_bg('22222')
        assert '22222' not in td._bg_procs.get('sess-A', {})
        assert '11111' in td._bg_procs.get('sess-A', {})
        # 清空 sess-B
        td._unregister_bg('33333')
        assert 'sess-B' not in td._bg_procs  # 空桶被清掉
        td._bg_procs.clear()
    _test("[v1.0] bg_procs 按 sid 分桶 + 空桶清理", test_bg_procs_session_bucket)

    # ── 29. cleanup_session_bg 跳过 keep_alive ──
    def test_cleanup_session_bg_respects_keep():
        import asyncio as _asyncio
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td._bg_procs.clear()
        td._register_bg('99001', 'temp-srv', '/tmp/x.log', sid='clean-test', keep=False)
        td._register_bg('99002', 'persistent-srv', '/tmp/y.log', sid='clean-test', keep=True)
        async def _run():
            return await td.cleanup_session_bg('clean-test', force=False)
        killed_count, killed_cmds = _asyncio.run(_run())
        # 99001/99002 是假 PID, kill 不会成功, 但 logic 应跳过 keep=True 的
        # 我们验证 keep=True 的进程仍然在 registry 里
        remaining = td.list_bg_for_session('clean-test')
        assert '99002' in remaining, f"keep=True 进程被错误清理: {remaining}"
        td._bg_procs.clear()
    _test("[v1.0] cleanup_session_bg 不杀 keep_alive 进程", test_cleanup_session_bg_respects_keep)

    # ── 30. session cleanup hook 注册 ──
    def test_session_cleanup_hook_registered():
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        # 重新 init 触发钩子注册
        td.init(ds=None, add_artifact_fn=None, search_cfg={})
        from lib.session import _cleanup_hook
        assert _cleanup_hook is not None, "tool_dispatch.init() 应当注册 session cleanup hook"
    _test("[v1.0] init 注册 session cleanup hook", test_session_cleanup_hook_registered)

    # ── 31. flow_control: 相同结果死循环检测 (新维度) ──
    def test_flow_control_same_result_detection():
        sys.path.insert(0, str(BASE))
        from flow_control import FlowController
        fc = FlowController()
        # 同结果 2 次应触发
        fc.check_loop_break("web_search", "identical-result")
        r = fc.check_loop_break("web_search", "identical-result")
        assert r and "LOOP_BREAK" in r, f"相同结果未触发: {r!r}"
        # 不同结果不应触发
        fc.reset()
        fc.check_loop_break("web_search", "result-1")
        r = fc.check_loop_break("web_search", "result-2")
        assert r is None, f"不同结果误触发: {r!r}"
    _test("[v1.0] flow_control 相同结果死循环检测", test_flow_control_same_result_detection)

    # ── 32. read_cache LRU 上限 ──
    def test_read_cache_lru():
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td._read_cache.clear()
        # 写超过上限
        for i in range(td._READ_CACHE_MAX + 50):
            td._read_cache_put(f"/tmp/file_{i}.py", (1.0, 10, 100))
        # 应该被淘汰到 ≤ _READ_CACHE_MAX
        assert len(td._read_cache) <= td._READ_CACHE_MAX, \
            f"LRU 没生效: {len(td._read_cache)} > {td._READ_CACHE_MAX}"
        # 最早的应被淘汰
        assert "/tmp/file_0.py" not in td._read_cache
        td._read_cache.clear()
    _test("[v1.0] read_cache LRU 淘汰机制", test_read_cache_lru)

    # ── 33. subagent 铁律继承 ──
    def test_subagent_iron_rules():
        sys.path.insert(0, str(BASE))
        from lib.agent.subagent import _SUBAGENT_CONFIGS, _SUBAGENT_IRON_RULES
        assert "改已有文件前必须先 read_file" in _SUBAGENT_IRON_RULES
        assert "写完代码必须运行一次验证" in _SUBAGENT_IRON_RULES
        # coder/analyst/tester max_iter 已提高
        assert _SUBAGENT_CONFIGS["coder"].get("max_iter", 12) >= 24
        assert _SUBAGENT_CONFIGS["analyst"].get("max_iter", 12) >= 24
        assert _SUBAGENT_CONFIGS["tester"].get("max_iter", 12) >= 18
    _test("[v1.0] subagent 铁律 + max_iter 提升", test_subagent_iron_rules)

    # ── 34. skill 使用统计 ──
    def test_skill_usage_tracking():
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        # 写一条记录
        td._record_skill_usage("test-skill-tracker")
        td._record_skill_usage("test-skill-tracker")
        summary = td.get_skill_usage_summary()
        assert "test-skill-tracker" in summary
        assert "loaded=" in summary  # 计数列存在
    _test("[v1.0] skill 使用统计可读写", test_skill_usage_tracking)

    # ── 35. 新工具 schema 都已注册 ──
    def test_new_tools_in_schema():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = {t["function"]["name"] for t in TOOL_DEFS}
        for must_have in ("list_bg", "kill_bg", "self_reflect", "create_skill"):
            assert must_have in names, f"工具 {must_have} 未注册到 TOOL_DEFS"
        # execute_shell 应该有 keep_alive 参数
        ex = next(t for t in TOOL_DEFS if t["function"]["name"] == "execute_shell")
        assert "keep_alive" in ex["function"]["parameters"]["properties"]
    _test("[v1.0] 新工具已注册到 TOOL_DEFS", test_new_tools_in_schema)

    # ── 36. create_skill 默认写到 drafts/ ──
    def test_create_skill_drafts():
        import tempfile, asyncio as _asyncio
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        from tools.handlers import meta as _meta
        # create_skill 的实际落地逻辑在 handlers/meta.py, 它独立 import 了
        # SKILLS_DIR (跟 tool_dispatch.py 各自的模块级绑定不是同一个引用),
        # 只 patch td.SKILLS_DIR 对 meta.py 里的执行路径无效, 得 patch 两处.
        tmp = Path(tempfile.mkdtemp(prefix="skill_test_"))
        orig_td, orig_meta = td.SKILLS_DIR, _meta.SKILLS_DIR
        td.SKILLS_DIR = tmp
        _meta.SKILLS_DIR = tmp
        try:
            async def _run():
                return await td.execute_tool("create_skill", {
                    "name": "test-draft-skill",
                    "description": "测试用",
                    "content": "# test\n\n## 何时使用\n测试场景",
                }, sid="test-sid")
            result, _ = _asyncio.run(_run())
            # 应当写到 drafts/, 不是直接激活
            assert (tmp / "drafts" / "test-draft-skill" / "SKILL.md").exists()
            assert "草稿" in result or "draft" in result.lower()
        finally:
            td.SKILLS_DIR = orig_td
            _meta.SKILLS_DIR = orig_meta
            import shutil; shutil.rmtree(tmp, ignore_errors=True)
    _test("[v1.0] create_skill 默认写到 drafts/", test_create_skill_drafts)

    # ── 37. find_files 跳过 SKIP_DIRS ──
    def test_find_files_skip_dirs():
        import tempfile, asyncio as _asyncio
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        # 建临时项目: 包含 node_modules
        tmp = Path(tempfile.mkdtemp(prefix="find_test_"))
        (tmp / "src").mkdir()
        (tmp / "src" / "main.py").write_text("# real")
        (tmp / "node_modules").mkdir()
        (tmp / "node_modules" / "junk.py").write_text("# noise")
        (tmp / ".git").mkdir()
        (tmp / ".git" / "config.py").write_text("# git noise")
        async def _run():
            return await td.execute_tool("find_files", {
                "pattern": "*.py",
                "path": str(tmp),
            })
        result, _ = _asyncio.run(_run())
        assert "main.py" in result
        assert "junk.py" not in result, "find_files 没跳过 node_modules"
        assert ".git" not in result, "find_files 没跳过 .git"
        import shutil; shutil.rmtree(tmp, ignore_errors=True)
    _test("[v1.0] find_files 跳过 node_modules/.git", test_find_files_skip_dirs)

    # ── 38. self_reflect 工具可调用 (skill_usage scope) ──
    def test_self_reflect_callable():
        import asyncio as _asyncio
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        async def _run():
            return await td.execute_tool("self_reflect", {"scope": "skill_usage"}, sid="test-sid")
        result, _ = _asyncio.run(_run())
        # 应返回字符串 (要么有数据要么"无使用记录")
        assert isinstance(result, str) and len(result) > 0
    _test("[v1.0] self_reflect 工具可调用", test_self_reflect_callable)

    # ══════════════════════════════════════════════════════════════
    # v1.1 OFFLINE TESTS  (vision_ocr / writer 扩权 / critic 回炉 / skill 数量)
    # ══════════════════════════════════════════════════════════════

    # ── 39. vision_ocr tool 已注册 ──
    def test_vision_ocr_registered():
        sys.path.insert(0, str(BASE))
        from lib.tools.defs import TOOL_DEFS
        names = {t["function"]["name"] for t in TOOL_DEFS}
        assert "vision_ocr" in names, "vision_ocr 未注册到 TOOL_DEFS"
        voc = next(t for t in TOOL_DEFS if t["function"]["name"] == "vision_ocr")
        params = voc["function"]["parameters"]
        assert "path" in params["required"]
        assert "prompt" in params["properties"]
        # 实现函数存在
        import tool_dispatch as td
        assert hasattr(td, "_do_vision_ocr")
    _test("[v1.1] vision_ocr 工具注册 + 实现就位", test_vision_ocr_registered)

    # ── 40. writer 扩权 (web_search + vision_ocr + load_skill) ──
    def test_writer_expanded_permissions():
        sys.path.insert(0, str(BASE))
        from lib.agent.subagent import _SUBAGENT_CONFIGS
        writer = _SUBAGENT_CONFIGS["writer"]
        for need in ("web_search", "browser_search", "vision_ocr", "load_skill"):
            assert need in writer["allowed"], f"writer 缺 {need}"
    _test("[v1.1] writer 扩权: 搜索 + vision_ocr + 加载题材 skill", test_writer_expanded_permissions)

    # ── 41. critic 回炉环 (DAGOrchestrator._extract_critic_failed_steps) ──
    def test_critic_replay_loop():
        import asyncio as _asyncio
        sys.path.insert(0, str(BASE))
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec
        from orchestrator.execute_mixin import _derive_plan_id
        call_count = [0]
        async def mock(t, a, c, s, parent_sid=None):
            call_count[0] += 1
            if a == "critic":
                # 前几次报 CRITICAL, 第 6 次之后放行
                if call_count[0] < 6:
                    return "❌ backend 缺失 parse\n[CRITICAL: step:backend 需补 parse 函数]"
                return "✅ 全通过"
            return f"ok:{a}"
        orch = DAGOrchestrator(mock)
        plan = DAGPlan(steps=[
            StepSpec(id="design", task="设计", agent_type="analyst"),
            StepSpec(id="backend", task="实现", agent_type="coder", depends_on=["design"]),
        ], auto_critic=True)
        # [P0-#7 审计 fix 后] plan_id 从 DAG 结构 hash 派生, 同结构的 checkpoint
        # 会跨测试运行命中并跳过步骤执行 (call_count 对不上) — 先清掉保证隔离
        orch.clear_checkpoint(_derive_plan_id(plan))
        r = _asyncio.run(orch.execute(plan))
        assert r.success, "DAG 执行失败"
        # 至少 design 1 + backend 1 + critic 1 + (replay backend 1 + critic 1) = 5 次
        assert call_count[0] >= 5, f"critic 回炉未触发, 调用 {call_count[0]} 次"
    _test("[v1.1] critic [CRITICAL:] 触发 step 回炉", test_critic_replay_loop)

    # ── 42. 21 题材 skill 数量 (v1.1.1 补 6 个) ──
    def test_novel_genre_skills():
        skills_dir = BASE / "skills"
        genres = [
            # v1.1 原 15 个
            "xuanhuan","xiuzhen","wuxia","urban","guiyi","scifi","yanqing",
            "lishi","moshi","dianjing","zhongtian","xitongliu","wuxianliu",
            "chuanyue","zhongsheng",
            # v1.1.1 新增 6 个
            "lishi-jiakong","gongdou","xuanyi","junshi","qingxiao","xifan",
        ]
        missing = [g for g in genres if not (skills_dir / f"novel-{g}" / "SKILL.md").exists()]
        assert not missing, f"题材 skill 缺失: {missing}"
        # 每个 skill 必须 ≥ 800 字 (保证内容量)
        for g in genres:
            p = skills_dir / f"novel-{g}" / "SKILL.md"
            assert len(p.read_text()) > 800, f"novel-{g} 内容太少"
        # 通用 common skill
        common = skills_dir / "novel-common" / "SKILL.md"
        assert common.exists()
        assert "常见坑" in common.read_text()
        assert "边写边查" in common.read_text()
    _test("[v1.1.1] 21 题材 novel-* + novel-common skill 齐", test_novel_genre_skills)

    # ── 43. deep-report / anti-ai-tell-audit / pdf-ocr-pipeline 存在 ──
    def test_aux_skills_exist():
        skills_dir = BASE / "skills"
        for s in ("deep-report", "anti-ai-tell-audit", "pdf-ocr-pipeline"):
            p = skills_dir / s / "SKILL.md"
            assert p.exists(), f"Missing {s}"
            assert len(p.read_text()) > 500, f"{s} 内容太少"
    _test("[v1.1] deep-report / anti-ai-tell-audit / pdf-ocr-pipeline 就位", test_aux_skills_exist)

    return results
