#!/usr/bin/env python3
"""test_full 在线场景定义 SCENARIOS (含 workspace 路径探测 _detect_ws)。"""

from pathlib import Path

from _full_common import _CFG


# ══════════════════════════════════════════════════════════════
# SCENARIO DEFINITIONS (v1.0.6 — 精选 25 个核心场景)
# ══════════════════════════════════════════════════════════════
#
# 设计原则:
#   1. 每个场景测一个独立特性, 无重叠
#   2. 验证机制强化: has_text / tool_names_include / text_contains_any
#      + file_exists (v1.0.6 新增) + min_tool_calls / max_tool_calls
#   3. 总运行时间 < 30 分钟 (平均每场景 60-90s)
#   4. 按类别分组: baseline / search / code / subagent / memory / ui / infra / complex
#
# 运行:
#   python3 test_full.py               # 跑 25 个 (默认)
#   python3 test_full.py --scenario 3  # 单场景
#   python3 test_full.py --offline     # 只跑离线单测

# ─── 辅助: workspace 路径 ───
# [2026-05-30] 路径解析优先级:
#   1. host repo 根 ./workspace (docker-compose 真正 bind mount 的目录)
#   2. container 内 /tmp/litecode_workspace (容器跑测试时)
#   3. config 配的 workspace_base 兜底
# 注意: host 上 /tmp/litecode_workspace 是个 symlink → /tmp/openclaw_workspace (老目录),
# 不能直接用 — 必须用 repo 根的 ./workspace.
def _detect_ws() -> str:
    host_ws = Path(__file__).resolve().parent.parent.parent / "workspace"
    if host_ws.is_dir() and not host_ws.is_symlink():
        return str(host_ws)
    cfg_ws = _CFG.get("paths", {}).get("workspace_base", "/tmp/litecode_workspace")
    p = Path(cfg_ws)
    # 容器内场景: cfg_ws 是真目录
    if p.is_dir() and not p.is_symlink():
        return cfg_ws
    return str(host_ws)
_WS = _detect_ws()

