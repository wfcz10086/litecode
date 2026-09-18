"""
lib/agent/subagent.py — v1.0 子代理配置 + 运行器
=================================================
在 v1.0 基础上新增:
  - Critic Agent 类型: 专职代码审查
  - 错误栈弹回: 超时/max_turns 时返回结构化错误摘要
  - 滚动摘要: 每 8 轮压缩历史消息，释放 context 空间
  - 黑板集成: 子 Agent 可通过 update_global_context 工具写入共享上下文

SSE 流式不变。
"""
import asyncio
import contextvars as _cvar
import json
import logging
import time
import uuid
import re as _re
from typing import Optional, Callable, List, Dict, Tuple

from ..config import (
    BACKEND_URL, API_KEY, MODEL_ID, MAX_TOKENS, CONTEXT_WINDOW,
    WORKSPACE, ENABLE_THINKING, THINKING_BUDGET, log,
)
from ..tools.defs import TOOL_DEFS
from ..transport import vllm_stream as _vllm_stream
from ..sse import sse_status, sse_content

SUBAGENT_TELEMETRY_DIR = WORKSPACE / "telemetry"
SUBAGENT_TELEMETRY_PATH = SUBAGENT_TELEMETRY_DIR / "subagent.jsonl"


# ═══════════════════════════════════════════════════════
# Subagent 配置（含 v1.0 Critic Agent）
# ═══════════════════════════════════════════════════════

# [v1.0] 所有子代理继承主 agent 的铁律（避免子代理不遵守 read-first/verify 规则）
_SUBAGENT_IRON_RULES = (
    "\n\n## 铁律（继承自主 agent，违反 = 严重错误）\n"
    "1. 改已有文件前必须先 read_file 看当前内容，不能凭记忆猜\n"
    "2. 写完代码必须运行一次验证，看到正确输出才算完成（py_compile 只是语法检查，不够）\n"
    "3. 每次工具调用后必须输出一句话说结果和下一步\n"
    "4. 同一策略连续失败 2 次 → 换工具/换思路，不要第 3 次重试\n"
    "5. 不知道的人名/API/版本/库名 → 先搜再用，不编造\n"
    "6. web_fetch 返回 ERROR → 立即换 web_search，不重试同域名\n"
    "7. [STDERR_WARNINGS - exit 0] 前缀 = 命令成功有警告，不是错误，不重试\n"
)

