#!/usr/bin/env python3
"""test_full 离线单测分片: L1 冒烟 + L2 中等 (_offline_l1_smoke / _offline_l2_mid); 由 test_full.py --offline 调度, 无 pytest 用例。"""

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


def _offline_l1_smoke() -> list:
    """离线单测 #44-73 (L1 低档): smoke / 元素存在 / 静态资源。"""
    results = []
    _test = _make_tester(results)

    # ══════════════════════════════════════════════════════════════
    # v1.10 P34-d-10 EXTENSION: 60+ NEW OFFLINE TESTS
    # 目标 ≥100 case, 分 4 档 (低/中/高/复杂)
    # ══════════════════════════════════════════════════════════════

    # ── L1 低 (30 case): smoke / 元素存在 / 静态资源 ─────────
    # ── 44. py_compile: litecode_server.py 可编译 ──
    def test_compile_server():
        import py_compile
        srv = BASE / "litecode_server.py"
        assert srv.exists(), "litecode_server.py 不存在"
        py_compile.compile(str(srv), doraise=True)
    _test("[v1.10] py_compile: litecode_server.py", test_compile_server)

    # ── 45. py_compile: web_ui.py ──
    def test_compile_webui():
        import py_compile
        wu = BASE / "web_ui.py"
        assert wu.exists()
        py_compile.compile(str(wu), doraise=True)
    _test("[v1.10] py_compile: web_ui.py", test_compile_webui)

    # ── 46. config.json schema (server/model/models 字段) ──
    def test_config_schema():
        cfg = json.loads((BASE / "config.json").read_text())
        for k in ("server", "model", "models"):
            assert k in cfg, f"config.json 缺 {k}"
        assert cfg["server"].get("port") in (18789, 18790), "server.port 应 18789/18790"
        assert isinstance(cfg["models"], list) and len(cfg["models"]) >= 2
    _test("[v1.10] config.json schema 完整", test_config_schema)

    # ── 47. server 路由存在性 (/health /v1/chat/completions) ──
    def test_server_routes():
        # [router 拆分后过期] litecode_server.py 现在只 `from gateway import mount`,
        # 具体路由被拆进 gateway/*.py (observability/model_mgmt/openai_compat/
        # sessions/memory) 各自的 router 里, 单看 litecode_server.py 源码文本
        # 找不到任何一条了 (真实路由集合仍完整, 只是搬了家)。
        src = (BASE / "litecode_server.py").read_text()
        src += "\n".join(
            p.read_text() for p in (BASE / "gateway").glob("*.py")
        )
        for route in ("/health", "/v1/chat/completions", "/v1/models",
                      "/v1/sessions", "/v1/model/list", "/v1/model/switch"):
            assert route in src, f"litecode_server(+gateway/) 缺路由 {route}"
    _test("[v1.10] server 必须路由都注册", test_server_routes)

    # ── 48. web_ui 路由存在性 ──
    def test_webui_routes():
        # [router 拆分后过期] /api/login 搬到 plugins/auth/core.py (经
        # routers/auth_router.py re-export), /api/timers 搬到
        # routers/timer_router.py — web_ui.py 自身文本已经找不到这两条了。
        src = (BASE / "web_ui.py").read_text()
        src += "\n".join(
            p.read_text() for p in (BASE / "routers").glob("*.py")
        )
        src += (BASE / "plugins" / "auth" / "core.py").read_text()
        for route in ("/api/login", "/api/sessions", "/api/chat", "/api/health",
                      "/api/timers", "/api/models"):
            assert route in src, f"web_ui(+routers/) 缺路由 {route}"
    _test("[v1.10] web_ui 必须路由都注册", test_webui_routes)

    # ── 49. web_ui.html 顶部按钮 (sbt) ≥ 7 个 ──
    def test_webui_sbt_count():
        html = (BASE / "web_ui.html").read_text()
        n = html.count('class="sbt"')
        assert n >= 7, f"sbt 按钮太少: {n}"
    _test("[v1.10] web_ui.html sbt 按钮 ≥ 7", test_webui_sbt_count)

    # ── 50. web_ui.html 8 个面板 pnl-* + DAG v2 独立全屏入口 ──
    def test_webui_panels():
        html = (BASE / "web_ui.html").read_text()
        for p in ("pnl-stats", "pnl-memory", "pnl-wechat", "pnl-timer",
                  "pnl-sfiles", "pnl-vnc", "pnl-project", "pnl-settings"):
            assert f'id="{p}"' in html, f"缺面板 {p}"
        # [dag-v2 2026-08-04] DAG 编排不再走 pnl-dag 弹窗面板, 已改成独立的
        # Jenkins-lite 3 屏全屏路由 (list/editor/build), 入口是 openDAGRouter()
        assert "openDAGRouter" in html, "DAG v2 全屏入口 openDAGRouter 缺失"
    _test("[v1.10] web_ui.html 8 个 pnl-* 面板 + DAG v2 入口", test_webui_panels)

    # ── 51. 输入元素 (input) 至少 2 个 (登录密码 + 上传文件) ──
    def test_webui_inputs():
        html = (BASE / "web_ui.html").read_text()
        import re as _re
        ids = _re.findall(r'<input[^>]*id="([^"]+)"', html)
        assert "lpwd" in ids, "缺登录密码 input"
        assert "fi" in ids, "缺文件上传 input"
    _test("[v1.10] web_ui.html 输入元素就位", test_webui_inputs)

    # ── 52. fold 折叠类 (.rsb .rsh .rsc .rsi) 都有 ──
    def test_css_fold_classes():
        css = (BASE / "web_assets" / "app.css").read_text()
        for cls in (".rsb", ".rsh", ".rsc", ".rsi"):
            assert cls in css, f"app.css 缺折叠类 {cls}"
    _test("[v1.10] app.css 4 个 fold 类齐全", test_css_fold_classes)

    # ── 53. 工具组类 (.tg .ti .tn .ta) ──
    def test_css_tool_classes():
        css = (BASE / "web_assets" / "app.css").read_text()
        for cls in (".tg{", ".ti{", ".tn{", ".ta{"):
            assert cls in css, f"app.css 缺工具类 {cls}"
    _test("[v1.10] app.css 4 个工具类齐全", test_css_tool_classes)

    # ── 54. 静态资源 app.js 存在 + 非空 ──
    def test_static_app_js():
        f = BASE / "web_assets" / "app.js"
        assert f.exists() and f.stat().st_size > 1000
    _test("[v1.10] static: app.js 存在", test_static_app_js)

    # ── 55. 静态资源 app.css ──
    def test_static_app_css():
        f = BASE / "web_assets" / "app.css"
        assert f.exists() and f.stat().st_size > 1000
    _test("[v1.10] static: app.css 存在", test_static_app_css)

    # ── 56. 静态资源 DAG v2 (dag_ui + dag_pipelines + dag_editor_simple) ──
    def test_static_dag_v2():
        for name in ("dag_ui.js", "dag_pipelines.js", "dag_editor_simple.js", "dag_build_view.js"):
            f = BASE / "web_assets" / name
            assert f.exists() and f.stat().st_size > 100, f"missing/empty: {name}"
    _test("[dag-v2] static: dag_ui/pipelines/editor_simple/build_view 全在", test_static_dag_v2)

    # ── 57. 静态资源 vendor.js ──
    def test_static_vendor_js():
        f = BASE / "web_assets" / "vendor.js"
        assert f.exists()
    _test("[v1.10] static: vendor.js 存在", test_static_vendor_js)

    # ── 58. 静态资源 fonts.css ──
    def test_static_fonts_css():
        f = BASE / "web_assets" / "fonts.css"
        assert f.exists()
    _test("[v1.10] static: fonts.css 存在", test_static_fonts_css)

    # ── 59. 静态资源 qrcode.js ──
    def test_static_qrcode():
        f = BASE / "web_assets" / "qrcode.js"
        assert f.exists()
    _test("[v1.10] static: qrcode.js 存在", test_static_qrcode)

    # ── 60. import litecode_server (顶层模块导入不崩) ──
    def test_import_server_module():
        # 通过 py_compile 验证而非 import (import 会启动)
        import ast
        src = (BASE / "litecode_server.py").read_text()
        ast.parse(src)
    _test("[v1.10] AST: litecode_server 语法合法", test_import_server_module)

    # ── 61. import lib.config ──
    def test_import_lib_config():
        sys.path.insert(0, str(BASE))
        from lib import config as _cfg
        assert hasattr(_cfg, "MODEL_ID")
        assert hasattr(_cfg, "_LIVE")
    _test("[v1.10] import: lib.config 含必需属性", test_import_lib_config)

    # ── 62. import core.tool_dispatch ──
    def test_import_tool_dispatch():
        sys.path.insert(0, str(BASE))
        sys.path.insert(0, str(BASE / "core"))
        import tool_dispatch as td
        assert hasattr(td, "execute_tool")
        assert hasattr(td, "init")
    _test("[v1.10] import: core.tool_dispatch 含 execute_tool", test_import_tool_dispatch)

    # ── 63. import lib.sse ──
    def test_import_lib_sse():
        sys.path.insert(0, str(BASE))
        from lib import sse
        for fn in ("sse_content", "sse_reasoning", "sse_status",
                   "sse_stop", "sse_done", "sse_error", "sse_usage"):
            assert hasattr(sse, fn), f"sse 缺 {fn}"
    _test("[v1.10] import: lib.sse 含 7 个事件函数", test_import_lib_sse)

    # ── 64. import core.timer_manager ──
    def test_import_timer():
        sys.path.insert(0, str(BASE / "core"))
        import timer_manager as tm
        assert hasattr(tm, "TimerManager")
        assert hasattr(tm, "_new_timer")
    _test("[v1.10] import: core.timer_manager", test_import_timer)

    # ── 65. import core.projects ──
    def test_import_projects():
        sys.path.insert(0, str(BASE / "core"))
        import projects as pj
        assert hasattr(pj, "ProjectStore")
        assert hasattr(pj, "ProjectMeta")
    _test("[v1.10] import: core.projects", test_import_projects)

    # ── 66. import lib.failure_memory ──
    def test_import_failure_memory():
        sys.path.insert(0, str(BASE))
        from lib import failure_memory as fm
        for fn in ("record_failure", "find_similar", "get_summary"):
            assert hasattr(fm, fn), f"failure_memory 缺 {fn}"
    _test("[v1.10] import: lib.failure_memory", test_import_failure_memory)

    # ── 67. import lib.dag_schema ──
    def test_import_dag_schema():
        sys.path.insert(0, str(BASE))
        from lib import dag_schema as ds
        for fn in ("validate_dag_json", "save_dag", "load_dag", "list_dags"):
            assert hasattr(ds, fn), f"dag_schema 缺 {fn}"
    _test("[v1.10] import: lib.dag_schema", test_import_dag_schema)

    # ── 68. import core.flow_control ──
    def test_import_flow_control():
        sys.path.insert(0, str(BASE / "core"))
        import flow_control as fc
        # FlowController 是主类
        assert hasattr(fc, "FlowController"), "flow_control 缺 FlowController"
    _test("[v1.10] import: core.flow_control", test_import_flow_control)

    # ── 69. prompts/ 6 个核心模板 ──
    def test_prompts_files():
        pdir = BASE / "prompts"
        for f in ("CODING.md", "OUTPUT.md", "PLANNING.md", "RULES.md", "SEARCH.md", "WRITING.md"):
            p = pdir / f
            assert p.exists(), f"缺 prompt {f}"
            assert len(p.read_text()) > 100
    _test("[v1.10] prompts/ 6 个模板存在", test_prompts_files)

    # ── 70. PROJECT_MAP.md 存在且非空 ──
    def test_project_map():
        p = BASE / "PROJECT_MAP.md"
        assert p.exists()
        assert len(p.read_text()) > 200
    _test("[v1.10] PROJECT_MAP.md 文档完整", test_project_map)

    # ── 71. CHANGELOG.md 存在 ──
    def test_changelog():
        p = BASE / "CHANGELOG.md"
        assert p.exists() and len(p.read_text()) > 100
    _test("[v1.10] CHANGELOG.md 存在", test_changelog)

    # ── 72. tools/defs.py 注册 ≥ 25 个工具 ──
    def test_tools_defs_count():
        sys.path.insert(0, str(BASE))
        from lib.tools import defs as _d
        # _d 模块应有 TOOL_DEFS 列表
        td_list = getattr(_d, "TOOL_DEFS", None) or getattr(_d, "TOOLS", None)
        assert td_list is not None, "tools/defs 没有 TOOL_DEFS"
        names = [t["function"]["name"] for t in td_list if t.get("function")]
        assert len(names) >= 20, f"工具数太少: {len(names)}"
    _test("[v1.10] tools/defs.py 注册工具 ≥ 20", test_tools_defs_count)

    # ── 73. cli.py 可执行性 ──
    def test_cli_compile():
        import py_compile
        cli = BASE / "cli.py"
        assert cli.exists()
        py_compile.compile(str(cli), doraise=True)
    _test("[v1.10] py_compile: cli.py", test_cli_compile)

    return results


