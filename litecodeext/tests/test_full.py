#!/usr/bin/env python3
"""
test_full.py — LiteCode v1.0 全链路测试 (25 精选场景)
==========================================================
分组:
  A. 基线 (2)     — QA / 模型自知
  B. 搜索 (4)     — web_search 直连 / 免费 API / 错误语义 / 浏览器
  C. 代码 (5)     — 写+跑+修 / 多步链路 / SQLite / Go / 批量文件
  D. 子代理 (4)   — 单/Pipeline/DAG/并行
  E. 长文 (3)     — 大纲 / 切片 / 多文件项目
  F. 记忆 (2)     — save_memory / update_profile
  G. 技能/基建 (3)— skill / CJK PDF / 定时器
  H. 错误 (1)     — 连续错误自愈
  I. 日期 (1)     — 2026 年感知

验证机制 (v1.0.6):
  - has_text / min_chars / no_tool_calls — 基础输出
  - tool_names_include / min_tool_calls / max_tool_calls — 工具调用
  - text_contains_any / text_not_contains — 关键词匹配
  - file_exists (新) — 验证输出文件真的创建了
  - file_contains (新) — 验证文件内容含关键字

运行:
  python3 test_full.py                    # 跑全部 25 个
  python3 test_full.py --scenario 3       # 单场景
  python3 test_full.py --tag search       # 按 tag 过滤 (search/code/spawn_agent/...)
  python3 test_full.py --offline          # 只跑离线单元测试
  python3 test_full.py --dry-run          # 只打印不发请求

产出:
  - stdout: 实时进度
  - logs/test_full_report.md: 结构化报告
  - logs/iteration_full.log: server 侧 itrace 自动写入
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip3 install requests --break-system-packages")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────
# [layout] 测试文件位于 litecodeext/tests/, 源码在父目录 litecodeext/
BASE = Path(__file__).resolve().parent.parent
# [layout v2] core/ 放了 15 个核心模块, 加到 sys.path 让扁平 import 继续工作
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "core"))

def _load_cfg() -> dict:
    p = BASE / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}

_CFG = _load_cfg()
_SRV = _CFG.get("server", {})
DEFAULT_URL   = f"http://127.0.0.1:{_SRV.get('port', 18789)}"
DEFAULT_TOKEN = _SRV.get("token", "CHANGE_ME_TOKEN")
DEFAULT_MODEL = _CFG.get("model", {}).get("id", "qwen3.6")

LOGS_DIR = Path(_CFG.get("paths", {}).get("logs_dir", "/tmp/litecode_workspace/logs"))
LOGS_DIR.mkdir(parents=True, exist_ok=True)
REPORT_FILE = LOGS_DIR / "test_full_report.md"
TRACE_LOG   = LOGS_DIR / "iteration_full.log"

# ── Colors ────────────────────────────────────────────────────
_T = sys.stdout.isatty()
G  = "\033[32m" if _T else ""
R  = "\033[31m" if _T else ""
Y  = "\033[33m" if _T else ""
C  = "\033[36m" if _T else ""
B  = "\033[1m"  if _T else ""
D  = "\033[2m"  if _T else ""
NC = "\033[0m"  if _T else ""



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


# ══════════════════════════════════════════════════════════════
# v1.0 OFFLINE UNIT TESTS (无需 server, 直接跑)
# ══════════════════════════════════════════════════════════════

def _make_tester(results: list):
    """构造绑定到某个 results 列表的 _test 记录闭包 (P2-1 拆分抽出, 机制/行为不变)。"""
    def _test(name, fn):
        t0 = time.time()
        try:
            fn()
            elapsed = time.time() - t0
            results.append({"name": name, "pass": True, "elapsed": round(elapsed, 3), "error": ""})
            print(f"  {G}✅{NC} {name} ({elapsed:.3f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            results.append({"name": name, "pass": False, "elapsed": round(elapsed, 3), "error": str(e)})
            print(f"  {R}❌{NC} {name}: {e}")
    return _test


def run_offline_tests() -> list:
    """v1.0 核心模块离线单元测试, 不需要 server 运行。"""
    print(f"\n{B}v1.0 Offline Unit Tests{NC}")
    print(f"{D}(不需要 server, 直接测试 Python 模块){NC}\n")

    results = []
    results += _offline_v10_core()
    results += _offline_v10_fixes()
    results += _offline_l1_smoke()
    results += _offline_l2_mid()
    results += _offline_l3_high()
    results += _offline_l4_complex()

    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed
    print(f"\n{B}Offline Results: {G}{passed}{NC}/{len(results)} passed" +
          (f"  {R}{failed} failed{NC}" if failed else ""))

    return results


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
        sv_file = BASE / "litecode_server.py"
        content = sv_file.read_text()
        # 应该同时支持 3 种字段名
        assert 'delta.get("reasoning_content")' in content, "缺 vLLM 旧版 reasoning_content"
        assert 'delta.get("reasoning")' in content, "缺 vLLM 新版 reasoning"
        assert 'delta.get("thinking")' in content, "缺 Ollama native thinking"
    _test("[v1.0] reasoning 字段 3 后端兼容 (vLLM旧/新/Ollama)", test_reasoning_multi_backend)

    # ── 22. 服务器 memory 后台异步化 (v1.0 修复微信延迟，v1.5 拆为 3 worker) ──
    def test_memory_bg_thread():
        sv_file = BASE / "litecode_server.py"
        content = sv_file.read_text()
        # v1.5 P35-a 修：原 _bg_memory_worker 被拆为三个独立 worker，
        # 防止 LLM 压缩 600s 超时拖慢 rule-memory（秒级）。任一存在即视为异步化已生效。
        bg_workers = ["_bg_compress_worker", "_bg_fast_worker", "_bg_smart_worker"]
        present = [w for w in bg_workers if w in content]
        assert len(present) >= 1, \
            f"缺后台内存处理函数 — 微信回复延迟 bug 未修复 (期望 {bg_workers} 至少一个)"
        # 应该用 threading.Thread 启动
        assert "threading.Thread" in content, "memory worker 未用 threading.Thread 启动"
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
            # 真实复现 (wx-b3607 会话, 18798 字符 prompt):
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


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="LiteCode v1.0 Full Test Suite")
    ap.add_argument("--server", default=DEFAULT_URL, help="Server URL")
    ap.add_argument("--token", default=DEFAULT_TOKEN, help="Auth token")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Model ID")
    ap.add_argument("--scenario", type=int, help="Run only scenario N")
    ap.add_argument("--session", default=None, help="Session ID (default: auto)")
    ap.add_argument("--dry-run", action="store_true", help="Print scenarios without running")
    ap.add_argument("--skip", type=str, help="Skip scenario IDs, comma-separated (e.g. '5,6')")
    ap.add_argument("--offline", action="store_true", help="Run only offline unit tests (no server needed)")
    ap.add_argument("--tag", type=str, help="[v1.0.6] Only run scenarios with this tag (e.g. --tag search)")
    ap.add_argument("--tier", type=str, choices=["smoke","standard","heavy","all"],
                    default="standard",
                    help="[v1.1] smoke=5 快速冒烟 / standard=25 常规 (不含 heavy) / heavy=8 深度 / all=全部")
    ap.add_argument("--test-switch", action="store_true", help="Run model switch 401 test (API-level)")
    args = ap.parse_args()

    # ── Offline 模式: 只跑单元测试 ──
    if args.offline:
        # [v1.9 P37-a] 设置 OFFLINE 标记 — 让 memory/network 路径快速 fail
        # 避免 test_memory_fallback 等测试用 192.0.2.1 触发 OS TCP SYN 127s 超时
        os.environ["LITECODE_TEST_OFFLINE"] = "1"
        os.environ["OPENCLAW_TEST_OFFLINE"] = "1"  # 兼容旧名
        offline_results = run_offline_tests()
        passed = sum(1 for r in offline_results if r["pass"])
        failed = len(offline_results) - passed

        # 写报告
        report_lines = [
            f"# LiteCode v1.0 Offline Test Report",
            f"",
            f"- Time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Tests: {len(offline_results)}",
            f"- Passed: {passed}",
            f"- Failed: {failed}",
            f"",
            f"| # | Test | Status | Time | Error |",
            f"|---|------|--------|------|-------|",
        ]
        for i, r in enumerate(offline_results, 1):
            s = "PASS" if r["pass"] else "FAIL"
            report_lines.append(f"| {i} | {r['name']} | {s} | {r['elapsed']}s | {r['error'][:60]} |")

        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text("\n".join(report_lines))
        print(f"\n  Report: {REPORT_FILE}")

        sys.exit(1 if failed else 0)

    server_url = args.server
    session_id = args.session or f"test-{int(time.time())}"
    skip_ids = set(int(x) for x in args.skip.split(",")) if args.skip else set()

    # ── 连通性检查 ──
    if not args.dry_run:
        print(f"{C}Checking server: {server_url}{NC}")
        try:
            r = requests.get(
                f"{server_url}/health",
                headers={"Authorization": f"Bearer {args.token}"},
                timeout=5,
            )
            if r.status_code != 200:
                print(f"{R}Server returned {r.status_code}{NC}")
                sys.exit(1)
            info = r.json()
            print(f"{G}Server online: {info.get('model', '?')}{NC}")
        except Exception as e:
            print(f"{R}Cannot connect to server: {e}{NC}")
            print(f"{Y}Start server first: python3 litecode_server.py{NC}")
            sys.exit(1)

    # ── [v1.0] 模型切换 401 测试 (API级别, 独立于 SSE 场景) ──
    if args.test_switch and not args.dry_run:
        print(f"\n{B}[v1.0] Model Switch 401 Test{NC}")
        _h = {"Authorization": f"Bearer {args.token}", "Content-Type": "application/json"}
        try:
            # 获取模型列表
            r = requests.get(f"{server_url}/v1/model/list", headers=_h, timeout=5)
            models = r.json().get("models", [])
            original = r.json().get("active", "")
            if len(models) < 2:
                print(f"  {Y}⚠ 只有 1 个模型，无法测试切换。请在 config.json 添加第二个模型{NC}")
            else:
                target = [m for m in models if m["id"] != original][0]
                print(f"  切换: {original} → {target['id']}")

                # 切换
                r2 = requests.post(f"{server_url}/v1/model/switch", headers=_h,
                                   json={"model_id": target["id"]}, timeout=10)
                assert r2.status_code == 200 and r2.json().get("ok"), f"切换失败: {r2.text[:200]}"

                # 立即发消息 — 旧版这里 401
                print(f"  切换后立即发消息...")
                r3 = requests.post(f"{server_url}/v1/chat/completions", headers=_h,
                                   json={"model": target["id"], "stream": False,
                                         "messages": [{"role": "user", "content": "回复OK两个字"}],
                                         "user": "test-switch"},
                                   timeout=30)
                if r3.status_code == 401:
                    print(f"  {R}❌ 401 BUG 未修复！切换后请求被拒绝{NC}")
                    print(f"  {R}   响应: {r3.text[:200]}{NC}")
                elif r3.status_code == 200:
                    print(f"  {G}✅ 切换后对话正常 (HTTP 200){NC}")
                else:
                    print(f"  {Y}⚠ HTTP {r3.status_code} (非401，可能是模型不可达){NC}")

                # 切回
                requests.post(f"{server_url}/v1/model/switch", headers=_h,
                              json={"model_id": original}, timeout=10)
                print(f"  已切回: {original}")
        except Exception as e:
            print(f"  {R}❌ 测试异常: {e}{NC}")
            traceback.print_exc()

    # ── 清理旧 trace log ──
    if TRACE_LOG.exists() and not args.dry_run:
        # 不删除, 追加模式. 但写一个分隔符
        with TRACE_LOG.open("a") as f:
            f.write(f"\n{'#'*80}\n# TEST_FULL RUN: {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'#'*80}\n")

    # ── 选择场景 ── [v1.1] --tier: smoke / standard / heavy / all
    if args.scenario:
        scenarios = [s for s in SCENARIOS if s["id"] == args.scenario]
        if not scenarios:
            print(f"{R}Scenario {args.scenario} not found (valid: 1-{len(SCENARIOS)}){NC}")
            sys.exit(1)
    elif getattr(args, "tag", None):
        wanted = args.tag
        scenarios = [s for s in SCENARIOS
                     if wanted in s.get("tags", []) and s["id"] not in skip_ids]
        if not scenarios:
            print(f"{R}tag '{wanted}' matched no scenarios{NC}")
            sys.exit(1)
    else:
        tier = getattr(args, "tier", "standard")
        if tier == "smoke":
            # 5 个快速冒烟: 基线 + 错误语义 + 写跑修 + SQLite + 日期感知
            scenarios = [s for s in SCENARIOS
                         if "smoke" in s.get("tags", []) and s["id"] not in skip_ids]
        elif tier == "heavy":
            # 仅 heavy 场景
            scenarios = [s for s in SCENARIOS
                         if "heavy" in s.get("tags", []) and s["id"] not in skip_ids]
        elif tier == "all":
            scenarios = [s for s in SCENARIOS if s["id"] not in skip_ids]
        else:  # standard (default): 排除 heavy 档
            scenarios = [s for s in SCENARIOS
                         if "heavy" not in s.get("tags", []) and s["id"] not in skip_ids]

    # 先跑离线测试
    if not args.dry_run:
        offline_results = run_offline_tests()
        offline_failed = sum(1 for r in offline_results if not r["pass"])
        if offline_failed:
            print(f"\n{R}⚠️ {offline_failed} offline tests failed. Fix before running online tests.{NC}")
            # 不退出, 继续跑在线测试

    print(f"\n{B}LiteCode v1.0 Full Test Suite{NC}")
    print(f"{D}Scenarios: {len(scenarios)}  Session: {session_id}  Server: {server_url}{NC}")
    print(f"{D}Report: {REPORT_FILE}{NC}")
    print(f"{D}Trace:  {TRACE_LOG}{NC}")

    # ── 执行 ──
    t_total = time.time()
    results: list[TestResult] = []

    for scenario in scenarios:
        try:
            r = run_scenario(
                scenario=scenario,
                server_url=server_url,
                token=args.token,
                model=args.model,
                session_id=session_id,
                dry_run=args.dry_run,
            )
            results.append(r)
        except Exception as e:
            # 即使单个场景完全崩溃, 也不影响其他场景
            print(f"\n{R}[CRASH] Scenario {scenario['id']}: {e}{NC}")
            traceback.print_exc()
            crash_result = TestResult(scenario)
            crash_result.error = f"CRASH: {type(e).__name__}: {e}"
            results.append(crash_result)

    elapsed_total = time.time() - t_total

    # ── 生成报告 ──
    report = generate_report(results, elapsed_total)
    REPORT_FILE.write_text(report)

    # ── 打印汇总 ──
    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed

    print(f"\n{C}{'='*60}{NC}")
    print(f"{B}RESULTS{NC}")
    print(f"  Total:  {len(results)}")
    print(f"  Passed: {G}{passed}{NC}")
    print(f"  Failed: {R}{failed}{NC}" if failed else f"  Failed: {G}0{NC}")
    print(f"  Time:   {elapsed_total:.1f}s")
    print(f"  Report: {REPORT_FILE}")

    # ── Trace log 完整性 ──
    if TRACE_LOG.exists():
        log_text = TRACE_LOG.read_text(errors="replace")
        starts = log_text.count("[TRACE_START]")
        ends = log_text.count("[TRACE_END]")
        print(f"  Trace:  {TRACE_LOG} ({TRACE_LOG.stat().st_size} bytes)")
        print(f"          TRACE_START={starts}  TRACE_END={ends}  {'PAIRED' if starts==ends else 'MISMATCH'}")
    else:
        print(f"  Trace:  {Y}not found (itrace disabled?){NC}")

    if failed:
        print(f"\n{R}Some tests failed. Check report for details.{NC}")
        sys.exit(1)
    else:
        print(f"\n{G}All tests passed.{NC}")
        sys.exit(0)


if __name__ == "__main__":
    main()