_SUBAGENT_CONFIGS: dict = {
    "explorer": {
        "system": (
            "你是代码探索专家。任务：分析项目结构、定位关键文件、理解代码逻辑。\n"
            "工具限制：只能用 get_tree / find_files / search_code / read_file。\n"
            "输出：结构化的发现报告，包含关键路径和重要信息。"
        ),
        "allowed": {"get_tree", "find_files", "search_code", "read_file"},
    },
    "researcher": {
        "system": (
            "你是信息收集专家。任务：搜索网络获取所需数据（价格/新闻/文档/API等）。\n"
            "工具：web_fetch / web_search / browser_search / browser_read / execute_shell。\n"
            "搜索策略 (v1.2):\n"
            "  1. 任务明确指明 REST API 端点 (含 api. 域名 / .json 路径) → 直接 execute_shell 跑 curl\n"
            "     例: `curl -s 'https://api.binance.com/api/v3/ticker/24hr' | jq '.[:40]'`\n"
            "     比绕 web_search 一圈拿到验证码页面靠谱得多\n"
            "  2. 通用网页/新闻/文档 → 先 web_search (免费 API: DuckDuckGo + Wikipedia + SearXNG)\n"
            "  3. web_search 结果为空或被反爬挡住 → browser_search (真实浏览器 + 截图)\n"
            "  4. 需要看 JS 渲染后的页面 → browser_read(url)\n"
            "输出：原始数据 + 来源 URL + 截图路径 (若有)，不要分析，直接返回数据供下游。"
        ),
        "allowed": {"web_fetch", "web_search", "browser_search", "browser_read",
                     "execute_shell"},
    },
    "coder": {
        "system": (
            "你是全栈编程专家。根据任务选择最合适的语言（Shell/Python/Go/JS/Rust...）。\n\n"
            "## 四段式工作流（每次任务必须显式经过这 4 步）\n"
            "**1. 分析**: 读现有文件 / 看报错 / 理解需求。用 read_file / get_tree，\n"
            "   输出一段话描述 '我理解的问题是 X, 关键点是 Y, Z 可能是坑'。\n"
            "   [v1.0.4] 大项目先看 PROJECT_MAP 摘要 (注入在系统提示末尾), 按索引定位文件。\n"
            "**2. 计划**: 列出接下来要做的 2-5 步. 格式: '- 步骤1: 写 X 文件 - 步骤2: 跑 Y 验证'.\n"
            "   禁止略过直接写代码。\n"
            "**3. 执行**: 按计划写/改/跑。每个工具调用后说一句 '这一步结果 OK / 有问题'。\n"
            "   写/改源文件后, 每 5 个源文件调一次 update_map 注册到 PROJECT_MAP (大项目必须)。\n"
            "**4. 验证**: 必须真跑一次 (execute_shell), 看到正确输出才能说完成.\n"
            "   py_compile 只是语法检查, 不算验证。\n\n"
            "## 报错处理流程 (v1.0.3)\n"
            "execute_shell 返回 traceback / 报错时:\n"
            "  a. 先看报错里的异常类+消息, 说一句 '根因可能是 X'\n"
            "  b. 如果是库/框架的已知问题 → 直接调 search_code_error(error_text=<stderr>)\n"
            "     它会自动搜 GitHub issues + StackOverflow, 返回别人遇过的相同错+解决方案\n"
            "  c. 拿到 github/SO 结果 → 说 '这个问题在 issue #N 修过, 我照它的思路改'\n"
            "  d. 改代码 + 重新验证\n\n"
            "## 输出\n"
            "每完成一个子任务: 代码文件路径 + 运行命令 + stdout/stderr/exit code. 不要抽象描述。"
        ),
        "allowed": {"write_file", "patch_file", "execute_shell", "read_file",
                     "get_tree", "update_global_context",
                     # [v1.0.3] 错误语义搜索
                     "search_code_error", "github_search_issues", "stackoverflow_search",
                     # [v1.0.4] 项目地图 (大项目导航 + 索引)
                     "update_map", "find_files", "search_code"},
        "max_iter": 50,
    },
    "analyst": {
        "system": (
            "你是数据分析专家。\n\n"
            "## 四段式工作流\n"
            "**1. 分析**: 明确要回答的问题 + 需要的数据源. 一句话说 '我要回答 X, 需要从 Y 拿 Z'.\n"
            "**2. 计划**: 列步骤. 格式: '- 步骤1: web_search 或 browser_search 拿 X - 步骤2: ...'\n"
            "**3. 执行**: 先搜数据 → 写代码分析 → 跑 → 看结果. 每个数字都必须来自代码输出.\n"
            "**4. 验证**: 结论要有代码运行截图/输出支撑. 交叉核对至少 2 个来源.\n\n"
            "## 错误处理\n"
            "数据搜不到 → browser_search 升级; 代码报错 → search_code_error 找已知方案.\n"
            "不得凭记忆给数字, 所有结论注明数据来源 URL."
        ),
        "allowed": {"web_fetch", "web_search", "browser_search", "browser_read",
                     "execute_shell", "write_file", "read_file",
                     "update_global_context",
                     "search_code_error", "github_search_issues", "stackoverflow_search"},
        "max_iter": 40,
    },
    "tester": {
        "system": (
            "你是测试验证专家。\n\n"
            "## 四段式工作流\n"
            "**1. 分析**: 读被测文件, 理解它声明的行为 (函数签名/接口). 列出要验证的点.\n"
            "**2. 计划**: 每个点对应什么命令/测试用例. 例: '- 点1: import 模块看有无 ImportError - 点2: pytest tests/ - 点3: ...'\n"
            "**3. 执行**: 依次跑. 每个点后报 PASS / FAIL + 关键输出.\n"
            "**4. 验证**: 失败的点要再读源码 + 复跑, 给出根因指导意见 (不改代码, 只诊断).\n\n"
            "## 错误根因\n"
            "看到 traceback → search_code_error 找社区已知问题, 返回给上游 (coder) 参考.\n"
            "输出: PASS/FAIL 表格 + 每个 FAIL 的根因分析 + 修复建议 (如'issue #123 建议改 X')."
        ),
        "allowed": {"execute_shell", "read_file",
                     "search_code_error", "github_search_issues", "stackoverflow_search"},
        "max_iter": 30,
    },
    "shell": {
        "system": (
            "你是 Shell 脚本专家。优先用 bash/sh 完成任务，避免引入额外语言依赖。\n"
            "写完脚本必须运行验证。\n"
            "输出：脚本内容 + 运行结果。"
        ),
        "allowed": {"execute_shell"},
    },
    "writer": {
        "system": (
            "你是长文写作专家。任务：按大纲创作高质量中文内容（小说/报告/文案/论文）。\n\n"
            "## ⛔️ 强制 GUARD（违反一律死循环, 必须遵守）\n\n"
            "**第 1 步永远是**: execute_shell `ls -1 *.md chapters/*.md 2>/dev/null | sort` —— "
            "看清已存在哪些章节文件, 用于跳过.\n"
            "**第 2 步**: read_file story_state.json 拿 current_chapter (没有就当 0).\n"
            "**写文件铁律**:\n"
            "  - 已存在的 chXX.md / ch_XX.md / chapter_XX.md → **禁止 write_file 覆盖**, 跳到下一章\n"
            "  - 同一文件 `write_file` 最多调 1 次；要修改用 `patch_file`\n"
            "  - 同一轮内**不允许** write_file 同一路径多次 (框架会在第 4 次同 path 强制 break)\n"
            "  - 写完一章立刻返回**简短摘要**（一句话 + 字数 + 路径）, 然后等下一轮指令; 不要连续写多章\n"
            "**完成判定**: 若 current_chapter ≥ 目标章号, 立即用文本回答 `[writer-done] 已完成 ch1..chN`, 不再调任何工具.\n\n"
            "## 写作流程（每一章都必须遵守）\n\n"
            "1. ls 已有章节 (上面 GUARD 第 1 步)\n"
            "2. **先 read_file story_state.json**，加载角色/情节/世界观状态\n"
            "3. 根据大纲和状态写**下一个未写**的章节（绝不重写已存在的）\n"
            "4. 写完后**立即 patch_file story_state.json**，更新：\n"
            "   - current_chapter +1\n"
            "   - 新角色加入 characters\n"
            "   - 本章事件加入 plot_events\n"
            "   - 角色状态变化更新\n\n"
            "## 写作规则\n"
            "- 每次 write_file 只写一个章节（2500-3500 字）, 写完返回摘要, 不连写多章\n"
            "- 角色名、武功名、地名必须与 story_state.json 完全一致\n"
            "- 不得出现前后矛盾（角色技能/关系/状态）\n"
            "- 每章结尾要有悬念或过渡\n"
            "- 节奏紧凑，不水字数\n\n"
            "## 一致性检查\n"
            "写完一章后，对照 story_state.json 检查：\n"
            "- 本章提到的角色是否都在 characters 中？\n"
            "- 角色使用的技能是否与记录一致？\n"
            "- 是否引入了新设定？如果是，必须记录到 world_rules\n\n"
            "## [v1.1] 边写边查 (有搜索权限时)\n"
            "以下内容出现即触发 web_search, 不搜就停笔:\n"
            "  🔴 4 位数真实年份 (除非已在 world_bible 登记)\n"
            "  🔴 真实人物姓名 (演员/作家/历史人物/企业家)\n"
            "  🔴 真实公司/品牌/APP\n"
            "  🔴 技术术语 (AI/量子/区块链/CRISPR)\n"
            "  🔴 具体真实地点 / 地标\n"
            "  🟡 影视剧引用 (剧情/台词/演员)\n"
            "  🟡 股票 / 加密货币价格\n"
            "  🟡 法律条文 / 医学常识\n"
            "搜到后标注来源 [source: xxx]; 多源矛盾取权威; 搜不到写 [TBD] 不编造.\n\n"
            "## [v1.1] 题材 skill 加载 (写前必做)\n"
            "写小说前两步 load_skill:\n"
            "  1. load_skill novel-common    (通用 9 大常见坑 + 触发清单)\n"
            "  2. load_skill novel-{题材}    (21 选 1)\n"
            "     番茄爽文→novel-xuanhuan/novel-xiuzhen/...\n"
            "     玄幻 novel-xuanhuan / 修真 novel-xiuzhen / 武侠 novel-wuxia /\n"
            "     都市 novel-urban / 诡异 novel-guiyi / 科幻 novel-scifi /\n"
            "     言情 novel-yanqing / 正史 novel-lishi / 架空史 novel-lishi-jiakong /\n"
            "     末世 novel-moshi / 电竞 novel-dianjing / 种田 novel-zhongtian /\n"
            "     系统流 novel-xitongliu / 无限流 novel-wuxianliu /\n"
            "     穿越 novel-chuanyue / 重生 novel-zhongsheng /\n"
            "     宫斗 novel-gongdou / 悬疑推理 novel-xuanyi /\n"
            "     军事 novel-junshi / 轻小说 novel-qingxiao / 西幻 novel-xifan\n"
            "  3. 长篇 (> 20 章) 同时 load_skill long-novel, 走工程化架构\n"
            "  4. 写完 load_skill anti-ai-tell-audit + 跑审查, 不合格重写\n\n"
            "报告/论文类: load_skill deep-report, 流程不同 (边查边写 + 引用密度).\n\n"
            "## 输出格式\n"
            "每章完成后输出：文件路径 + 章节摘要（一句话）+ 中文字数\n"
        ),
        # [v1.1] writer 扩权: 写作中可以搜资料 + 识别引用图片
        # 场景: 都市/重生题材提到真实年份/人物/事件时必须 web_search 确认
        # 报告类任务必须边搜边写; 带引用图片的文档需要 vision_ocr
        "allowed": {"write_file", "read_file", "execute_shell", "patch_file",
                     "update_global_context",
                     "web_search", "web_fetch", "browser_search", "browser_read",
                     "vision_ocr",
                     "load_skill", "list_skills"},
        "max_iter": 50,
    },

    # ── v1.0 新增 ──
    "critic": {
        "system": (
            "你是代码/内容审查专家（Critic Agent）。你的唯一职责是找错误。\n\n"
            "审查维度:\n"
            "1. 正确性: 变量是否定义、函数调用参数是否正确、逻辑是否有 bug\n"
            "2. 安全性: SQL注入/XSS/硬编码密码/不安全的依赖\n"
            "3. 一致性: 不同文件间的接口是否匹配、路径是否一致\n"
            "4. 完整性: 是否有未实现的占位函数、缺失的错误处理\n"
            "5. 依赖性: 是否有未安装的依赖、版本冲突\n\n"
            "输出格式:\n"
            "- ✅ [检查项]: 通过（简要说明）\n"
            "- ❌ [检查项]: 问题描述 → 修复建议\n"
            "- ⚠️ [检查项]: 优化建议\n\n"
            "如果发现严重问题，在最后写: [CRITICAL: 问题描述]\n"
            "你只负责审查，不需要修改代码。"
        ),
        "allowed": {"read_file", "execute_shell", "search_code", "get_tree"},
        "max_iter": 10,  # [v1.0] critic 5 轮常不够读完大项目, 提升到 10
    },
    # [#3 2026-09-05] 视觉理解角色: 看图 + 推理决策 (配合决策节点做"看图→分支")
    "vision": {
        "system": (
            "你是视觉理解专家（Vision Agent）。你能看图: 用 vision_ocr 对图片做 OCR, "
            "或按要求描述/判断图像内容（文字、表格、界面截图、图表、实物）。\n"
            "流程: 1) 先确认图片路径（find_files / execute_shell 找）; 2) 用 vision_ocr 识别; "
            "3) 基于识别结果给出结论或结构化数据。\n"
            "输出: 识别到的关键信息 + 你的判断。若下游要按你的结果走条件分支, "
            "请用明确的关键词或 JSON（例: {\"has_error\": true, \"amount\": 128}）方便后续 when 取值。"
        ),
        "allowed": {"vision_ocr", "read_file", "execute_shell", "find_files", "get_tree"},
    },
}