SCENARIOS = [
    # ═══════════════════════════════════════════════════════
    # A. 基线 (2)
    # ═══════════════════════════════════════════════════════
    {
        "id": 1, "name": "基线问答", "tags": ["baseline", "qa", "smoke"],
        "description": "模型连通 + 中文无工具回答",
        "message": "用一句话解释什么是 MVRV 指标, 不要调用任何工具",
        "expect": {
            "has_text": True, "min_chars": 15,
            "no_tool_calls": True,
        },
        "timeout": 60,
    },
    {
        "id": 2, "name": "模型信息自知", "tags": ["baseline", "model", "smoke"],
        "description": "模型应知道自己的名称和后端类型, 无需搜索",
        "message": "告诉我当前使用的模型名称和后端类型 (backend_type), 不要搜索, 直接回答",
        "expect": {
            "has_text": True, "min_chars": 10,
            "no_tool_calls": True,
        },
        "timeout": 30,
    },

    # ═══════════════════════════════════════════════════════
    # B. 搜索 (4) — 覆盖 v1.0 搜索能力
    # ═══════════════════════════════════════════════════════
    {
        "id": 3, "name": "加密货币直连", "tags": ["search", "crypto", "direct_fetch"],
        "description": "web_search 命中 Binance 直连, 不走搜索引擎",
        "message": "当前 BTC 价格是多少 USD? 用 web_search 获取, 输出 JSON: "
                   '{"symbol":"BTC","price_usd":数字,"source":"..."}',
        "expect": {
            "has_text": True,
            "tool_names_include": ["web_search"],
            "text_contains_any": ["BTC", "btc", "bitcoin", "USD", "价格"],
        },
        "timeout": 60,
    },
    {
        "id": 4, "name": "免费API搜索", "tags": ["search", "free_api"],
        "description": "web_search 走 DuckDuckGo Instant / Wikipedia 免费 API",
        "message": "什么是 Merkle tree? 用 web_search 查一下, 然后一句话解释",
        "expect": {
            "has_text": True, "min_chars": 40,
            "tool_names_include": ["web_search"],
            "text_contains_any": ["Merkle", "树", "hash", "哈希", "区块"],
        },
        "timeout": 60,
    },
    {
        "id": 5, "name": "错误语义搜索", "tags": ["search", "github", "error_debug", "smoke"],
        "description": "触发 traceback 后应调 search_code_error 找社区方案",
        "message": (
            "跑这段代码看会报什么错, 然后用 search_code_error 搜一下解决方案:\n"
            "```\npython3 -c 'import requests; requests.get(123)'\n```"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["execute_shell"],
            "text_contains_any": ["TypeError", "error", "错误", "search_code_error", "issue"],
        },
        "timeout": 90,
    },
    {
        "id": 6, "name": "浏览器搜索", "tags": ["search", "browser", "screenshot"],
        "description": "browser_search 真实浏览器 + 截图",
        "message": "用 browser_search 搜'Python asyncio best practices'"
                   ", 返回 top-3 标题即可",
        "expect": {
            "has_text": True,
            "tool_names_include": ["browser_search"],
            "text_contains_any": ["asyncio", "Python", "python", "best", "practices"],
        },
        "timeout": 60,
    },

    # ═══════════════════════════════════════════════════════
    # C. 代码 (5) — 写/跑/改/验证
    # ═══════════════════════════════════════════════════════
    {
        "id": 7, "name": "写+跑+自修复", "tags": ["code", "fix", "smoke"],
        "description": "故意写带 bug 的代码, 运行报错, 修复后再跑通过",
        "message": (
            "写一个 Python 脚本 " + _WS + "/buggy.py 里用到 json.loads 处理 '{{bad json}}' "
            "(故意写错), 运行它看到 JSONDecodeError, 然后修复并重新运行成功"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "text_contains_any": ["JSONDecodeError", "修复", "fix", "成功", "通过"],
            "file_exists": _WS + "/buggy.py",
            # [v1.1] 修复后必须能跑通, 否则 scenario 7 以前会误报 PASS
            "file_runs_ok": _WS + "/buggy.py",
        },
        "timeout": 120,
    },
    {
        "id": 8, "name": "多步链路", "tags": ["code", "multi_step", "chain"],
        "description": "搜索 -> 写代码计算 -> 生成报告 JSON",
        "message": (
            "完成以下三步:\n"
            "1. web_search 查 'Fibonacci 100th number'\n"
            "2. 写 Python " + _WS + "/fib.py 算 F(100), 运行确认\n"
            "3. 写结果到 " + _WS + "/fib_result.json: "
            '{"n":100,"value":..., "source":"..."}'
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "min_tool_calls": 3,
            "text_contains_any": ["354224848179261915075", "Fibonacci", "F(100)"],
            "file_exists": _WS + "/fib_result.json",
        },
        "timeout": 150,
    },
    {
        "id": 9, "name": "SQLite链路", "tags": ["code", "database", "sql", "smoke"],
        "description": "建库 + 建表 + CRUD + 聚合查询",
        "message": (
            "用 Python + sqlite3 完成: "
            "1) 创建 " + _WS + "/users.db 的 users 表 (id/name/age/city)\n"
            "2) 插入 5 条假数据\n"
            "3) 查询 avg(age) 并按 city 分组, 输出结果"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "text_contains_any": ["city", "avg", "average", "分组",
                                    "城市", "平均", "年龄"],
            "file_exists": _WS + "/users.db",
        },
        "timeout": 90,
    },
    {
        "id": 10, "name": "Go 工具链", "tags": ["code", "go", "compile"],
        "description": "Go 环境完整性: 写+编译+跑",
        "message": (
            "写 Go 程序 " + _WS + "/hello.go: 输出 'HELLO_FROM_GO' + 当前时间。"
            "然后 go run 编译运行确认输出包含 HELLO_FROM_GO"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "text_contains_any": ["HELLO_FROM_GO"],
            "file_exists": _WS + "/hello.go",
        },
        "timeout": 60,
    },
    {
        "id": 11, "name": "批量文件操作", "tags": ["code", "file", "batch"],
        "description": "一轮写 3 个 JSON 文件 + 合并脚本",
        "message": (
            "1) 创建 3 个文件 " + _WS + "/a.json / b.json / c.json, "
            '每个含 {"name":"文件X","value":随机数}\n'
            "2) 写 Python 脚本合并成数组 并输出结果"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "text_contains_any": ["合并", "merge", "json", "JSON", "array"],
            "file_exists": _WS + "/a.json",
        },
        "timeout": 90,
    },

    # ═══════════════════════════════════════════════════════
    # D. 子代理 (4) — spawn_agent 4 种模式
    # ═══════════════════════════════════════════════════════
    {
        "id": 12, "name": "子代理单 coder", "tags": ["spawn_agent", "single"],
        "description": "spawn_agent(coder) 基本调度",
        "message": (
            "用 spawn_agent(agent_type='coder') 委派一个子代理完成:\n"
            "写 Python 脚本 " + _WS + "/fact.py 计算 10 的阶乘, 运行验证输出 3628800"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["spawn_agent"],
            "text_contains_any": ["3628800", "阶乘", "factorial"],
            "file_exists": _WS + "/fact.py",
            "file_runs_ok": _WS + "/fact.py",
        },
        "timeout": 180,
    },
    {
        "id": 13, "name": "子代理 Pipeline", "tags": ["spawn_agent", "pipeline"],
        "description": "spawn_agent pipeline_tasks 串行: coder -> tester",
        "message": (
            "用 spawn_agent 的 pipeline_tasks 模式:\n"
            "step1(coder): 写 " + _WS + "/pipe_calc.py 实现 add(a,b)/sub(a,b), 包含简单 __main__ 自测\n"
            "step2(tester): 跑该脚本验证 add(2,3)==5"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["spawn_agent"],
            "text_contains_any": ["PASS", "通过", "5", "验证", "测试"],
            "file_exists": _WS + "/pipe_calc.py",
            "file_runs_ok": _WS + "/pipe_calc.py",
        },
        "timeout": 240,
    },
    {
        "id": 14, "name": "子代理 DAG", "tags": ["spawn_agent", "dag", "v1.0"],
        "description": "spawn_agent dag_tasks: 设计 -> (后端 // 文档) -> 集成",
        "message": (
            "用 spawn_agent 的 dag_tasks:\n"
            '  step_id="design", agent_type="analyst", task="设计一个日志分析 CLI 的接口"\n'
            '  step_id="backend", agent_type="coder", depends_on=["design"], '
            'task="实现 ' + _WS + '/logcli.py, 接受 --level=ERROR 参数过滤日志"\n'
            '  step_id="docs", agent_type="writer", depends_on=["design"], '
            'task="为该 CLI 写 README.md 说明用法"\n'
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["spawn_agent"],
            "text_contains_any": ["DAG", "dag", "设计", "完成", "编排", "分析"],
        },
        "timeout": 600,
    },
    {
        "id": 15, "name": "子代理并行", "tags": ["spawn_agent", "parallel"],
        "description": "spawn_agent parallel_tasks 两个 coder 同时跑",
        "message": (
            "用 spawn_agent parallel_tasks 并行:\n"
            "task1(coder): 写 " + _WS + "/p1.py 输出 PART1 + 时间戳\n"
            "task2(coder): 写 " + _WS + "/p2.py 输出 PART2 + 时间戳\n"
            "都跑一遍验证"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["spawn_agent"],
            "text_contains_any": ["PART1", "PART2"],
            "file_exists": _WS + "/p1.py",
        },
        "timeout": 240,
    },

    # ═══════════════════════════════════════════════════════
    # E. 长文 / 复杂项目 (3)
    # ═══════════════════════════════════════════════════════
    {
        "id": 16, "name": "大纲驱动报告", "tags": ["outline", "writer", "long"],
        "description": "load_skill outline-generation → 大纲 → 章节写作",
        "message": (
            "先 load_skill outline-generation, 然后生成一份技术报告:\n"
            "主题: 'REST vs GraphQL 对比'\n"
            "用 pipeline_tasks: analyst 生成 3 章大纲, 然后 writer 写第一章 (500字)\n"
            "合并到 " + _WS + "/rest_vs_graphql.md"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["spawn_agent"],
            "text_contains_any": ["REST", "GraphQL", "大纲", "章"],
            "file_exists": _WS + "/rest_vs_graphql.md",
        },
        "timeout": 600,
    },
    {
        "id": 17, "name": "长文本切片", "tags": ["longtext", "chunking"],
        "description": "切片 → 处理 → 合并的本地脚本",
        "message": (
            "写 Python 脚本 " + _WS + "/chunker.py:\n"
            "  - 生成 5000 字 lorem ipsum 文本\n"
            "  - 按句子边界切成 ~1500 字的 chunks\n"
            "  - 统计每个 chunk 的字数和句数\n"
            "  - 输出 JSON 汇总\n"
            "运行脚本并确认输出"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "text_contains_any": ["chunk", "切片", "字数", "句"],
            "file_exists": _WS + "/chunker.py",
            "file_runs_ok": _WS + "/chunker.py",
        },
        "timeout": 120,
    },
    {
        "id": 18, "name": "复杂多文件项目", "tags": ["complex", "project", "project_map"],
        "description": "5+ 文件项目, 验证 PROJECT_MAP 自动生成",
        "message": (
            "在 " + _WS + "/taskman/ 下写一个简易 Task Manager:\n"
            "  - models.py: Task 类 (id/title/done)\n"
            "  - storage.py: JSON 持久化\n"
            "  - service.py: add/list/complete 方法\n"
            "  - cli.py: 命令行入口\n"
            "  - test_service.py: 测 add + complete\n"
            "跑 test_service.py 验证全通过"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "min_tool_calls": 5,
            "text_contains_any": ["通过", "PASS", "OK", "测试"],
            "file_exists": _WS + "/taskman/service.py",
        },
        "timeout": 300,
    },

    # ═══════════════════════════════════════════════════════
    # F. 记忆 / 会话 (2)
    # ═══════════════════════════════════════════════════════
    {
        "id": 19, "name": "save_memory", "tags": ["memory", "save"],
        "description": "save_memory 工具写入 L1",
        "message": (
            "记住以下信息到 memory: "
            "'用户偏好 Python + pytest, 不喜欢 unittest'. "
            "用 save_memory 工具存到 '用户偏好' 主题"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["save_memory"],
            "text_contains_any": ["记忆", "保存", "save", "偏好"],
        },
        "timeout": 60,
    },
    {
        "id": 20, "name": "update_profile", "tags": ["memory", "profile"],
        "description": "update_profile 修改 USER.md",
        "message": (
            "把我的时区改成 'America/Los_Angeles', 用 update_profile 修改 USER.md"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["update_profile"],
            "text_contains_any": ["时区", "Los_Angeles", "timezone", "已更新", "修改"],
        },
        "timeout": 60,
    },

    # ═══════════════════════════════════════════════════════
    # G. 技能 / 基础设施 (3)
    # ═══════════════════════════════════════════════════════
    {
        "id": 21, "name": "技能自动加载", "tags": ["skill", "auto"],
        "description": "关键词自动匹配到技能",
        "message": (
            "先 list_skills 看有哪些技能, 然后 load_skill 加载 'file-reading', "
            "简要说明该技能做什么"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["list_skills", "load_skill"],
            "text_contains_any": ["skill", "技能", "file-reading", "读取"],
        },
        "timeout": 60,
    },
    {
        "id": 22, "name": "CJK PDF 生成", "tags": ["pdf", "cjk", "font"],
        "description": "reportlab + Noto Sans CJK 生成中文 PDF",
        "message": (
            "生成一份中文 PDF " + _WS + "/cn_report.pdf: "
            "标题 'AI Agent 技术简报', 含中英文混合段落。"
            "中文不能有方块/乱码。写完用 python 验证文件大小 > 3000 字节"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["execute_shell"],
            "text_contains_any": ["pdf", "PDF", "字节", "3000", "完成"],
            "file_exists": _WS + "/cn_report.pdf",
        },
        "timeout": 180,
    },
    {
        "id": 23, "name": "定时器", "tags": ["timer", "schedule"],
        "description": "创建定时任务 + 触发 + 验证",
        "message": (
            "写 Python 脚本 " + _WS + "/timer_demo.py: "
            "使用 threading.Timer 在 2 秒后写文件 " + _WS + "/timer_out.txt (内容 'fired')。"
            "跑该脚本并等它完成, cat 输出文件确认"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "text_contains_any": ["fired", "触发", "完成"],
            "file_exists": _WS + "/timer_demo.py",
        },
        "timeout": 60,
    },

    # ═══════════════════════════════════════════════════════
    # H. 错误恢复 (1)
    # ═══════════════════════════════════════════════════════
    {
        "id": 24, "name": "错误自愈", "tags": ["error", "recovery", "self_heal"],
        "description": "连续触发多种错误后仍能收敛",
        "message": (
            "按顺序执行, 遇到错误说出原因并继续:\n"
            "1) cat /不存在的路径 (PermanentErr)\n"
            "2) python3 -c 'import nonexistent_module' (ImportErr)\n"
            "3) python3 -c '1/0' (ZeroDivisionError)\n"
            "4) 写一个 " + _WS + "/ok.txt 文件内容 'recovered' 证明能继续\n"
            "最后 echo ALL_ERRORS_HANDLED"
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["execute_shell", "write_file"],
            "text_contains_any": ["ALL_ERRORS_HANDLED", "recovered", "继续"],
            "file_exists": _WS + "/ok.txt",
        },
        "timeout": 180,
    },

    # ═══════════════════════════════════════════════════════
    # I. 日期感知 (1) — v1.0 回归
    # ═══════════════════════════════════════════════════════
    {
        "id": 25, "name": "日期感知", "tags": ["datetime", "v1.0", "smoke"],
        "description": "模型应知道当前是 2026 年, 不是知识截止时间",
        "message": "今年是哪一年? 不要调任何工具, 直接告诉我",
        "expect": {
            "has_text": True,
            "no_tool_calls": True,
            "text_contains_any": ["2026"],
        },
        "timeout": 30,
    },

    # ═══════════════════════════════════════════════════════
    # ═══ v1.1 HEAVY 档 — 真正考验复杂活 (≤ 3h 总时长) ═══
    # ═══════════════════════════════════════════════════════
    # 触发: python3 tests/test_full.py --tier heavy
    #
    # 覆盖: PDF OCR / 带搜索写作 / 长 bug 修复链 / 小说自审 /
    #       记忆穿透 / xlsx 分析 / vision_ocr / DAG 自愈 /
    #       自写自测自评 / 跨会话知识复用
    {
        "id": 26, "name": "[heavy] vision_ocr 中英混合",
        "tags": ["heavy", "vision", "ocr", "multimodal"],
        "description": "调 vision_ocr 识别预置的中英混合截图, 返回文字按版式排列",
        "message": (
            "加载一张本地图片 " + _WS + "/test_ocr_sample.png 用 vision_ocr 识别, "
            "prompt 要求 '逐字输出保持版式'. 把结果 write_file 到 " + _WS + "/ocr_result.md. "
            "如图片不存在, 先 execute_shell 用 python3 + PIL 生成一张含'Hello 世界\\nAI 2026\\n多模态测试'文字的 PNG."
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["execute_shell"],
            "text_contains_any": ["vision_ocr", "OCR", "识别", "保存"],
            "file_exists": _WS + "/ocr_result.md",
        },
        "timeout": 300,
    },
    {
        "id": 27, "name": "[heavy] PDF → MD 有序拼接",
        "tags": ["heavy", "pdf", "ocr", "pipeline"],
        "description": "生成 3 页 PDF, 走 pdf-ocr-pipeline 转 md, 页号顺序正确",
        "message": (
            "1) 用 reportlab (或 fpdf2) 生成 3 页 " + _WS + "/test_doc.pdf, "
            "   每页标题分别 '第一页/第二页/第三页'.\n"
            "2) load_skill pdf-ocr-pipeline.\n"
            "3) 按 skill 流程抽取 + 合并到 " + _WS + "/pdf_out.md.\n"
            "4) 验证 pdf_out.md 含 'Page 1' / 'Page 2' / 'Page 3' 且按序."
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell", "load_skill"],
            "text_contains_any": ["Page 1", "第一页", "成功"],
            "file_exists": _WS + "/pdf_out.md",
        },
        "timeout": 600,
    },
    {
        "id": 28, "name": "[heavy] 带搜索深度报告",
        "tags": ["heavy", "report", "search", "writer"],
        "description": "5000 字 AI 行业报告, writer 中途 ≥ 3 次搜索",
        "message": (
            "load_skill deep-report, 写一份 AI 行业深度报告到 " + _WS + "/ai_report.md, "
            "主题: '2025 年全球 AI 行业现状', 至少 3000 字, 每主要论点 ≥ 1 条引用. "
            "必须用 spawn_agent(writer) + writer 中途 web_search 3 次以上."
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["spawn_agent"],
            "text_contains_any": ["AI", "报告", "web_search", "引用"],
            "file_exists": _WS + "/ai_report.md",
        },
        "timeout": 900,
    },
    {
        "id": 29, "name": "[heavy] 小说自审回炉",
        "tags": ["heavy", "novel", "critic", "writer"],
        "description": "写 1 章玄幻 3000 字, 触发 anti-ai-tell-audit, 不合格重写 1 次",
        "message": (
            "load_skill novel-xuanhuan + anti-ai-tell-audit.\n"
            "写玄幻小说第 1 章到 " + _WS + "/novel_ch01.md (3000 字). 题材: 废柴觉醒金手指打脸. "
            "写完立刻跑 ai_tell_audit.py 自审 (execute_shell). "
            "如果不通过 (退出码 1) 就按建议重写一次. "
            "最终要求: 中文字数 ≥ 2800, 【】≤2, 瞳孔一缩等套话 ≤3 次."
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["load_skill", "write_file", "execute_shell"],
            "text_contains_any": ["通过", "自审", "字数"],
            "file_exists": _WS + "/novel_ch01.md",
        },
        "timeout": 900,
    },
    {
        "id": 30, "name": "[heavy] 长 bug 修复链",
        "tags": ["heavy", "code", "fix", "loop"],
        "description": "给定含 5 处故意 bug 的 project, agent 必须修到 pytest 全绿",
        "message": (
            "在 " + _WS + "/buggy_project/ 下做:\n"
            "1) 写 calc.py 含 5 个函数 (add/sub/mul/div/pow), 每个故意埋一个 bug "
            "(例: sub 写成 a+b, div 不处理 0, pow 用 *).\n"
            "2) 写 test_calc.py 覆盖这 5 个函数, 每个至少 2 个断言.\n"
            "3) 跑 pytest, 预期失败.\n"
            "4) 根据 traceback 逐个修复, 直到 pytest 全绿.\n"
            "5) 最后 echo ALL_FIXED."
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "min_tool_calls": 8,
            "text_contains_any": ["ALL_FIXED", "passed", "通过"],
            "file_exists": _WS + "/buggy_project/calc.py",
        },
        "timeout": 1200,
    },
    {
        "id": 31, "name": "[heavy] 自写自测自评",
        "tags": ["heavy", "code", "test", "critic"],
        "description": "实现 LRU Cache + 覆盖率 > 80%, critic 审查",
        "message": (
            "用 spawn_agent 的 dag_tasks 完成:\n"
            "  step_id='impl', agent_type='coder', task='实现 " + _WS + "/lru.py LRUCache(capacity) 支持 get/put'\n"
            "  step_id='test', agent_type='tester', depends_on=['impl'], "
            "task='写 " + _WS + "/test_lru.py 覆盖 8+ 测试点, 跑 pytest --cov 验证覆盖率'\n"
            "  step_id='review', agent_type='critic', depends_on=['test'], task='审查代码是否有死代码/异常边界'\n"
            "要求: pytest 全绿 + critic 通过 (无 [CRITICAL])."
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["spawn_agent"],
            "text_contains_any": ["LRU", "passed", "通过", "覆盖"],
            "file_exists": _WS + "/lru.py",
            "file_runs_ok": _WS + "/lru.py",
        },
        "timeout": 1200,
    },
    {
        "id": 32, "name": "[heavy] xlsx 数据分析",
        "tags": ["heavy", "xlsx", "data"],
        "description": "1000 行销售数据 → 分组统计 + md 报告",
        "message": (
            "1) 生成 " + _WS + "/sales.xlsx 1000 行数据 (date/product/region/amount).\n"
            "2) 用 openpyxl + pandas 分析: 按 region 汇总 total + top3 product.\n"
            "3) 输出 " + _WS + "/sales_report.md 含表格 + 简要分析 (≥500 字)."
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["write_file", "execute_shell"],
            "text_contains_any": ["region", "top", "汇总", "分析"],
            "file_exists": _WS + "/sales_report.md",
        },
        "timeout": 600,
    },
    {
        "id": 33, "name": "[heavy] 跨会话记忆穿透",
        "tags": ["heavy", "memory"],
        "description": "同一 session 100+ 轮后仍记得开头设定",
        "message": (
            "我是 Anna. 我的职业是数据分析师. 我用 Python + SQL. "
            "我讨厌 Excel. 请用 save_memory 记住这些. "
            "然后随机找一本中国古典小说的一段文字 (《红楼梦》为例), "
            "用 web_search 查 1 段原文, 写到 " + _WS + "/anna_note.md. "
            "最后在文件末尾加一行 '[针对 Anna 的风格推荐]' 总结, "
            "必须体现对 Python/SQL/讨厌 Excel 的理解."
        ),
        "expect": {
            "has_text": True,
            "tool_names_include": ["save_memory", "web_search", "write_file"],
            "text_contains_any": ["Python", "Anna", "SQL"],
            "file_exists": _WS + "/anna_note.md",
        },
        "timeout": 600,
    },
    # ── P31 复杂场景扩充 (#34-#39) ──────────────────────────────
    {
        "id": 34, "name": "P31-a 超长翻译 30k 中→英",
        "tags": ["heavy", "translation", "longtext", "chunking", "skill"],
        "description": "30000 字中文 → 英文, 走 longtext-processing 切片, 术语一致 ≥95%",
        "input": "请把以下中文小说翻译成英文 (字数约 30000):\n[长文 placeholder]",
        "expect": {"min_response_chars": 25000, "tool_names_include": ["spawn_agent"]},
        "timeout": 1800,
    },
    {
        "id": 35, "name": "P31-e 多语言 OCR + 翻译",
        "tags": ["heavy", "vision", "multilingual", "ocr"],
        "description": "日韩中英 4 种文字截图 → vision_ocr → 译成中文",
        "input": "[图片占位] 识别图中所有文字, 全部翻译成中文",
        "expect": {"tool_names_include": ["vision_ocr"]},
        "timeout": 600,
    },
    {
        "id": 36, "name": "P31-b 跨 skill 流水线",
        "tags": ["heavy", "skill", "pipeline", "multi_skill"],
        "description": "PDF → pdf-ocr → longtext → anti-ai-tell → deep-report",
        "input": "用 PDF 论文写中文研究报告, 走完整 skill 流水线",
        "expect": {"min_skills_used": 4},
        "timeout": 1800,
    },
    {
        "id": 37, "name": "P31-c 21 题材小说全员",
        "tags": ["heavy", "novel", "all_genres"],
        "description": "玄幻/修真/都市/末世/科幻 5 题材各 1500 字, anti-ai-tell ≥70",
        "input": "为玄幻/修真/都市/末世/科幻 5 题材各写 1500 字开头",
        "expect": {"min_response_chars": 7500},
        "timeout": 1800,
    },
    {
        "id": 38, "name": "P31-d 24h 长会话耐压",
        "tags": ["heavy", "longsession", "memory", "stress"],
        "description": "200 轮对话 + 5 主题切换 + 召回开头",
        "input": "[200 轮模拟] 第 200 轮问: 你还记得第 1 轮我说了什么?",
        "expect": {"compress_count_min": 3},
        "timeout": 3600,
    },
    {
        "id": 39, "name": "P31-f 微信端到端",
        "tags": ["heavy", "wechat", "e2e", "multimodal"],
        "description": "短问/长任务/图片/文件混合输入, 保活机制",
        "input": "[微信 4 种输入混合]",
        "expect": {"all_responded": True},
        "timeout": 1800,
    },
]
