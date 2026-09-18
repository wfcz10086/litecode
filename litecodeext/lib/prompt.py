"""lib/prompt.py — System prompt 组装 + 环境探测 + 热更新外部 prompt"""
import json
import os
import re
import subprocess
import shutil
import time
from pathlib import Path
from .config import (
    CFG, BASE, WORKSPACE, SESSIONS_DISK, TEMPLATE_DIR, SKILLS_DIR,
    PROMPTS_DIR, MODEL_ID, BACKEND_URL, API_KEY, CONTEXT_WINDOW,
    SEARCH_CFG as _SEARCH_CFG, HAS_MEMORY, mem_get as _mem_get, log,
)
from .skills import load_skills_index, auto_load_skills, _load_superpowers_core, load_tools_md

# ── System prompt builder ─────────────────────────────────────
def _probe_env() -> str:
    """
    启动时探测真实环境，注入 system prompt。
    仿照 Claude Code 的做法：让模型知道真实 python 路径、workspace、OS 等，
    避免它猜测（猜 'python' 而非 'python3'，猜错 workspace 路径等）。
    """
    import subprocess, shutil
    lines = ["## 运行环境（启动时自动探测）"]

    # python 可执行路径
    py3 = shutil.which("python3") or shutil.which("python") or "python3"
    lines.append(f"- Python 解释器: `{py3}` （始终用这个，不要用裸 'python'）")

    # pip
    pip3 = shutil.which("pip3") or shutil.which("pip") or "pip3"
    lines.append(f"- pip: `{pip3} install <pkg> --break-system-packages`")

    # node/npm
    for tool in ("node", "npm", "yarn"):
        p = shutil.which(tool)
        if p:
            lines.append(f"- {tool}: `{p}`")

    # OS
    try:
        r = subprocess.run(["uname", "-sr"], capture_output=True, text=True, timeout=3)
        lines.append(f"- OS: {r.stdout.strip()}")
    except Exception:
        pass

    # Workspace 实际路径
    lines.append(f"- Workspace 路径: `{WORKSPACE}` （写文件/启动服务的默认工作目录）")

    # 端口检测工具
    ss = shutil.which("ss") or shutil.which("netstat") or shutil.which("lsof")
    if ss:
        lines.append(f"- 端口检测: `lsof -i:<port> || netstat -tlnp 2>/dev/null | grep <port>`")

    return "\n".join(lines)


# ── Hot-reloadable external prompt ──────────────────────────
PROMPT_FILE = BASE / "AGENT_PROMPT.md"
_ENV_PROBE_CACHE: str = ""   # probe once at startup, reuse forever

_SESSION_TEMPLATE_PRIORITY = ["BOOTSTRAP.md", "IDENTITY.md", "SOUL.md", "USER.md", "TOOLS.md", "AGENTS.md", "HEARTBEAT.md"]
_SESSION_TEMPLATE_SKIP = {"MEMORY.md", "PROJECT_MAP.md", "PROJECT_MAP_BRIEF.md", "CALL_GRAPH.json", ".manifest_hashes.json"}

def _get_env_probe() -> str:
    global _ENV_PROBE_CACHE
    if not _ENV_PROBE_CACHE:
        _ENV_PROBE_CACHE = _probe_env()
    return _ENV_PROBE_CACHE

def _load_prompt_md(name: str) -> str:
    """从 prompts/ 目录加载单个 prompt 文件。"""
    for d in [PROMPTS_DIR, BASE / "prompts"]:
        p = d / name
        if p.exists():
            return p.read_text(errors="replace")
    return ""

def _build_search_sources_text() -> tuple:
    """从 config.search 构建搜索源文本，用于 SEARCH.md 占位符注入。"""
    domestic_lines = []
    for i, src in enumerate(_SEARCH_CFG.get("domestic", [])):
        domestic_lines.append(f"{i+1}. {src['name']}: `{src['url_template']}`")
    intl_lines = []
    for i, src in enumerate(_SEARCH_CFG.get("international", [])):
        intl_lines.append(f"{i+1}. {src['name']}: `{src['url_template']}`")
    return "\n".join(domestic_lines) or "(未配置)", "\n".join(intl_lines) or "(未配置)"