# ═══════════════════════════════════════════════════════
# [v1.0.4] PROJECT_MAP 摘要注入
# ═══════════════════════════════════════════════════════
_PROJECT_MAP_SIZE_HINT = 0  # 记录下次提示用

def _find_project_map() -> Optional["Path"]:
    """找 WORKSPACE 下最近改过的 PROJECT_MAP.md (大项目可能嵌一层目录)."""
    from pathlib import Path as _Path
    candidates = []
    root = _Path(WORKSPACE)
    if not root.exists():
        return None
    # 根目录优先
    p = root / "PROJECT_MAP.md"
    if p.exists():
        candidates.append((p.stat().st_mtime, p))
    # 一层子目录
    for sub in root.iterdir():
        if sub.is_dir() and not sub.name.startswith("."):
            pp = sub / "PROJECT_MAP.md"
            if pp.exists():
                candidates.append((pp.stat().st_mtime, pp))
    if not candidates:
        return None
    return sorted(candidates, reverse=True)[0][1]


def _load_project_map_header(max_chars: int = 2500) -> str:
    """
    从 PROJECT_MAP.md 抽关键段落 (目录树 + API 合约 + 函数索引 + 类索引 前若干行) 注入子代理。
    全量文件可能很大 (几十 KB), 只塞关键结构, 让子代理知道项目长啥样。
    """
    global _PROJECT_MAP_SIZE_HINT
    p = _find_project_map()
    if not p:
        return ""
    try:
        raw = p.read_text(errors="replace")
        _PROJECT_MAP_SIZE_HINT = len(raw)
    except Exception:
        return ""
    # 抽: 前 30 行 (概述/目录树) + "## API" 表 + "## 函数索引" 表 + "## 类索引" 表
    lines = raw.splitlines()
    out = lines[:30]
    for section in ("## API 合约", "## API", "## 函数索引", "## 类索引",
                     "## 路由/接口", "## Modules"):
        try:
            start = next(i for i, l in enumerate(lines) if l.strip().startswith(section))
        except StopIteration:
            continue
        end = start + 1
        # 收到下一个 ## 或 25 行为止
        while end < len(lines) and end - start < 25:
            if lines[end].startswith("## ") and end > start:
                break
            end += 1
        out.append("")
        out.extend(lines[start:end])
        if sum(len(x) for x in out) > max_chars:
            break
    header = "\n".join(out)
    if len(header) > max_chars:
        header = header[:max_chars] + "\n...[PROJECT_MAP 摘要截断, 需全貌用 read_file 读]"
    return header


