#!/usr/bin/env python3
"""test_full 离线单测分片: v1.0 核心模块 (_offline_v10_core); 由 test_full.py --offline 调度, 无 pytest 用例。"""

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


def _offline_v10_core() -> list:
    """离线单测 #1-20: blackboard / orchestrator / memory_index 等核心模块。"""
    results = []
    _test = _make_tester(results)

    # ── 1. blackboard.py ──
    def test_blackboard():
        sys.path.insert(0, str(BASE))
        from blackboard import Blackboard, EntryType, BLACKBOARD_TOOL_DEF
        bb = Blackboard()
        bb.put("k1", "v1", EntryType.RESULT, source="test")
        bb.put_file("/src/main.py", "entry", source="coder")
        bb.put_error("e1", "timeout", "DNS fail", source="net")
        assert len(bb) == 3, f"Expected 3, got {len(bb)}"
        assert bb.get("k1") == "v1"
        assert len(bb.query_by_type(EntryType.RESULT)) == 1
        ctx = bb.to_context_string()
        assert "Blackboard" in ctx
        snap = bb.snapshot()
        bb2 = Blackboard.from_dict(snap["entries"])
        assert len(bb2) == 3
        assert "update_global_context" in BLACKBOARD_TOOL_DEF["function"]["name"]
    _test("blackboard: CRUD + snapshot + restore", test_blackboard)

    # ── 2. orchestrator.py ──
    def test_orchestrator():
        import asyncio
        sys.path.insert(0, str(BASE))
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec, StepStatus
        async def mock(t, a, c, s, parent_sid=None): return f"ok:{a}"
        orch = DAGOrchestrator(mock)
        steps = [
            StepSpec(id="a", task="A"),
            StepSpec(id="b", task="B", depends_on=["a"]),
            StepSpec(id="c", task="C", depends_on=["a"]),
            StepSpec(id="d", task="D", depends_on=["b", "c"]),
        ]
        layers = orch._topological_sort(steps)
        assert len(layers) == 3, f"Expected 3 layers, got {len(layers)}"
        assert layers[0][0].id == "a"
        assert set(s.id for s in layers[1]) == {"b", "c"}
        plan = DAGPlan(steps=steps, auto_critic=False)
        result = asyncio.run(orch.execute(plan))
        assert result.success, "DAG execution failed"
        assert len(result.steps) == 4
    _test("orchestrator: topo sort + DAG execute (a→b‖c→d)", test_orchestrator)

    # ── 3. memory_index.py ──
    def test_memory_index():
        import tempfile, os
        sys.path.insert(0, str(BASE))
        from memory_index import MemoryIndex
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            dbp = f.name
        try:
            idx = MemoryIndex(dbp)
            idx.index_memory("s1", "error", "pip install PEP668", solution="--break-system-packages")
            idx.index_error_solution("s1", "ModuleNotFoundError", "missing requests", "pip3 install requests")
            idx.index_decision("s1", "Use FastAPI", "async native")
            r = idx.search("pip install")
            assert len(r) > 0, "Search returned no results"
            ctx = idx.search_as_context("pip install")
            assert "历史经验" in ctx, f"Context missing header: {ctx[:100]}"
            stats = idx.stats()
            assert stats["total"] == 3, f"Expected 3, got {stats['total']}"
            idx.close()
        finally:
            os.unlink(dbp)
    _test("memory_index: index + FTS5 search + context export", test_memory_index)

    # ── 4. itrace.py ──
    def test_itrace():
        import tempfile, os
        sys.path.insert(0, str(BASE))
        from itrace import Tracer, NullTracer, make_tracer, SpanContext
        # SpanContext
        sc = SpanContext()
        assert sc.depth == 0
        sc.push_span()
        assert sc.depth == 1
        sc.push_span()
        assert sc.depth == 2
        sc.pop_span()
        assert sc.depth == 1
        sc.pop_span()
        assert sc.depth == 0
        # NullTracer
        nt = NullTracer()
        nt.dag_step_begin("s1", "t", "c", "task")
        nt.critic_event("start")
        assert nt.get_dashboard_data() == {}
        # Full tracer
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            lp = f.name
        try:
            t = make_tracer({"enabled": True, "save_path": lp})
            t.start("test")
            t.iteration_begin(0, [{"role": "user", "content": "x"}])
            t.dag_step_begin("s1", "Code", "coder", "write")
            t.tool_call("write_file", {"filepath": "t.py"}, "Written", 0.5)
            t.dag_step_end("s1", "Code", "success", 2.0)
            t.critic_event("end", issues=["bug"])
            t.blackboard_write("k", "t", "s")
            dash = t.get_dashboard_data()
            assert dash["iterations"] == 1
            assert "write_file" in dash["tool_calls"]
            assert len(dash["dag_steps"]) == 1
            t.finish("done")
            log = open(lp).read()
            assert "trace_id" in log
            assert "DAG_STEP_START" in log
            assert "CRITIC_END" in log
        finally:
            os.unlink(lp)
    _test("itrace: SpanContext + tree trace + dashboard data", test_itrace)

    # ── 5. multi_agent.py ──
    def test_multi_agent():
        sys.path.insert(0, str(BASE))
        from multi_agent import AgentMode, AgentSpec, MultiAgentOrchestrator, OrchestratorTemplates
        # Backward compat
        assert AgentMode.PIPELINE == "pipeline"
        assert AgentMode.PARALLEL == "parallel"
        assert AgentMode.COMPETITIVE == "competitive"
        assert AgentMode.DAG == "dag"
        # New fields have defaults
        spec = AgentSpec(task="test", agent_type="coder")
        assert spec.depends_on == []
        assert spec.max_retries == 2
        assert spec.critical == True
        assert spec.step_id == ""
        # Templates
        mode, specs = OrchestratorTemplates.code_with_tests_dag("hello", "Python")
        assert mode == AgentMode.DAG
        assert specs[1].depends_on == ["code"]
    _test("multi_agent: backward compat + DAG mode + templates", test_multi_agent)

    # ── 6. 原有模块不受影响 ──
    def test_original_modules():
        sys.path.insert(0, str(BASE))
        from memory import MemoryManager, estimate_tokens
        from executor_v4 import classify_error, infer_timeout
        assert classify_error("", 0) == "ok"
        assert classify_error("Name or service not known", 1) == "permanent"
        assert infer_timeout("pip install x") == 300
        assert infer_timeout("curl http://x") == 60
        tokens = estimate_tokens([{"role": "user", "content": "hello world"}])
        assert tokens > 0
    _test("original modules: memory + executor_v4 unchanged", test_original_modules)

    # ── 7. SKILL.md 存在性检查 (含 v1.1 新增 18 个) ──
    def test_skills_exist():
        sys.path.insert(0, str(BASE))
        skills_dir = BASE / "skills"
        required = [
            "browser-automation/SKILL.md",
            "longtext-processing/SKILL.md",
            "outline-generation/SKILL.md",
            # v1.1 新增: 15 题材 + 1 深度报告 + 1 AI味自审 + 1 PDF OCR
            "novel-xuanhuan/SKILL.md",  "novel-xiuzhen/SKILL.md",
            "novel-wuxia/SKILL.md",     "novel-urban/SKILL.md",
            "novel-guiyi/SKILL.md",     "novel-scifi/SKILL.md",
            "novel-yanqing/SKILL.md",   "novel-lishi/SKILL.md",
            "novel-moshi/SKILL.md",     "novel-dianjing/SKILL.md",
            "novel-zhongtian/SKILL.md", "novel-xitongliu/SKILL.md",
            "novel-wuxianliu/SKILL.md", "novel-chuanyue/SKILL.md",
            "novel-zhongsheng/SKILL.md",
            # v1.1.1 新增: 架空历史/宫斗/悬疑/军事/轻小说/西幻 + 通用规则
            "novel-lishi-jiakong/SKILL.md", "novel-gongdou/SKILL.md",
            "novel-xuanyi/SKILL.md",        "novel-junshi/SKILL.md",
            "novel-qingxiao/SKILL.md",      "novel-xifan/SKILL.md",
            "novel-common/SKILL.md",
            "deep-report/SKILL.md",
            "anti-ai-tell-audit/SKILL.md",
            "pdf-ocr-pipeline/SKILL.md",
        ]
        for rel in required:
            p = skills_dir / rel
            assert p.exists(), f"Missing: {p}"
            content = p.read_text()
            assert len(content) > 200, f"Skill too short: {rel} ({len(content)} chars)"
            assert "---" in content[:100], f"Missing frontmatter: {rel}"
    _test("skills: all v1.1 SKILL.md files exist + valid", test_skills_exist)

    # ── 8. browser-automation 新 action 定义 ──
    def test_browser_actions():
        sys.path.insert(0, str(BASE))
        server_py = (BASE / "skills" / "browser-automation" / "browser_server.py").read_text()
        new_actions = ["deep_inspect", "find_element", "check_state", "wait_dom_stable",
                       "smart_wait", "extract_table", "extract_form", "fill_form", "intercept"]
        for action in new_actions:
            assert action in server_py, f"Missing action: {action}"
    _test("browser: all 9 new actions defined in server", test_browser_actions)

    # ── 9. thinking_adapter (v1.0) ──
    def test_thinking_adapter():
        sys.path.insert(0, str(BASE))
        from lib.thinking_adapter import ThinkingConfig, inject_params, inject_for_compress, extract_content, init, get

        # vLLM 后端
        init({"backend_type": "vllm", "enable_thinking": True, "thinking_budget": 4096})
        cfg = get()
        assert cfg.backend_type == "vllm"
        assert cfg.enabled == True
        assert cfg.budget == 4096

        # inject_params: vLLM + enabled
        payload = {"max_tokens": 8000, "temperature": 0.6}
        inject_params(payload)
        assert payload["chat_template_kwargs"]["enable_thinking"] == True
        assert payload["max_tokens"] == 8000 + 4096, f"Expected {8000+4096}, got {payload['max_tokens']}"
        assert "temperature" not in payload

        # inject_params: force_disable
        payload2 = {"max_tokens": 8000}
        inject_params(payload2, force_disable=True)
        assert payload2["chat_template_kwargs"]["enable_thinking"] == False

        # OpenAI 后端: 不注入 thinking
        init({"backend_type": "openai", "enable_thinking": True})
        payload3 = {"max_tokens": 8000, "temperature": 0.6}
        inject_params(payload3)
        assert "chat_template_kwargs" not in payload3
        assert "thinking" not in payload3

        # Ollama 后端
        init({"backend_type": "ollama", "enable_thinking": True})
        payload4 = {"max_tokens": 8000}
        inject_params(payload4)
        assert payload4.get("think") == True

        # extract_content
        init({"backend_type": "vllm"})
        assert extract_content({"content": "hello"}) == "hello"
        init({"backend_type": "ollama"})
        assert extract_content({"content": "<think>reasoning</think>answer"}) == "answer"

    _test("thinking_adapter: vLLM/OpenAI/Ollama inject + extract", test_thinking_adapter)

    # ── 10. flow_control (v1.0) ──
    def test_flow_control():
        sys.path.insert(0, str(BASE))
        from flow_control import FlowController

        fc = FlowController(loop_threshold=3, batch_threshold=2, sw_interval=5, sw_keep_recent=2)

        # LOOP_BREAK: 连续3次同工具触发
        assert fc.check_loop_break("web_search") is None
        assert fc.check_loop_break("web_search") is None
        hint = fc.check_loop_break("web_search")
        assert hint is not None and "LOOP_BREAK" in hint

        # 切换工具后重置
        assert fc.check_loop_break("write_file") is None

        # BATCH_REMIND
        fc.reset()
        assert fc.check_batch_remind("write_file") is None
        hint2 = fc.check_batch_remind("write_file")
        assert hint2 is not None and "BATCH" in hint2

        # 非 write_file 重置
        assert fc.check_batch_remind("execute_shell") is None

        # SLIDING_WINDOW
        msgs = [{"role": "tool", "content": "x" * 500} for _ in range(10)]
        trimmed = fc.sliding_window(msgs, 5)
        assert trimmed > 0
        assert msgs[0]["content"].startswith("[cleared:")

    _test("flow_control: LOOP_BREAK + BATCH + SLIDING_WINDOW", test_flow_control)

    # ── 11. tool_dispatch 独立性 (v1.0) ──
    def test_tool_dispatch_importable():
        sys.path.insert(0, str(BASE))
        from tool_dispatch import execute_tool, _bg_procs, _read_cache, init as td_init
        # 验证核心函数可导入
        assert callable(execute_tool)
        assert isinstance(_bg_procs, dict)
        assert isinstance(_read_cache, dict)
        # 验证 init 不报错
        td_init(ds=None, add_artifact_fn=None, search_cfg={})

    _test("tool_dispatch: importable + init", test_tool_dispatch_importable)

    # ══════════════════════════════════════════════════════════════
    # v1.0 FIX OFFLINE TESTS
    # ══════════════════════════════════════════════════════════════

    # ── 12. config._LIVE 共享机制 (v1.0) ──
    def test_config_live():
        sys.path.insert(0, str(BASE))
        from lib.config import _LIVE, reload_model, MODEL_ID, API_KEY
        # _LIVE 应该存在且包含所有必要 key
        required_keys = ["model_id", "backend_url", "api_key", "max_tokens",
                         "enable_thinking", "context_window", "backend_type"]
        for k in required_keys:
            assert k in _LIVE, f"_LIVE 缺少 key: {k}"
        # 初始值应该和模块级变量一致
        assert _LIVE["model_id"] == MODEL_ID, f"_LIVE mismatch: {_LIVE['model_id']} != {MODEL_ID}"
        # reload_model 应该更新 _LIVE
        old = dict(_LIVE)
        reload_model({"id": "test-v123", "backend_url": "http://test:9999/v1",
                       "api_key": "test-key-123"})
        assert _LIVE["model_id"] == "test-v123", f"reload_model 没更新 _LIVE"
        assert _LIVE["api_key"] == "test-key-123"
        # _LIVE 是同一个 dict 对象（by reference，不是 copy）
        from lib.config import _LIVE as live2
        assert live2 is _LIVE, "_LIVE 不是同一个引用！transport.py 拿不到更新"
        # 恢复
        reload_model({"id": old["model_id"], "backend_url": old["backend_url"],
                       "api_key": old["api_key"]})
    _test("[v1.0] config._LIVE: 共享 + reload_model + by-reference", test_config_live)

    # ── 13. subagent 工具注入接口 (v1.0) ──
    def test_subagent_injection():
        sys.path.insert(0, str(BASE))
        from lib.agent.subagent import (set_execute_tool, set_parse_text_tool_calls,
                                         execute_tool as et, _parse_text_tool_calls as ptc)
        # 函数应该存在
        assert callable(set_execute_tool)
        assert callable(set_parse_text_tool_calls)
        # 注入后应该生效
        async def _fake_exec(n, a): return ("FAKE_OK", None)
        def _fake_parse(t): return [{"name": "test", "arguments": "{}"}]
        set_execute_tool(_fake_exec)
        set_parse_text_tool_calls(_fake_parse)
        from lib.agent.subagent import execute_tool as et2, _parse_text_tool_calls as ptc2
        assert et2 is _fake_exec, "set_execute_tool 没生效"
        assert ptc2 is _fake_parse, "set_parse_text_tool_calls 没生效"
        # 恢复
        set_execute_tool(et)
        set_parse_text_tool_calls(ptc)
    _test("[v1.0] subagent: set_execute_tool + set_parse_text_tool_calls", test_subagent_injection)

    # ── 14. memory smart_auto_memory 降级 (v1.0) ──
    def test_memory_fallback():
        sys.path.insert(0, str(BASE))
        from memory import smart_auto_memory, rule_based_memory_update

        class FakeMgr:
            def __init__(self):
                self.session_id = "test-v123-mem"
                self._store = {}
            def save(self, section, content):
                self._store.setdefault(section, []).append(content)
            def load(self, section=None):
                if section:
                    return "\n".join(self._store.get(section, []))
                return str(self._store)

        mgr = FakeMgr()
        msgs = [
            {"role": "user", "content": "我叫张三，Python 工程师，做数据分析"},
            {"role": "assistant", "content": "好的张三，有什么需要帮忙的？"},
        ]
        # 用不可达地址触发 LLM 超时 → 验证 rule_based 结果不丢弃
        result = smart_auto_memory(
            vllm_url="http://192.0.2.1:1",  # RFC 5737 不可路由地址
            model_id="fake", api_key="fake",
            messages=msgs, manager=mgr,
        )
        # 旧版 bug: LLM 超时后 return False，丢弃 rule_based 结果
        # 修复后: return rule_updated (True if rule_based saved something)
        # rule_based 应该至少提取了一些信息
        assert result is not False or len(mgr._store) > 0, \
            f"smart_auto_memory 超时后丢弃了 rule_based 结果! result={result}, store={mgr._store}"
    _test("[v1.0] memory: LLM超时 → rule_based结果不丢弃", test_memory_fallback)

    # ── 15. AGENT_PROMPT.md TAV 关键词检查 (v1.0) ──
    def test_prompt_tav():
        prompt_file = BASE / "AGENT_PROMPT.md"
        assert prompt_file.exists(), f"AGENT_PROMPT.md 不存在: {prompt_file}"
        content = prompt_file.read_text()
        # 必须包含 Think→Act→Verify 关键结构
        assert "Think" in content, "AGENT_PROMPT 缺少 Think 段"
        assert "Act" in content, "AGENT_PROMPT 缺少 Act 段"
        assert "Verify" in content, "AGENT_PROMPT 缺少 Verify 段"
        assert "{{DATETIME}}" in content, "AGENT_PROMPT 缺少 {{DATETIME}} 占位符"
        assert "file-reading" in content, "AGENT_PROMPT 缺少 file-reading 技能引用"
        assert "币安" in content or "binance" in content.lower(), "AGENT_PROMPT 缺少币安优先策略"
    _test("[v1.0] AGENT_PROMPT.md: TAV + DATETIME + 策略关键词", test_prompt_tav)

    # ══════════════════════════════════════════════════════════════
    # v1.0 FIX OFFLINE TESTS — 真实用户反馈的问题
    # ══════════════════════════════════════════════════════════════

    # ── 16. 默认模型配置内部一致 (v1.0; test-full 基准) ──
    def test_default_model():
        # [断言过期] "qwen3.6 免费 Ollama 默认模型" 这套配置早在 d5d96fd (精简模型
        # 列表, 保留 gether-DeepSeek + Qwen3.6-35B) + 248d21c (切默认模型到
        # Qwen3.5-35B) 等提交里就被移除了, config.json 现在完全没有任何一条
        # backend_type=ollama 的 qwen3.6 条目 (models[] 是 deepseek-v4-pro/flash
        # + vllm-qwen3-30b-remote + Qwen3.6-35B[vLLM 私有部署] + kimi/glm/
        # deepseek-flash/qwen3.7-max 8 条 gateway/vllm/official 模型)。硬编码这条
        # 老配置只反映"今天手动切到了哪个模型", 不反映真实设计意图。
        # 改成校验"激活模型必须是 models[] 目录里真实存在、且字段自洽的一条"。
        cfg = _load_cfg()
        active = cfg.get("model", {})
        models = cfg.get("models", [])
        assert isinstance(models, list) and len(models) >= 1, "models 目录为空"
        catalog = {m.get("id"): m for m in models}
        assert active.get("id") in catalog, \
            f"激活模型 {active.get('id')!r} 不在 models 目录里 (孤儿配置)"
        entry = catalog[active.get("id")]
        assert active.get("backend_url") == entry.get("backend_url"), \
            "激活模型 backend_url 跟 models 目录里的条目不一致"
        assert active.get("context_window", 0) > 0, "context_window 必须 > 0"
        assert active.get("backend_type") in ("ollama", "vllm", "openai", "anthropic", "deepseek"), \
            f"未知 backend_type: {active.get('backend_type')}"
    _test("[v1.0] 默认模型: 激活配置与 models 目录自洽", test_default_model)

    # ── 17. models 数组包含 4 个模型 (v1.0) ──
    def test_models_registry():
        cfg = _load_cfg()
        models = cfg.get("models", [])
        ids = [m.get("id") for m in models]
        assert "Qwen3.5-35B" in ids, "缺少默认模型 Qwen3.5-35B"
        assert "glm-5" in ids, "缺少 glm-5"
        assert "Qwen3.5-122B" in ids, "缺少 Qwen3.5-122B"
        assert "qwen3.5:35b" in ids, "缺少 Ollama qwen3.5:35b"
        assert len(models) >= 4, f"应有至少 4 个模型, 实际: {len(models)}"
        # Ollama 模型必须有正确配置
        ollama = [m for m in models if m.get("backend_type") == "ollama"][0]
        assert "your-model-host.example" in ollama.get("backend_url", ""), "Ollama URL 错误"
    _test("[v1.0] models 数组: 4 个模型 + Ollama 配置正确", test_models_registry)

    # ── 18. 自动触发 spawn_agent: 数据获取+PDF (v1.0) ──
    def test_auto_delegation_data_pdf():
        sys.path.insert(0, str(BASE))
        from lib.prompt import _detect_auto_delegation
        # 触发场景
        msg = "写一个获取过去7天BTC价格的脚本, 然后生成 PDF 报告"
        result = _detect_auto_delegation(msg)
        assert "spawn_agent" in result, \
            f"'数据+PDF' 任务未触发 spawn_agent 自动委派, result:\n{result[:300]}"
        assert "coder" in result, "触发信息应提到 agent_type='coder'"
        # 不触发场景 (用户已说 spawn_agent)
        msg2 = "用 spawn_agent 获取BTC价格然后输出 PDF"
        result2 = _detect_auto_delegation(msg2)
        # 用户明说了, 不需要再 MANDATORY 注入 (检查没 'data+PDF' mandatory)
        assert "数据获取 + 文档输出" not in result2, \
            "用户已说 spawn_agent 时不应重复注入"
    _test("[v1.0] 自动委派: '数据+PDF'隐式触发 spawn_agent", test_auto_delegation_data_pdf)

    # ── 19. entrypoint.sh inject_env 占位符保护逻辑 (v1.0) ──
    def test_entrypoint_placeholder_protection():
        # 模拟 inject_env 的核心逻辑
        _placeholders = ('', 'EMPTY', 'sk-your-api-key-here',
                         'your-api-key', 'changeme', 'REPLACE_ME')
        def _is_placeholder(v):
            return not v or v in _placeholders

        # 占位符应被识别
        assert _is_placeholder('') is True
        assert _is_placeholder('EMPTY') is True
        assert _is_placeholder('sk-your-api-key-here') is True
        assert _is_placeholder(None) is True
        # 真实值不是占位符
        assert _is_placeholder('sk-EXAMPLE0000000000000000') is False
        assert _is_placeholder('woyaofacai@your-model-host.example2026') is False
        assert _is_placeholder('http://your-llm-host.example:20000/v1') is False
    _test("[v1.0] entrypoint: 占位符识别逻辑", test_entrypoint_placeholder_protection)

    # ── 20. subagent 每轮进度日志 (v1.0) ──
    def test_subagent_iter_log():
        sa_file = BASE / "lib/agent/subagent.py"
        content = sa_file.read_text()
        assert "subagent:{agent_type}:iter=" in content, \
            "subagent 缺少每轮进度日志, 会被误认为'卡死'"
        assert "start" in content  # iter 开始日志
    _test("[v1.0] subagent: 每轮进度日志防'卡死'误报", test_subagent_iter_log)

    return results