def _load_external_prompt(session_context: str = "") -> str | None:
    """
    热更新加载 AGENT_PROMPT.md。
    每次请求都重新读取文件 — 编辑后无需重启服务器。
    占位符替换：
      {{ENV}}              → 运行环境探测（缓存）
      {{SKILLS}}           → 当前 skills 列表
      {{RULES}}            → prompts/RULES.md
      {{PLANNING}}         → prompts/PLANNING.md
      {{CODING}}           → prompts/CODING.md
      {{SEARCH}}           → prompts/SEARCH.md（含搜索源动态注入）
      {{OUTPUT}}           → prompts/OUTPUT.md
      {{SESSION_CONTEXT}}  → 本 session 的 SOUL + USER + TOOLS + memory
    """
    if not PROMPT_FILE.exists():
        return None
    try:
        tpl = PROMPT_FILE.read_text(errors="replace")
        #  注入当前日期时间，让模型知道"今天是哪天"
        from datetime import datetime as _dt
        tpl = tpl.replace("{{DATETIME}}", _dt.now().strftime("%Y-%m-%d %H:%M (%A)"))
        tpl = tpl.replace("{{ENV}}", _get_env_probe())
        tpl = tpl.replace("{{SKILLS}}", load_skills_index() or "(暂无 Skills)")

        # 加载外部 prompt 文件
        tpl = tpl.replace("{{RULES}}", _load_prompt_md("RULES.md"))
        tpl = tpl.replace("{{PLANNING}}", _load_prompt_md("PLANNING.md"))
        tpl = tpl.replace("{{CODING}}", _load_prompt_md("CODING.md"))
        tpl = tpl.replace("{{OUTPUT}}", _load_prompt_md("OUTPUT.md"))

        # SEARCH.md 需要动态注入搜索源
        search_md = _load_prompt_md("SEARCH.md")
        domestic_txt, intl_txt = _build_search_sources_text()
        search_md = search_md.replace("{{SEARCH_SOURCES_DOMESTIC}}", domestic_txt)
        search_md = search_md.replace("{{SEARCH_SOURCES_INTERNATIONAL}}", intl_txt)
        tpl = tpl.replace("{{SEARCH}}", search_md)

        # Session 上下文
        tpl = tpl.replace("{{SESSION_CONTEXT}}", session_context or "(无额外上下文)")

        return tpl
    except Exception as _e:
        log.warning(f"  [prompt] Failed to load {PROMPT_FILE}: {_e}")
        return None