# ═══════════════════════════════════════════════════════
# 滚动摘要（Rolling Summary）
# ═══════════════════════════════════════════════════════

def _rolling_summary(messages: list, keep_recent: int = 4) -> list:
    """
    v1.0: 当消息数 > 阈值时，对旧消息做规则摘要压缩。
    不调用 LLM，纯规则提取，零延迟。
    """
    # 保留 system + 最近 keep_recent 条
    if len(messages) <= keep_recent + 2:
        return messages

    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    if len(non_system) <= keep_recent:
        return messages

    old = non_system[:-keep_recent]
    recent = non_system[-keep_recent:]

    # 提取旧消息摘要
    summary_parts = []
    for m in old:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, str):
            if role == "user":
                summary_parts.append(f"[User] {content[:80]}")
            elif role == "assistant":
                # 提取关键信息
                if "write_file" in content or "✓" in content or "✗" in content:
                    summary_parts.append(f"[Action] {content[:100]}")
            elif role == "tool":
                if content.startswith("ERROR"):
                    summary_parts.append(f"[Error] {content[:100]}")
                elif len(content) > 200:
                    summary_parts.append(f"[Result] {content[:60]}...")

    if summary_parts:
        summary_msg = {
            "role": "user",
            "content": (
                "[SYSTEM: 以下是前几轮操作的摘要，帮助你了解上下文。"
                "详细信息已压缩，请基于最近的消息继续工作。]\n\n"
                + "\n".join(summary_parts[-15:])  # 最多保留 15 条摘要
            ),
        }
        return system_msgs + [summary_msg] + recent

    return system_msgs + recent


# ═══════════════════════════════════════════════════════
# 错误栈构建（用于弹回 Supervisor）
# ═══════════════════════════════════════════════════════

def _build_error_stack(messages: list, agent_type: str,
                       task: str, max_errors: int = 5) -> str:
    """
    从子 Agent 消息历史中提取最近的错误栈，
    结构化返回给 Supervisor 做重调度决策。
    """
    errors = []
    for m in reversed(messages):
        content = m.get("content", "")
        if isinstance(content, str) and content.startswith("ERROR"):
            errors.append(content[:200])
            if len(errors) >= max_errors:
                break

    if not errors:
        return ""

    return (
        f"[子代理错误栈] agent_type={agent_type}\n"
        f"任务: {task[:150]}\n"
        f"最近 {len(errors)} 个错误:\n"
        + "\n".join(f"  {i+1}. {e}" for i, e in enumerate(reversed(errors)))
        + "\n建议: 检查是否需要换策略或拆分任务。"
    )


# ═══════════════════════════════════════════════════════
# 核心运行器
# ═══════════════════════════════════════════════════════