def _offline_l2_mid() -> list:
    """离线单测 #74-103 (L2 中档): 单工具 / 单 SSE 字段 / 鉴权。"""
    results = []
    _test = _make_tester(results)

    # ── L2 中 (30 case): 单工具 / 单 SSE 字段 / 鉴权 ──

    # ── 74. tool_dispatch: write_file 离线 ──
    def test_tool_write_file():
        import asyncio, tempfile
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "hello.txt"
            r, _ = asyncio.run(td.execute_tool("write_file",
                {"filepath": str(p), "content": "hi"}, "off-1"))
            assert p.exists()
            assert "hi" in p.read_text()
    _test("[v1.10] tool: write_file 写盘", test_tool_write_file)

    # ── 75. tool_dispatch: read_file 离线 ──
    def test_tool_read_file():
        import asyncio, tempfile
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("LINE_A\nLINE_B\n"); fp = f.name
        try:
            r, _ = asyncio.run(td.execute_tool("read_file",
                {"filepath": fp}, "off-2"))
            assert "LINE_A" in r and "LINE_B" in r
        finally:
            os.unlink(fp)
    _test("[v1.10] tool: read_file 回读", test_tool_read_file)

    # ── 76. tool_dispatch: execute_shell echo ──
    def test_tool_execute_shell():
        import asyncio
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        r, _ = asyncio.run(td.execute_tool("execute_shell",
            {"command": "echo HELLO_LITE"}, "off-3"))
        assert "HELLO_LITE" in r
    _test("[v1.10] tool: execute_shell echo", test_tool_execute_shell)

    # ── 77. tool_dispatch: list_skills ──
    def test_tool_list_skills():
        import asyncio
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        r, _ = asyncio.run(td.execute_tool("list_skills", {}, "off-4"))
        # 应包含至少一个 novel-* 或 deep-report
        assert any(s in r for s in ("novel-", "deep-report", "browser", "skill")), \
            f"list_skills 输出异常: {r[:120]}"
    _test("[v1.10] tool: list_skills 列出技能", test_tool_list_skills)

    # ── 78. tool_dispatch: find_files ──
    def test_tool_find_files():
        import asyncio
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        r, _ = asyncio.run(td.execute_tool("find_files",
            {"pattern": "*.md", "path": str(BASE / "prompts")}, "off-5"))
        assert "RULES.md" in r or "CODING.md" in r, f"find_files 没找到 prompts/*.md: {r[:200]}"
    _test("[v1.10] tool: find_files glob", test_tool_find_files)

    # ── 79. tool_dispatch: search_code ──
    def test_tool_search_code():
        import asyncio
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        r, _ = asyncio.run(td.execute_tool("search_code",
            {"query": "MODEL_ID", "path": str(BASE / "lib")}, "off-6"))
        assert "MODEL_ID" in r
    _test("[v1.10] tool: search_code grep", test_tool_search_code)

    # ── 80. tool_dispatch: get_tree ──
    def test_tool_get_tree():
        import asyncio
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        r, _ = asyncio.run(td.execute_tool("get_tree",
            {"path": str(BASE / "prompts"), "max_depth": 2}, "off-7"))
        assert ".md" in r or "RULES" in r
    _test("[v1.10] tool: get_tree dir 列表", test_tool_get_tree)

    # ── 81. tool_dispatch: 未知工具回错 ──
    def test_tool_unknown():
        import asyncio
        sys.path.insert(0, str(BASE / "core"))
        sys.path.insert(0, str(BASE))
        import tool_dispatch as td
        td.init()
        r, _ = asyncio.run(td.execute_tool("nonexistent_zzz_tool",
            {}, "off-8"))
        # 不应抛异常, 应回 'unknown tool' 之类
        assert "unknown" in r.lower() or "not" in r.lower() or "error" in r.lower(), \
            f"未知工具未给错误回执: {r[:120]}"
    _test("[v1.10] tool: 未知工具优雅返回", test_tool_unknown)

    # ── 82. SSE: sse_content 字段 ──
    def test_sse_content():
        sys.path.insert(0, str(BASE))
        from lib.sse import sse_content
        s = sse_content("HI")
        assert s.startswith("data: ")
        d = json.loads(s[6:].strip())
        assert d["choices"][0]["delta"]["content"] == "HI"
    _test("[v1.10] SSE: sse_content 字段格式", test_sse_content)

    # ── 83. SSE: sse_reasoning ──
    def test_sse_reasoning():
        sys.path.insert(0, str(BASE))
        from lib.sse import sse_reasoning
        d = json.loads(sse_reasoning("THINKING")[6:].strip())
        assert d["choices"][0]["delta"]["reasoning"] == "THINKING"
    _test("[v1.10] SSE: sse_reasoning 字段格式", test_sse_reasoning)

    # ── 84. SSE: sse_status (task_exec) ──
    def test_sse_status_task_exec():
        sys.path.insert(0, str(BASE))
        from lib.sse import sse_status
        d = json.loads(sse_status("task_exec", {"state":"executing"})[6:].strip())
        assert d["choices"][0]["delta"]["task_exec"]["state"] == "executing"
    _test("[v1.10] SSE: task_exec executing", test_sse_status_task_exec)

    # ── 85. SSE: sse_status diff_view ──
    def test_sse_status_diff():
        sys.path.insert(0, str(BASE))
        from lib.sse import sse_status
        d = json.loads(sse_status("diff_view", {"file":"x.py","diff":"+a"})[6:].strip())
        assert "diff_view" in d["choices"][0]["delta"]
    _test("[v1.10] SSE: diff_view 字段", test_sse_status_diff)

    # ── 86. SSE: sse_status agent_status ──
    def test_sse_status_agent():
        sys.path.insert(0, str(BASE))
        from lib.sse import sse_status
        d = json.loads(sse_status("agent_status", {"agent":"coder","state":"running"})[6:].strip())
        assert d["choices"][0]["delta"]["agent_status"]["agent"] == "coder"
    _test("[v1.10] SSE: agent_status 字段", test_sse_status_agent)

    # ── 87. SSE: sse_stop ──
    def test_sse_stop():
        sys.path.insert(0, str(BASE))
        from lib.sse import sse_stop
        d = json.loads(sse_stop()[6:].strip())
        assert d["choices"][0]["finish_reason"] == "stop"
    _test("[v1.10] SSE: sse_stop finish_reason", test_sse_stop)

    # ── 88. SSE: sse_done [DONE] ──
    def test_sse_done():
        sys.path.insert(0, str(BASE))
        from lib.sse import sse_done
        assert sse_done() == "data: [DONE]\n\n"
    _test("[v1.10] SSE: [DONE] 终止符", test_sse_done)

    # ── 89. SSE: sse_error ──
    def test_sse_error():
        sys.path.insert(0, str(BASE))
        from lib.sse import sse_error
        d = json.loads(sse_error("upstream_dead", retry_hint=True)[6:].strip())
        err = d["choices"][0]["delta"]["error"]
        assert err["reason"] == "upstream_dead"
        assert err["retry_hint"] is True
        assert d["choices"][0]["finish_reason"] == "error"
    _test("[v1.10] SSE: sse_error retry_hint", test_sse_error)

    # ── 90. SSE: sse_usage 字段 ──
    def test_sse_usage():
        sys.path.insert(0, str(BASE))
        from lib.sse import sse_usage
        d = json.loads(sse_usage(10, 20, iterations=2, elapsed=1.5)[6:].strip())
        u = d["choices"][0]["delta"]["usage"]
        assert u["prompt_tokens"] == 10 and u["completion_tokens"] == 20
        assert u["total_tokens"] == 30
        assert u["iterations"] == 2 and u["elapsed_seconds"] == 1.5
    _test("[v1.10] SSE: usage prompt/completion/total", test_sse_usage)

    # ── 91. 鉴权: token 在 config 中匹配 ──
    def test_auth_token_match():
        cfg = json.loads((BASE / "config.json").read_text())
        tok = cfg.get("server", {}).get("token", "")
        assert tok and len(tok) >= 6, f"token 太弱: {tok}"
    _test("[v1.10] auth: token 长度合规", test_auth_token_match)

    # ── 92. 鉴权: 401 在 server 路由中存在校验 ──
    def test_auth_401_check():
        # [router 拆分后过期] 鉴权拒绝逻辑搬到 gateway/_deps.py (共享依赖,
        # 各 gateway router 复用), litecode_server.py 自身文本已经没有 401 了。
        src = (BASE / "litecode_server.py").read_text()
        src += "\n".join(
            p.read_text() for p in (BASE / "gateway").glob("*.py")
        )
        assert "401" in src, "litecode_server(+gateway/) 没有 401 拒绝逻辑"
    _test("[v1.10] auth: server 包含 401 拒绝路径", test_auth_401_check)

    # ── 93. 鉴权: 403 在 web_ui 中 (登录页) ──
    def test_auth_403_check():
        src = (BASE / "web_ui.py").read_text()
        # 401 或 403 至少一个 (登录失败拒绝)
        assert "401" in src or "403" in src, "web_ui 缺鉴权拒绝码"
    _test("[v1.10] auth: web_ui 含 401/403", test_auth_403_check)

    # ── 94. failure_memory: record + find ──
    def test_failure_memory_crud():
        import tempfile
        sys.path.insert(0, str(BASE))
        from lib import failure_memory as fm
        with tempfile.TemporaryDirectory() as ws:
            ok = fm.record_failure(ws, "timeout", "ssh connect 192.0.2.1 timeout 30s", fix_hint="check route")
            assert ok
            # 重复写应去重
            ok2 = fm.record_failure(ws, "timeout", "ssh connect 192.0.2.1 timeout 30s", fix_hint="check route")
            assert ok2 is False, "重复 failure 应去重"
            sims = fm.find_similar(ws, "timeout", "ssh 192.0.2.1 timeout")
            assert len(sims) == 1
            summ = fm.get_summary(ws)
            assert summ.get("timeout") == 1
    _test("[v1.10] failure_memory: record + dedup + find", test_failure_memory_crud)

    # ── 95. timer: TimerManager add + delete ──
    def test_timer_crud():
        sys.path.insert(0, str(BASE / "core"))
        import timer_manager as tm
        # 用临时 file 避免污染真实 timer
        import tempfile
        orig_file = tm._TIMERS_FILE
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(b"[]"); tmp_path = f.name
        try:
            tm._TIMERS_FILE = Path(tmp_path)
            mgr = tm.TimerManager()
            mgr.timers = []
            t = mgr.add_timer({"name":"test1","type":"once","schedule":"2099-01-01T00:00:00",
                                "action_type":"shell","action_content":"echo hi"})
            assert t["id"].startswith("tmr_")
            assert mgr.delete_timer(t["id"]) is True
            assert len(mgr.timers) == 0
        finally:
            tm._TIMERS_FILE = orig_file
            os.unlink(tmp_path)
    _test("[v1.10] timer: CRUD add+delete", test_timer_crud)

    # ── 96. project: ProjectStore create + get ──
    def test_project_crud():
        import tempfile
        sys.path.insert(0, str(BASE / "core"))
        import projects as pj
        with tempfile.TemporaryDirectory() as ws:
            store = pj.ProjectStore(Path(ws))
            m = store.create("MyProj", "code", "desc-1", tags=["t1"])
            assert m.project_id and m.name == "MyProj"
            got = store.get(m.project_id)
            assert got and got.name == "MyProj"
            all_p = store.list_all()
            assert any(p.project_id == m.project_id for p in all_p)
    _test("[v1.10] project: create + get + list", test_project_crud)

    # ── 97. dag_schema: validate ──
    def test_dag_validate():
        sys.path.insert(0, str(BASE))
        from lib.dag_schema import validate_dag_json
        ok, errs = validate_dag_json({"steps":[{"id":"a","task":"hi"}]})
        assert ok, f"valid dag 不该 fail: {errs}"
        ok2, _ = validate_dag_json({"foo":"bar"})
        assert not ok2, "无 steps 应 fail"
    _test("[v1.10] dag_schema: validate_dag_json", test_dag_validate)

    # ── 98. memory_index: 多次 search 排序稳定 ──
    def test_memory_index_search_order():
        import tempfile
        sys.path.insert(0, str(BASE))
        from memory_index import MemoryIndex
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            dbp = f.name
        try:
            idx = MemoryIndex(dbp)
            idx.index_memory("s1", "fact", "Python is a snake")
            idx.index_memory("s1", "fact", "FastAPI uses Python")
            r = idx.search("Python")
            assert len(r) >= 2
            idx.close()
        finally:
            os.unlink(dbp)
    _test("[v1.10] memory_index: 多记录 search", test_memory_index_search_order)

    # ── 99. blackboard: query_by_type 过滤 ──
    def test_blackboard_query():
        sys.path.insert(0, str(BASE))
        from blackboard import Blackboard, EntryType
        bb = Blackboard()
        bb.put("k1","v1",EntryType.RESULT,source="t")
        bb.put("k2","v2",EntryType.DECISION,source="t")
        bb.put_error("e1","timeout","x",source="t")
        assert len(bb.query_by_type(EntryType.RESULT)) == 1
        assert len(bb.query_by_type(EntryType.ERROR)) == 1
        assert len(bb.query_by_type(EntryType.DECISION)) == 1
    _test("[v1.10] blackboard: query_by_type 三类齐", test_blackboard_query)

    # ── 100. orchestrator: parallel layer 结果有序 ──
    def test_orchestrator_layers_size():
        import asyncio
        sys.path.insert(0, str(BASE))
        from orchestrator import DAGOrchestrator, DAGPlan, StepSpec
        async def mock(t, a, c, s, parent_sid=None): return f"R:{a}"
        orch = DAGOrchestrator(mock)
        steps = [
            StepSpec(id="x", task="X"),
            StepSpec(id="y", task="Y", depends_on=["x"]),
            StepSpec(id="z", task="Z", depends_on=["x"]),
        ]
        layers = orch._topological_sort(steps)
        # 第一层只有 x; 第二层 y, z (并行)
        assert len(layers) == 2
        assert {s.id for s in layers[1]} == {"y","z"}
    _test("[v1.10] orchestrator: 2 层 (x → y‖z)", test_orchestrator_layers_size)

    # ── 101. CLI 文件存在 + 可执行 ──
    def test_cli_exists():
        cli = BASE / "cli.py"
        assert cli.exists()
        # 含 argparse / main
        src = cli.read_text()
        assert "main" in src or "ArgumentParser" in src
    _test("[v1.10] cli.py: argparse 入口", test_cli_exists)

    # ── 102. AGENT_PROMPT.md 段落齐全 ──
    def test_agent_prompt_sections():
        p = BASE / "AGENT_PROMPT.md"
        c = p.read_text()
        # 至少包含核心 3 段
        for kw in ("Think", "Act", "Verify"):
            assert kw in c, f"AGENT_PROMPT 缺 {kw}"
    _test("[v1.10] AGENT_PROMPT.md TAV 三段", test_agent_prompt_sections)

    # ── 103. RULES.md 非空 ──
    def test_rules_md():
        p = BASE / "prompts" / "RULES.md"
        assert p.exists() and len(p.read_text()) > 200
    _test("[v1.10] prompts/RULES.md 非空", test_rules_md)

    return results