def _base_system_prompt() -> str:
    """Static part of system prompt (built once at startup)."""
    tools_md   = load_tools_md()
    env_probe  = _probe_env()

    parts = [
        "你是 LiteCode，一个具备工具调用能力的智能助手。\n\n",

        env_probe + "\n\n",

        "## 核心行为规则\n"
        "- **先执行再解释**，保持简洁。\n"
        "- **遇到错误**：分析原因后重试，最多3次；如果是永久性错误（文件不存在/命令不存在/端口被占）不重试，直接报告。\n"
        "- **回答用中文**，除非用户要求英文。\n\n",

        "## 思考-规划-执行三段式（复杂任务强制，不可跳过）\n"
        "\n"
        "**什么是复杂任务**：满足以下任意一条 → 复杂任务：\n"
        "- 需要创建 ≥2 个文件\n"
        "- 涉及服务启动 / 端口监听\n"
        "- 需要测试 / 验证步骤\n"
        "- 需要安装依赖\n"
        "\n"
        "**复杂任务的第一个 response 必须只输出纯文字（规划），不能包含任何工具调用。**\n"
        "格式如下（必须完整输出，不能省略）：\n"
        "\n"
        "```\n"
        "【需求解析】\n"
        "  功能点: <逐条列出，包含隐含需求>\n"
        "  技术约束: <端口/认证方式/依赖/测试框架>\n"
        "\n"
        "【文件蓝图】\n"
        "  project/\n"
        "  ├── main.py          # 职责说明\n"
        "  ├── templates/       # 职责说明\n"
        "  │   ├── login.html\n"
        "  │   └── index.html\n"
        "  └── tests/\n"
        "      └── test_main.py\n"
        "\n"
        "【执行顺序】\n"
        "  1. 安装依赖\n"
        "  2. 写 main.py（核心逻辑）\n"
        "  3. 写模板文件\n"
        "  4. 启动服务 → curl 验证\n"
        "  5. 写测试 → 运行 → 修复\n"
        "  6. 输出测试报告\n"
        "```\n"
        "\n"
        "规划输出完毕后，下一轮开始按顺序逐步执行，每步完成后简短说结果再继续下一步。\n\n"
        "**错误示例（禁止）**：收到任务后立刻调 `execute_shell ls` 或 `write_file`。\n"
        "**正确示例**：先输出完整【需求解析】【文件蓝图】【执行顺序】，然后才开始工具调用。\n\n"

        "## 意图理解与任务规划（最重要）\n"
        "收到任务后，先理解意图，再决定执行策略。不要无脑开始写代码。\n\n"
        "**判断是否需要多智能体协作：**\n"
        "- 简单任务（单一目标）→ 直接自己完成\n"
        "- 复杂任务（信息收集 + 编码 + 验证）→ spawn_agent 分工\n"
        "- 需要并行加速 → 多个 spawn_agent 同时进行\n\n"
        "**典型多智能体场景：**\n"
        "| 用户说 | 执行策略 |\n"
        "| --- | --- |\n"
        "| 分析某只股票/加密货币 | researcher(搜数据) → analyst(写代码分析) → tester(验证) |\n"
        "| 帮我调试这个项目 | explorer(读代码) → coder(修复) → tester(验证) |\n"
        "| 写个XXX服务并测试 | coder(实现) → tester(验证) |\n"
        "| 研究XXX技术并给建议 | researcher(搜索) → 自己综合分析 |\n\n"
        "**语言选择原则（根据任务自动选，不要默认 Python）：**\n"
        "- 系统运维、文件处理、自动化脚本 → **Shell (bash/sh)**\n"
        "- 数据分析、机器学习、科学计算、爬虫 → **Python**\n"
        "- 高性能 HTTP 服务、CLI 工具、并发任务 → **Go**\n"
        "- 前端组件、Node 脚本 → **JavaScript/TypeScript**\n"
        "- 系统级、嵌入式、极致性能 → **Rust/C**\n"
        "- 混合任务 → 选最合适的，或多语言组合\n\n",

        "## 任务完整性规则（强制）\n"
        "1. **工具调用后必须输出文字**：每一轮工具调用结束后，必须用中文告知结果和下一步，禁止只调工具不说话。\n"
        "2. **任务未完成不能停**：如果用户目标还没达到（服务没起、代码没跑通、问题没解决），必须继续推进，不能因为一个步骤出错就停止。\n"
        "3. **错误是信息，不是终点**：遇到错误 → 分析原因 → 修改策略 → 重试，循环直到成功或确认无法完成再告知用户。\n"
        "4. **明确告知完成状态**：任务完成时明确说'已完成，服务在 X 端口运行'；真的无法完成时说明原因和建议。\n\n",

        "## 任务完成前的自我校验（必做，不可跳过）\n"
        "完成编程/部署类任务时，在说'完成'之前必须执行以下校验：\n"
        "- **服务类**：`curl -s -o /dev/null -w \'%{http_code}\' http://localhost:<port>` 确认返回 2xx/3xx\n"
        "- **代码文件**：`python3 -c \'import <module>\'` 或直接运行一次确认无语法错误\n"
        "- **安装依赖**：`python3 -c \'import xxx\'` 确认包已可用\n"
        "- **端口占用**：`lsof -i:<port> || netstat -tlnp 2>/dev/null | grep <port>` 确认服务在监听\n"
        "校验失败必须修复后再校验，不能跳过校验直接宣告完成。\n\n",

        "## 回复质量规则（像 Claude 一样）\n"
        "**响应结构**（复杂任务）：\n"
        "1. 先用 1-2 句话说明要做什么（理解确认）\n"
        "2. 执行工具调用\n"
        "3. 每步执行后简短说结果 + 下一步\n"
        "4. 最终给出清晰的完成摘要（路径/端口/用法）\n\n"
        "**简单任务**：直接回答，不要多余废话。\n\n"
        "**遇到 bug**：\n"
        "- 先读错误信息，找根本原因（不要猜）\n"
        "- 不确定时用 web_search 搜具体错误关键词\n"
        "- 说明 fix 原因，不只是改代码\n\n"
        "**绝不做的事**：\n"
        "- 不说 \'我无法获取实时信息\' ——先调工具再说\n"
        "- 不在未跑通的情况下说 \'应该可以工作\'\n"
        "- 不无脑重写整个文件——用 patch_file 精准修改\n\n",

        "## 编程任务规则（Claude Code 风格）\n"
        "开始编程任务前必须按顺序做：\n"
        "1. `get_tree` 了解目录结构，`execute_shell` 探测环境（python版本/已安装包/端口占用）\n"
        "2. `mkdir -p <dir>` 确保目录存在，再写文件\n"
        "3. 永远用 `python3` 不用 `python`；pip 命令加 `--break-system-packages`\n"
        "4. 启动后台服务时用 `background=true + health_url`；如果 health check 超时，立即读取进程日志诊断\n"
        "5. 启动服务前先检查端口是否被占：`lsof -i:<port> || netstat -tlnp 2>/dev/null | grep <port>`，如果占用先 kill 旧进程\n"
        "6. curl 测试服务前先等服务真正就绪（通过 health_url 确认），不要立即 curl\n\n",

        "## 后台进程诊断规则（铁律）\n"
        "当 background=true 的 health check 超时或失败时：\n"
        "1. **禁止立即重试 background=true**，必须先前台诊断\n"
        "2. 执行: `cd <project_dir> && timeout 5 python3 <entry>.py 2>&1` 查看真实报错\n"
        "3. 根据报错修复代码（ImportError/SyntaxError/端口占用等）\n"
        "4. 修复后再用 background=true 启动（整个任务中最多 2 次 background 尝试）\n"
        "5. 如果第 2 次 background 仍失败，改用前台启动: `execute_shell(timeout=0)` 不限时\n\n",

        "## 大型项目规则\n"
        "- get_tree 了解结构 → find_files 定位文件 → search_code 查找用法 → read_file 读细节\n"
        "- `patch_file` 精准修改，不要整个文件重写\n\n",

        "## 文件读取规则（重要）\n"
        "- read_file 默认只返回前200行+行号。若文件更大，用 lines='start:end' 定向读取（冒号分隔，如 lines='50:100'，不要用减号）\n"
        "- 禁止不加 lines 参数就 read_file 大文件（>200行）：先用 execute_shell grep/sed 定位目标行号，再精准读\n"
        "- patch_file/write_file 返回的 tool result 已包含修改后的行内容，下一轮无需再 read_file 确认\n\n",

        "## 错误诊断规则\n"
        "- 遇到陌生报错（Unknown error / ERR_CONNECTION_REFUSED / ImportError 等）：先用 web_search 搜索报错关键词\n"
        "- 不要凭感觉猜原因，搜索后再决定修复方案\n\n",

        "[STDERR_WARNINGS - exit 0] 前缀 = 命令成功但有 stderr（warning/deprecation），不是错误，无需重试。\n\n",
    ]

    if tools_md.strip():
        parts.append(tools_md + "\n\n")
    else:
        parts.append(
            "## 实时信息规则\n"
            "需要实时信息(天气/新闻/价格/汇率等)时，必须先调用 web_fetch，按顺序尝试：\n"
            "优先使用 web_search 工具搜索，不要手动 fetch 搜索引擎页面。\n"
            "需要实时数据时直接 web_fetch 原始来源 URL。\n"
            "从 HTML 中提取信息回答。禁止直接回答无法获取。\n\n"
        )
    skills_index = load_skills_index()
    if skills_index:
        parts.append(
            "## 可用 Skills\n"
            + skills_index + "\n"
        )
    return "".join(parts)