# 前向引用: execute_tool 和 _parse_text_tool_calls 从 server 注入
# 避免循环导入
execute_tool = None
_parse_text_tool_calls = None
MAX_ERROR_STREAK = 5


def set_execute_tool(fn):
    """[v1.0] 由 server 启动时调用，注入工具执行回调"""
    global execute_tool
    execute_tool = fn

def set_parse_text_tool_calls(fn):
    """[v1.0] 由 server 启动时调用，注入 text-format tool call 解析器"""
    global _parse_text_tool_calls
    _parse_text_tool_calls = fn

# 黑板引用（由 orchestrator 或 multi_agent 注入）
_active_blackboard = None


def set_blackboard(bb):
    """设置当前活跃的黑板引用。"""
    global _active_blackboard
    _active_blackboard = bb


def _sanitize_history(messages: list):
    """清理 messages 中可能导致 400 的格式问题。"""
    for m in messages:
        if m.get("role") == "assistant" and "tool_calls" in m:
            for tc in m.get("tool_calls", []):
                fn = tc.get("function", {})
                args = fn.get("arguments", "")
                if isinstance(args, str):
                    args = args.strip()
                    if not args:
                        fn["arguments"] = "{}"
                    else:
                        try:
                            json.loads(args)
                        except Exception:
                            fn["arguments"] = "{}"


def _clear_old_tool_results(messages: list, keep_recent: int = 6):
    """清理旧 tool result，防止 context 爆炸。"""
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) <= keep_recent:
        return
    for idx in tool_indices[:-keep_recent]:
        content = messages[idx].get("content") or ""
        if isinstance(content, str) and len(content) > 200:
            messages[idx]["content"] = f"[cleared: {len(content)}c] {content[:80]}..."


async def _run_subagent(task: str, agent_type: str, context: str = "",
                       sse_emit: callable = None,
                       parent_sid: str = None) -> str:
    """
    运行聚焦子智能体。v1.0 增强:
    - 滚动摘要: 每 8 轮压缩历史
    - 黑板集成: update_global_context 工具
    - 错误栈弹回: 超限时结构化返回
    - [v1.0] parent_sid: 父 session 中断时子代理同步退出
    """
    # [v1.0] 父会话中断传播: 每轮开始前先检查父 session 是否被中断
    try:
        from lib.session import check_interrupt as _check_parent_interrupt
    except Exception:
        _check_parent_interrupt = None
    cfg = _SUBAGENT_CONFIGS.get(agent_type, _SUBAGENT_CONFIGS["coder"])
    allowed = cfg["allowed"]
    sa_tools = [t for t in TOOL_DEFS if t["function"]["name"] in allowed]

    # v1.0: 如果黑板工具在 allowed 中，添加工具定义
    if "update_global_context" in allowed and _active_blackboard:
        from blackboard import BLACKBOARD_TOOL_DEF
        # 检查是否已在 sa_tools 中
        if not any(t["function"]["name"] == "update_global_context" for t in sa_tools):
            sa_tools.append(BLACKBOARD_TOOL_DEF)

    sys_prompt = cfg["system"]
    # [v1.0] 所有子代理（除 critic 外）继承主 agent 的铁律
    # critic 的职责是找错，不需要执行约束
    if agent_type != "critic":
        sys_prompt += _SUBAGENT_IRON_RULES
    if context:
        sys_prompt += f"\n\n## 上下文（来自主智能体）\n{context}"

    # [v1.0.4] PROJECT_MAP 注入: 大项目的子代理开工前看到项目索引
    # 避免子代理盲目 get_tree/find_files, 直接从 map 里定位
    if agent_type in {"coder", "analyst", "explorer", "tester", "critic"}:
        _map_header = _load_project_map_header()
        if _map_header:
            sys_prompt += (
                "\n\n## 项目地图 (PROJECT_MAP.md 摘要, 由 AST watcher 自动维护)\n"
                f"{_map_header}\n"
                "**导航优先级**:\n"
                "1. 要找某函数/类 → 先看上面的'函数索引'/'类索引', 定位到 `relpath`\n"
                "2. 要改已有代码 → read_file 该文件完整内容, 再 patch_file\n"
                "3. 新写源文件 → 写完后必须调 update_map 注册到 PROJECT_MAP\n"
                "4. 完整地图: read_file('PROJECT_MAP.md') (大约 "
                f"{_PROJECT_MAP_SIZE_HINT} 字节, 按需读)"
            )

    # [v1.0] 为子代理生成虚拟 sid, 这样它启动的 bg 进程可独立追踪/清理
    _subagent_sid = f"subagent_{agent_type}_{uuid.uuid4().hex[:8]}"

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": task},
    ]

    log.info(f"  \033[33m[subagent:{agent_type}]\033[0m {task[:80]}")
    final_text = ""
    error_streak = 0

    # [loop-detect-2026-05] 工具调用循环检测 — 防 writer 反复写同一章 / coder 反复改同一文件
    # write_file/patch_file 同一 path 调用 >= 3 次 (容许 2 次, 第 3 次必停) → 强制 break
    # 其它工具同 args json 前 200 字符 >= 3 次同理
    from collections import deque as _deque
    _recent_tool_sigs: _deque = _deque(maxlen=8)
    _sig_counter: dict = {}

    def _tool_call_sig(fn_name: str, fn_args: dict):
        path = fn_args.get("filepath") or fn_args.get("path") or fn_args.get("file") or ""
        if path and fn_name in ("write_file", "patch_file", "create_file"):
            return (fn_name, str(path))
        try:
            return (fn_name, json.dumps(fn_args, sort_keys=True, ensure_ascii=False)[:200])
        except Exception:
            return (fn_name, str(fn_args)[:200])

    max_iter = cfg.get("max_iter", 20)  # [v1.0] 默认 12 太紧, 提升到 20
    for _iter in range(max_iter):
        # [v1.0] 每轮开始检查父 session 中断标记, 立即退出
        if parent_sid and _check_parent_interrupt and _check_parent_interrupt(parent_sid):
            log.warning(f"  [subagent:{agent_type}] parent sid={parent_sid[:8]} interrupted, aborting at iter={_iter+1}")
            await _cleanup_subagent_bg(_subagent_sid, agent_type)
            return f"[子代理已中断] 父会话 {parent_sid[:8]} 被用户中断，子代理 {agent_type} 于 iter={_iter+1} 停止。"

        # [v1.0] 每轮开始打日志, 让用户知道子代理在哪一步（防止"卡死"错觉）
        import time as _time
        _iter_t0 = _time.time()
        log.info(f"  \033[2m[subagent:{agent_type}:iter={_iter+1}/{max_iter}] start\033[0m")

        # v1.0: 滚动摘要 — 每 8 轮压缩
        if _iter > 0 and _iter % 8 == 0:
            messages = _rolling_summary(messages, keep_recent=6)

        # 清理旧 tool result
        if _iter > 0 and _iter % 4 == 0:
            _clear_old_tool_results(messages, keep_recent=6)

        full_text = ""
        acc_tools: dict = {}

        _sanitize_history(messages)

        async for chunk in _vllm_stream(messages, sa_tools):
            choices = chunk.get("choices", [])
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta", {})
            if delta.get("content"):
                full_text += delta["content"]
            for tc in delta.get("tool_calls", []):
                idx = tc.get("index", 0)
                if idx not in acc_tools:
                    acc_tools[idx] = {
                        "id": tc.get("id", f"sa_{uuid.uuid4().hex[:8]}"),
                        "name": "", "arguments": "",
                    }
                fn = tc.get("function", {})
                if fn.get("name"):
                    acc_tools[idx]["name"] = fn["name"]
                if tc.get("id"):
                    acc_tools[idx]["id"] = tc["id"]
                acc_tools[idx]["arguments"] += fn.get("arguments") or ""

        # text-format fallback
        if not acc_tools and full_text and _parse_text_tool_calls:
            for i, tc in enumerate(_parse_text_tool_calls(full_text)):
                if tc["name"] in allowed:
                    acc_tools[i] = tc

        # [v1.0] 防御性剥离 <think>...</think> — 万一模型没有 parser 把思考塞在 content 里
        # 子代理不需要看自己的思考，内部逻辑只需要最终答案
        # [v1.2] 剥之前先存一份, 万一剥完空了 → 兜底
        _pre_strip_full = full_text
        if full_text and ("<think>" in full_text or "</think>" in full_text):
            import re as _re_th
            full_text = _re_th.sub(r'<think>.*?</think>', '', full_text, flags=_re_th.DOTALL).strip()

        if not acc_tools:
            # [v1.2] 链路兜底: 模型本轮既无工具调用也无正文 → 不能让下游拿到空串
            # 优先级 1) 历史里最近的 tool 结果 (含真实数据)
            #        2) 剥掉的 <think> 思考内容 (模型推理结论)
            #        3) 显式标记空回复, 让上游知道节点跑了但 LLM 没答
            if not full_text:
                _tool_outs: List[str] = []
                for _m in reversed(messages):
                    if _m.get("role") == "tool" and _m.get("content"):
                        _tool_outs.append(str(_m["content"]))
                        if len(_tool_outs) >= 3:
                            break
                if _tool_outs:
                    full_text = (
                        f"[{agent_type} 未生成答案文本, 自动兜底返回最近工具输出供下游使用]\n\n"
                        + "\n---\n".join(reversed(_tool_outs))
                    )[:6000]
                elif _pre_strip_full:
                    import re as _re_th
                    _think = _re_th.search(r'<think>(.*?)</think>', _pre_strip_full,
                                           flags=_re_th.DOTALL)
                    if _think:
                        full_text = (
                            f"[{agent_type} 仅输出思考链未给正式答案, 兜底返回思考内容]\n\n"
                            + _think.group(1).strip()
                        )[:6000]
                if not full_text:
                    full_text = (
                        f"[{agent_type} 第 {_iter+1} 轮: LLM 返回空 content 且无 tool_call, "
                        f"历史也无 tool 结果可兜底 — 任务可能过于抽象/工具不足]"
                    )
                log.warning(f"  [subagent:{agent_type}] empty final_text fallback "
                            f"@iter={_iter+1}, 用兜底文本 {len(full_text)} 字")
            final_text = full_text
            break

        def _sj(s):
            s = (s or "").strip()
            if not s:
                return "{}"
            try:
                json.loads(s)
                return s
            except Exception:
                return "{}"

        asst_msg = {
            "role": "assistant", "content": full_text or None,
            "tool_calls": [
                {"id": acc_tools[i]["id"], "type": "function",
                 "function": {"name": acc_tools[i]["name"],
                              "arguments": _sj(acc_tools[i]["arguments"])}}
                for i in sorted(acc_tools)
            ],
        }
        messages.append(asst_msg)

        # [v1.0] 执行工具前再次检查父中断标记
        if parent_sid and _check_parent_interrupt and _check_parent_interrupt(parent_sid):
            log.warning(f"  [subagent:{agent_type}] parent sid={parent_sid[:8]} interrupted before tool exec, aborting")
            await _cleanup_subagent_bg(_subagent_sid, agent_type)
            return f"[子代理已中断] 父会话 {parent_sid[:8]} 被用户中断，工具执行前停止。"

        tool_results = []
        _loop_break_sig = None
        _loop_break_count = 0
        for i in sorted(acc_tools):
            tc = acc_tools[i]
            try:
                fn_args = json.loads(tc["arguments"] or "{}")
            except Exception:
                fn_args = {}

            fn_name = tc["name"]

            # [loop-detect-2026-05] 循环检测: 同一签名 (tool, path/args) 在最近 8 次调用里
            # 已出现 ≥ 3 次 → 这次是第 4 次, 强制 break. 给 LLM 3 次机会 (写/改/重写),
            # 第 4 次还来就是死循环 (典型: writer 重写 ch01 13 次).
            _sig = _tool_call_sig(fn_name, fn_args)
            _already = _recent_tool_sigs.count(_sig)
            if _already >= 3:
                _loop_break_sig = _sig
                _loop_break_count = _already + 1
                break
            _recent_tool_sigs.append(_sig)

            detail_str = f"[{agent_type}] {fn_name}: {str(fn_args)[:80]}"
            log.info(f"    \033[2m{detail_str}\033[0m")

            if sse_emit:
                await sse_emit(sse_status("task_exec", {
                    "status": "executing", "agent": agent_type, "detail": detail_str,
                }))

            # v1.0: 黑板工具特殊处理
            if fn_name == "update_global_context" and _active_blackboard:
                key = fn_args.get("key", "")
                value = fn_args.get("value", "")
                entry_type = fn_args.get("type", "context")
                _active_blackboard.put(key, value, source=agent_type)
                result = f"✅ 已写入黑板: {key}"
            elif execute_tool:
                try:
                    result, _ = await execute_tool(fn_name, fn_args, _subagent_sid)
                except Exception as _tool_exc:
                    result = f"ERROR: tool execution failed: {_tool_exc}"
            else:
                result = f"ERROR: execute_tool not available for {fn_name}"

            # 截断
            if len(result) > 3000:
                result = result[:600] + f"\n...[subagent truncated {len(result)}c]...\n" + result[-800:]

            if sse_emit:
                preview = result[:150].replace("\n", " ")
                await sse_emit(sse_status("task_exec", {
                    "status": "done", "agent": agent_type, "detail": preview,
                }))

            # 错误处理
            if result.startswith("ERROR"):
                error_streak += 1
                if error_streak < MAX_ERROR_STREAK:
                    result += (
                        "\n\n[SYSTEM: 这是一个错误，不是终止信号。"
                        "分析根因，制定新方案，继续执行任务。"
                        "禁止输出'无法继续'或等待用户指令。]"
                    )
                else:
                    # v1.0: 错误栈弹回 — 超过阈值时构建结构化错误栈
                    error_stack = _build_error_stack(messages, agent_type, task)
                    log.warning(f"  [subagent:{agent_type}] error_streak={error_streak}, bubbling error stack")
                    # [v1.0] 子代理出错退出前也清理它启动的 bg 进程
                    await _cleanup_subagent_bg(_subagent_sid, agent_type)
                    return error_stack or f"[子代理错误栈] {agent_type}: 连续 {error_streak} 次错误，已停止。"
            else:
                error_streak = 0

            tool_results.append({
                "role": "tool", "tool_call_id": tc["id"], "content": result,
            })
        messages.extend(tool_results)

        # [loop-detect-2026-05] 内层 for 触发循环, 跳出外层 _iter
        if _loop_break_sig is not None:
            log.warning(
                f"  [subagent:{agent_type}] LOOP DETECTED @iter={_iter+1}: "
                f"{_loop_break_sig[0]}({str(_loop_break_sig[1])[:80]}) called "
                f"{_loop_break_count} times in last 8 → force break"
            )
            final_text = (
                f"[loop-detect 子代理强制停止] {agent_type}: "
                f"工具 {_loop_break_sig[0]}({str(_loop_break_sig[1])[:120]}) "
                f"在最近 8 次调用里已出现 {_loop_break_count} 次, 判定为死循环, 强制终止. "
                f"已执行 {_iter + 1} 轮, 已收集工具结果 {len(tool_results)} 个 (见上文). "
                f"建议: 检查任务是否已完成 / 工具结果是否被正确读取 / 改换策略后再 spawn."
            )
            break

    log.info(f"  \033[33m[subagent:{agent_type}] done\033[0m {final_text[:80]}")

    # [v1.0] 子代理结束: 清理它启动的非 keep_alive bg 进程
    # 例: coder 启动 uvicorn 做测试, 没主动 kill -> 由这里兜底清掉
    await _cleanup_subagent_bg(_subagent_sid, agent_type)

    # v1.0: max_iter 耗尽时也返回错误栈
    if not final_text and _iter == max_iter - 1:
        error_stack = _build_error_stack(messages, agent_type, task)
        return error_stack or "(subagent exhausted max iterations)"

    return final_text or "(subagent returned no text)"