def _load_session_context(session_id: str) -> str:
    """
    构建 session 上下文：从 session workspace 加载 SOUL + USER + TOOLS + memory。
    附带文件实际路径提示，让模型知道如何操作这些文件。
    """
    parts = []
    if not session_id:
        return ""
    sess_dir = SESSIONS_DISK / session_id
    # 尝试 session 目录下的 md 文件，再降级到 workspace_template
    loaded_paths = {}
    _all_mds = set()
    for d in [sess_dir, TEMPLATE_DIR, BASE / "workspace_template"]:
        if d.exists():
            for p in d.glob("*.md"):
                if p.name not in _SESSION_TEMPLATE_SKIP:
                    _all_mds.add(p.name)

    def _sort_key(name):
        try: return (_SESSION_TEMPLATE_PRIORITY.index(name), name)
        except ValueError: return (len(_SESSION_TEMPLATE_PRIORITY), name)

    for fname in sorted(_all_mds, key=_sort_key):
        content = ""
        source_path = None
        for d in [sess_dir, TEMPLATE_DIR, BASE / "workspace_template"]:
            p = d / fname
            if p.exists():
                content = p.read_text(errors="replace").strip()
                if content:
                    source_path = str(p)
                    break
        if content:
            parts.append(content)
            loaded_paths[fname] = source_path

    # [P54+] Session Memory 注 system prompt 会让 Qwen3 看到自己的历史 Q1/Q2/Q3
    # 答复后跳过 reasoning channel, 直接出 content。
    # 模型本身记忆通过对话 messages 自然保留, 不需要 system prompt 注入。
    # 如果以后要恢复, 设环境变量 LITECODE_INJECT_SESSION_MEMORY=1。
    import os as _os
    if HAS_MEMORY and _os.environ.get("LITECODE_INJECT_SESSION_MEMORY", "0") == "1":
        try:
            mgr = _mem_get(
                workspace=WORKSPACE, session_id=session_id,
                vllm_url=BACKEND_URL, model_id=MODEL_ID, api_key=API_KEY,
                context_window=CONTEXT_WINDOW,
            )
            mem = mgr.load_for_prompt()
            if mem and len(mem.strip().splitlines()) > 3:
                parts.append(f"## Session Memory\n{mem}")
        except Exception as _e:
            log.warning(f"  [memory] {_e}")

    # 追加文件管理提示：告诉模型如何更新这些上下文文件
    if loaded_paths:
        hint_lines = [
            "\n## 上下文文件管理",
            "以下文件构成你的持久记忆，使用 `update_profile` 工具来读取和更新（不要用 read_file/write_file）：",
        ]
        for fname, fpath in loaded_paths.items():
            hint_lines.append(f"  - {fname}: `{fpath}`")
        hint_lines.append(
            "当用户提到新的个人信息、偏好、工作环境时，主动用 update_profile 更新 USER.md。"
        )
        hint_lines.append(
            "当 session 中创建了新服务/路径/环境变量时，用 update_profile 更新 TOOLS.md。"
        )
        if HAS_MEMORY:
            hint_lines.append(
                "重要的项目决策、架构选择、解决方案用 save_memory 工具保存到 MEMORY.md。"
            )
        parts.append("\n".join(hint_lines))

    return "\n\n---\n\n".join(parts) if parts else ""