async def _cleanup_subagent_bg(subagent_sid: str, agent_type: str):
    """[v1.0] 子代理退出时, 清理它启动的非 keep_alive bg 进程"""
    try:
        from tool_dispatch import cleanup_session_bg
        killed, cmds = await cleanup_session_bg(subagent_sid, force=False)
        if killed > 0:
            log.info(f"  [subagent:{agent_type}] cleaned {killed} bg procs: {cmds[:3]}")
    except Exception as e:
        log.warning(f"  [subagent:{agent_type}] bg cleanup skipped: {e}")


import hashlib as _hashlib

_SUBAGENT_DEPTH: _cvar.ContextVar[int] = _cvar.ContextVar('subagent_depth', default=0)
_SUBAGENT_MAX_DEPTH = 2

_SUBAGENT_CACHE: Dict[str, Tuple[float, str]] = {}
_SUBAGENT_CACHE_TTL = 3600
_SUBAGENT_CACHE_MAX = 200


def _subagent_cache_key(agent_type: str, task: str, context: str) -> str:
    h = _hashlib.sha256()
    h.update(agent_type.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update(task.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update(context.encode("utf-8", errors="replace"))
    return h.hexdigest()[:16]


def _subagent_cache_get(key: str) -> Optional[str]:
    entry = _SUBAGENT_CACHE.get(key)
    if not entry:
        return None
    ts, result = entry
    if time.time() - ts > _SUBAGENT_CACHE_TTL:
        _SUBAGENT_CACHE.pop(key, None)
        return None
    return result


def _subagent_cache_put(key: str, result: str) -> None:
    if len(_SUBAGENT_CACHE) >= _SUBAGENT_CACHE_MAX:
        oldest = min(_SUBAGENT_CACHE.keys(), key=lambda k: _SUBAGENT_CACHE[k][0])
        _SUBAGENT_CACHE.pop(oldest, None)
    _SUBAGENT_CACHE[key] = (time.time(), result)


def _emit_subagent_telemetry(agent_type, task, status, duration_ms, result_len, error=""):
    """v1.8 P36-c: 走 core/telemetry.py 统一入口；业务字段保留。"""
    try:
        import hashlib
        task_hash = hashlib.sha256(task.encode("utf-8", errors="replace")).hexdigest()[:12]
        fields = {
            "agent_type": agent_type,
            "task_len": len(task),
            "task_hash": task_hash,
            "task_preview": task[:80].replace("\n", " "),
            "status": status,
            "duration_ms": duration_ms,  # 旧字段保留
            "result_len": result_len,
            "error": error[:200] if error else "",
        }
        try:
            from core.telemetry import emit as _emit
            _emit(
                event=f"subagent_{status}",
                fields=fields,
                jsonl="subagent.jsonl",
                latency_ms=duration_ms,
            )
        except Exception:
            SUBAGENT_TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
            entry = {"ts": time.time(), **fields}
            with SUBAGENT_TELEMETRY_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[telemetry/subagent] write failed: {e}")


async def _run_subagent_safe(task: str, agent_type: str, context: str = "",
                            sse_emit: callable = None, timeout: int = 600,
                            parent_sid: str = None) -> str:
    """
    带超时和兜底的子代理调用。v1.0 增强: 超时时返回错误栈。
    [v1.0] parent_sid: 透传给 _run_subagent 以便子代理响应父中断
    """
    cur_depth = _SUBAGENT_DEPTH.get()
    if cur_depth >= _SUBAGENT_MAX_DEPTH:
        log.warning(f"  [subagent:{agent_type}] depth limit hit ({cur_depth} >= {_SUBAGENT_MAX_DEPTH}), refusing")
        _emit_subagent_telemetry(agent_type, task, "error", 0, 0, error="depth_limit")
        return f"[子代理嵌套过深] depth={cur_depth} >= {_SUBAGENT_MAX_DEPTH}, 拒绝执行避免无限递归. 请简化任务或在父代理中拆分."
    _token = _SUBAGENT_DEPTH.set(cur_depth + 1)
    try:
        _t0 = time.time()
        _cache_key = _subagent_cache_key(agent_type, task, context or "")
        _cached = _subagent_cache_get(_cache_key)
        if _cached is not None:
            log.info(f"  [subagent:{agent_type}] cache HIT (key {_cache_key[:8]})")
            _emit_subagent_telemetry(agent_type, task, "ok", 0, len(_cached), error="(cache)")
            return _cached
        try:
            result = await asyncio.wait_for(
                _run_subagent(task, agent_type, context, sse_emit, parent_sid=parent_sid),
                timeout=timeout,
            )
            _emit_subagent_telemetry(agent_type, task, "ok", (time.time() - _t0) * 1000, len(result))
            if result and not result.startswith("[子代理"):
                _subagent_cache_put(_cache_key, result)
            return result
        except asyncio.TimeoutError:
            log.warning(f"  [subagent:{agent_type}] TIMEOUT after {timeout}s")
            _emit_subagent_telemetry(agent_type, task, "timeout", (time.time() - _t0) * 1000, 0, error="超时")
            return (
                f"[子代理超时] {agent_type} 执行超过 {timeout}s 已停止。\n"
                f"任务: {task[:200]}\n"
                f"建议: 拆分为更小子任务，或增加超时。"
            )
        except Exception as e:
            log.error(f"  [subagent:{agent_type}] FATAL: {e}")
            _emit_subagent_telemetry(agent_type, task, "error", (time.time() - _t0) * 1000, 0, error=str(e))
            return (
                f"[子代理异常] {agent_type} 执行出错: {e}\n"
                f"任务: {task[:200]}\n"
                f"请用其他方式完成此任务。"
            )
    finally:
        _SUBAGENT_DEPTH.reset(_token)