def build_request_system_prompt(user_message: str, session_id: str = None,
                                strategist: bool = False) -> str:
    """
    Per-request prompt builder。
    优先加载 AGENT_PROMPT.md（热更新），不存在时回退到内置 prompt。
    [v7] 增加任务分发检测: 长文/多步任务自动注入 spawn_agent 强制指令。
    [2026-09-03] strategist=True 时注入权谋/多方博弈推演模式 (prompts/STRATEGIST.md)。
    """
    session_context = _load_session_context(session_id) if session_id else ""

    # 热更新：每次请求重新读取外部文件
    ext  = _load_external_prompt(session_context=session_context)
    base = ext if ext is not None else _BASE_PROMPT
    matched = auto_load_skills(user_message)
    parts   = [base]

    # [v7] 任务自动分发检测 -- 强制注入到 prompt 最前面
    delegation = _detect_auto_delegation(user_message)
    if delegation:
        parts.insert(0, delegation)

    # [2026-09-03] 权谋/多方博弈推演模式 — 前端 chip 开启时注入到最前 (优先级最高)
    if strategist:
        _strat = _load_prompt_md("STRATEGIST.md")
        if _strat:
            parts.insert(0, _strat + "\n\n")
            try:
                import logging as _lg
                _lg.getLogger("openclaw").info(f"  [STRATEGIST] 权谋推演模式已注入 (+{len(_strat)} 字符)")
            except Exception:
                pass

    # 如果没用外部 prompt（内置 prompt），memory 需要额外注入
    if ext is None and session_context:
        parts.append(f"\n\n## Session 上下文\n{session_context}")

    # [OPT] P2-A: 注入网络环境提示，避免模型盲目尝试不可达域名
    net_hint = _network_env_hint()
    if net_hint:
        parts.append(net_hint)

    # 永远注入 using-superpowers 核心元规则（不需要触发词，每次请求都注入）
    _sp_core = _load_superpowers_core()
    if _sp_core:
        parts.append("\n\n## Skills 使用元规则（superpowers）\n" + _sp_core)

    # [OPT] P1-A: 技能注入改为摘要 — 只注入名称+一行描述，不注入全文
    # 旧: 完整 SKILL.md（3K~15K chars）; 新: 摘要 (<500 chars) + load_skill 提示
    if matched:
        label = "\n\n## 已自动加载的匹配 Skills（完整内容）\n" if ext is not None else "\n\n## 已自动加载的匹配 Skills\n"
        parts.append(label + matched)
    return "".join(parts)


def _detect_auto_delegation(user_message: str) -> str:
    """
    [v7] 任务自动分发检测。
    灵感来自 Claude Code: 框架级强制路由, 不靠 LLM 自觉。
    当检测到长文/多步任务时, 返回一段不可忽略的强制指令注入到 system prompt 最前面。
    """
    msg = user_message.lower()
    instructions = []

    # 0.  用户显式指示使用子任务/subagent → 最高优先级强制路由
    #    Bug: "帮我用子任务去搜索下 btc 现在行情，最后给我生成pdf" 过去会走 write_file
    #    Root cause: _DATA_FETCH_KW 不含"搜索"，"子任务"字样本身也无专门规则
    #    Fix: 显式关键词命中即强制 spawn_agent，且插入第 0 条（放指令列表首位）
    _SUBAGENT_KW = ('子任务', '子代理', '子智能体', '分工',
                    'subtask', 'sub-task', 'sub task',
                    'subagent', 'sub-agent', 'sub agent',
                    'spawn_agent')
    _explicit_subagent = any(k in msg for k in _SUBAGENT_KW)
    if _explicit_subagent:
        instructions.append(
            "[MANDATORY-0] 用户显式要求使用子任务 / subagent 执行。\n"
            "本轮禁止直接 write_file + 本地执行。必须：\n"
            "1. 先拆解任务（收集信息 / 编码实现 / 产物生成 三类独立可并行步骤）\n"
            "2. 调用 spawn_agent 完成, 优先 parallel_tasks（独立子任务）或\n"
            "   pipeline_tasks（前步输出喂给后步）:\n"
            "   - 数据获取/搜索类 → agent_type='researcher'\n"
            "   - 代码实现/文件生成 → agent_type='coder'\n"
            "   - 长文写作 → agent_type='writer'\n"
            "3. 每个子任务的 task 参数写清楚『做什么 + 交付什么产物』\n"
            "4. 子代理返回后主循环只负责汇总/校验, 不要重复执行子任务里的事\n"
            "违反此规则（例如直接写两个脚本本地跑）= 任务失败。"
        )

    # 1. 长文写作检测: 包含字数要求 + 写作关键词
    _WRITING_KW = ('小说', '章节', '长文', '系列', '连载', 'novel', 'chapter')
    has_writing = any(k in msg for k in _WRITING_KW)
    word_target = 0
    import re as _re
    # 匹配: "25万字" / "250000字" / "5000字" / "2万字"
    _wm = _re.search(r'(\d+)\s*万\s*字', msg)
    if _wm:
        word_target = int(_wm.group(1)) * 10000
    _wm2 = _re.search(r'(\d{4,})\s*字', msg)
    if _wm2:
        word_target = max(word_target, int(_wm2.group(1)))

    if has_writing and word_target >= 5000:
        instructions.append(
            f"[MANDATORY] 本任务为长文写作 (目标 {word_target} 字)。\n"
            "你必须严格按以下流程执行：\n"
            "1. 第一轮只输出完整大纲 (章节标题+情节要点+目标字数), 不调工具\n"
            "2. 第二轮创建 story_state.json (包含 characters/plot_events/world_rules/style_guide/consistency_notes)\n"
            "3. 第三轮开始用 spawn_agent(writer) 分批写, 每批 1-3 章\n"
            "4. 每批 spawn_agent 的 context 参数必须包含 story_state.json 的内容摘要\n"
            "5. 每批完成后检查 story_state.json 是否已更新\n"
            "6. 禁止在主循环中直接 write_file 写长文内容\n"
            "违反此规则将导致前后矛盾和 context 溢出。\n"
            "参考 prompts/WRITING.md 中的 story_state.json 结构。"
        )

    # 2. 多步搜索+报告检测
    _SEARCH_KW = ('搜索', '深度', '研究', '调研', 'search', 'research', 'web_search', 'web_fetch')
    _REPORT_KW = ('报告', '分析', 'report', 'analysis')
    has_search = sum(1 for k in _SEARCH_KW if k in msg) >= 2
    has_report = any(k in msg for k in _REPORT_KW)
    if has_search and has_report:
        instructions.append(
            "[MANDATORY] 本任务需要多源搜索+报告生成。执行流程:\n"
            "1. 先用 web_search 搜索关键词, 获取结果列表\n"
            "2. 从搜索结果中选 2-3 个高质量 URL, 用 web_fetch 读取详细内容\n"
            "3. 综合搜索摘要和详细内容, 生成报告\n"
            "禁止: 只用 web_search 不用 web_fetch, 或者用 web_fetch 直接访问搜索引擎。"
        )

    # 3. 多步骤任务检测 (>=5 步) -- 只匹配 "步骤N" 格式, 不匹配普通数字列表
    step_count = len(_re.findall(r'步骤\s*\d', msg))
    if step_count >= 4:
        instructions.append(
            f"[MANDATORY] 本任务包含 {step_count} 个步骤。"
            "考虑使用 spawn_agent 的 parallel_tasks 并行执行独立步骤, 提高效率。"
        )

    # 4.  数据获取+输出文档 模式 → 自动触发 spawn_agent(coder)
    #    "获取X数据，然后输出/生成PDF/Excel/报告" 这类多步代码任务
    #  补充 '搜索/检索/search/行情/价格' — 日志里 "搜索下 btc 现在行情"
    #             过去不命中 _DATA_FETCH_KW（只认 获取/抓取/下载/…），导致不走 spawn_agent
    _DATA_FETCH_KW = ('获取', '抓取', '下载', '采集', '查询', '搜索', '检索', '行情', '价格',
                      'fetch', 'get', 'download', 'scrape', 'search', 'price', 'quote')
    _OUTPUT_DOC_KW = ('pdf', 'excel', 'xlsx', 'docx', 'word', '报告', '图表', 'chart',
                      'html', '表格', 'csv', '输出', '生成')
    has_fetch = any(k in msg for k in _DATA_FETCH_KW)
    has_output = any(k in msg for k in _OUTPUT_DOC_KW)
    if has_fetch and has_output and ('spawn_agent' not in msg):
        # 用户没有显式说 spawn_agent 且任务涉及"获取数据 + 生成文档"
        instructions.append(
            "[MANDATORY] 检测到'数据获取 + 文档输出'复合任务 (多步代码 + 外部API + 生成文件)。\n"
            "必须使用 spawn_agent(agent_type='coder') 执行, 原因:\n"
            "1. 任务涉及网络请求、数据处理、文件生成 — 单轮 write_file 难以完成\n"
            "2. 子代理有独立上下文, 不会污染主对话\n"
            "3. 失败可重试, 不阻断主循环\n"
            "调用方式: spawn_agent(agent_type='coder', task='完整任务描述', context='必要的背景')\n"
            "子代理内部会自动: 写代码 → 运行 → 调试 → 验证输出文件存在"
        )

    # 5.  代码+测试组合 → 推荐 spawn_agent(pipeline)
    _CODE_KW = ('写脚本', '写代码', '写函数', '写类', '实现', 'write code', 'implement')
    _TEST_KW = ('测试', '验证', 'test', 'verify', '单元测试', 'unit test')
    has_code = any(k in msg for k in _CODE_KW)
    has_test = any(k in msg for k in _TEST_KW)
    if has_code and has_test and len(msg) > 50 and ('spawn_agent' not in msg):
        instructions.append(
            "[SUGGEST] 检测到'编码+测试'任务。推荐使用 spawn_agent 的 pipeline_tasks 模式:\n"
            "spawn_agent(pipeline_tasks=[\n"
            "  {task: '实现功能', agent_type: 'coder'},\n"
            "  {task: '编写测试', agent_type: 'tester', ingest_previous: true}\n"
            "])\n"
            "coder 的输出自动作为 tester 的上下文, 避免重复解释需求。"
        )

    if not instructions:
        return ""
    return "\n\n".join(instructions) + "\n\n"


def _network_env_hint() -> str:
    """[OPT] P2-A: 检测网络出站白名单，注入到 system prompt。"""
    allowed_hosts = set()
    for mode in ("domestic", "international"):
        for src in _SEARCH_CFG.get(mode, []):
            url = src.get("url_template", "")
            try:
                from urllib.parse import urlparse
                host = urlparse(url.replace("{q}", "test")).hostname
                if host:
                    allowed_hosts.add(host)
            except Exception:
                pass
    if allowed_hosts:
        return (
            "\n\n## 网络环境\n"
            "当前服务器配置的搜索源: " + ", ".join(sorted(allowed_hosts)) + "\n"
            "其他外部域名（coinmarketcap, coingecko, google, binance 等）可能不可达。\n"
            "需要实时数据时，优先使用 web_search 工具而非 web_fetch 访问外部站点。\n"
        )
    return ""

