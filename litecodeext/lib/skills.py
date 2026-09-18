"""lib/skills.py — Skill 索引构建 + 关键词自动匹配 + CN 别名映射"""
import re
import time
import json
from pathlib import Path
from .config import SKILLS_DIR, BASE, WORKSPACE, log

SKILLS_TELEMETRY_DIR = WORKSPACE / "telemetry"
SKILLS_TELEMETRY_PATH = SKILLS_TELEMETRY_DIR / "skills.jsonl"

# Stop words for keyword matching
_STOP = {
    'the','and','for','are','that','this','with','from','use','used',
    'you','your','any','all','not','can','will','its','into','also',
    'when','user','skill','file','want','need','should','have','been',
    'they','their','there','here','what','which','where','how','who',
    'but','more','some','such','each','both','only','very','well',
    'ask','was','make','like','take','just','than','then','them',
    'those','these','would','could','about','after','other','even',
    'using','being','see','set','new','one','two','per','via',
}

# Skill keyword index: [(name, keyword_set, full_text, one_liner)]
_SKILL_INDEX: list = []

def _build_skill_index():
    _SKILL_INDEX.clear()  # 就地清空，保持引用不变（import 安全）
    if not SKILLS_DIR.exists():
        return
    for md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        try:
            txt  = md.read_text(errors="replace")
            name = md.parent.name

            m    = re.search(r"description:\s*(.+?)(?:\n---|$)", txt, re.S)
            desc = m.group(1).strip() if m else ""
            one_liner = desc.split("\n")[0][:120]

            km   = re.search(r"##\s*[Kk]eyword.+?\n(.+?)(?:\n##|\Z)", txt, re.S | re.I)
            kw_s = km.group(1) if km else ""

            # Extensions (.docx .pdf etc.) are high-signal triggers
            exts = set(re.findall(r'\.\w{2,6}', desc + kw_s))
            # Content words from description + keywords section
            words = set(
                w.lower() for w in re.findall(r'\b[a-zA-Z]{3,}\b', desc + kw_s)
            ) - _STOP
            # Chinese words
            # Chinese: extract all contiguous runs, then also bigrams for better overlap
            cn_runs  = re.findall(r'[\u4e00-\u9fa5]+', desc + kw_s)
            cn_words = set()
            for run in cn_runs:
                if len(run) >= 2:
                    cn_words.add(run)          # full run
                # sliding 2-gram and 3-gram for partial matching
                for n in (2, 3):
                    for i in range(len(run) - n + 1):
                        cn_words.add(run[i:i+n])

            keywords = words | exts | cn_words
            _SKILL_INDEX.append((name, keywords, txt[:10000], one_liner))
        except Exception:
            pass

def load_skills_index() -> str:
    """One-liner index of all skills for system prompt."""
    lines = []
    for name, _, _, one_liner in _SKILL_INDEX:
        lines.append(f"- {name}: {one_liner}  [path: skills/{name}/SKILL.md]")
    return "\n".join(lines)


_superpowers_core_cache = ""
_superpowers_core_mtime = 0.0

def _load_superpowers_core() -> str:
    """
    从 using-superpowers/SKILL.md 提取核心规则注入 system prompt。
    mtime 缓存，热更新生效。只取关键段落不含 dot 流程图，控制 token 占用。
    """
    global _superpowers_core_cache, _superpowers_core_mtime
    p = SKILLS_DIR / "using-superpowers" / "SKILL.md"
    if not p.exists():
        return ""
    try:
        mtime = p.stat().st_mtime
        if mtime == _superpowers_core_mtime and _superpowers_core_cache:
            return _superpowers_core_cache
        raw = p.read_text(errors="replace")
        parts = []
        import re as _re3
        # 1. EXTREMELY-IMPORTANT
        m = _re3.search(r"<EXTREMELY-IMPORTANT>([\s\S]*?)</EXTREMELY-IMPORTANT>", raw)
        if m:
            parts.append(m.group(1).strip())
        # 2. 工具对照表
        m2 = _re3.search(r"(## 工具对照[^\n]*\n(?:(?!\n##)[\s\S])*)", raw)
        if m2:
            parts.append(m2.group(1).strip())
        # 3. 技能优先级
        m3 = _re3.search(r"(## 技能优先级[^\n]*\n(?:(?!\n##)[\s\S])*)", raw)
        if m3:
            parts.append(m3.group(1).strip())
        # 4. 中国特色技能路由（表格部分）
        m4 = _re3.search(r"(## 中国特色技能路由[^\n]*\n(?:(?!\n##)[\s\S])*)", raw)
        if m4:
            parts.append(m4.group(1).strip())
        result = "\n\n".join(x for x in parts if x.strip())
        _superpowers_core_cache = result
        _superpowers_core_mtime = mtime
        return result
    except Exception:
        return ""

# Chinese keyword → skill name direct mapping
_CN_SKILL_MAP: dict = {
    # docx
    "word": "docx", "文档": "docx", "docx": "docx", ".docx": "docx",
    # xlsx
    "excel": "xlsx", "表格": "xlsx", "电子表格": "xlsx", "xlsx": "xlsx", ".xlsx": "xlsx",
    # pptx
    "ppt": "pptx", "pptx": "pptx", "幻灯片": "pptx", "演示": "pptx",
    "演示文稿": "pptx", "slides": "pptx", ".pptx": "pptx",
    # pdf
    "pdf": "pdf", ".pdf": "pdf", "pdf文件": "pdf",
    # pdf-reading
    "读取pdf": "pdf-reading", "提取pdf": "pdf-reading", "解析pdf": "pdf-reading",
    # internal-comms
    "3p": "internal-comms", "状态报告": "internal-comms", "周报": "internal-comms",
    "newsletter": "internal-comms", "内部通报": "internal-comms",
    # systematic-debugging (superpowers-zh)
    "调试": "systematic-debugging", "debug": "systematic-debugging",
    "排查": "systematic-debugging", "排错": "systematic-debugging",
    "报错": "systematic-debugging", "bug": "systematic-debugging",
    "traceback": "systematic-debugging", "exception": "systematic-debugging",
    "不工作": "systematic-debugging", "启动失败": "systematic-debugging",
    "修复": "systematic-debugging", "修bug": "systematic-debugging",
    # verification-before-completion (superpowers-zh)
    "验证": "verification-before-completion", "verify": "verification-before-completion",
    "测试通过": "verification-before-completion", "完成验证": "verification-before-completion",
    # writing-plans (superpowers-zh)
    "规划": "writing-plans", "计划": "writing-plans", "plan": "writing-plans",
    "实现计划": "writing-plans", "任务拆解": "writing-plans",
    # brainstorming (superpowers-zh)
    "头脑风暴": "brainstorming", "brainstorm": "brainstorming",
    "方案设计": "brainstorming", "需求分析": "brainstorming",
    # test-driven-development (superpowers-zh)
    "tdd": "test-driven-development", "单元测试": "test-driven-development",
    "测试驱动": "test-driven-development", "red green": "test-driven-development",
    # deep-search
    "搜索": "deep-search", "查询": "deep-search", "search": "deep-search",
    # frontend-design
    "react": "frontend-design", "组件": "frontend-design", "前端": "frontend-design",
    "网页": "frontend-design", "html": "frontend-design", "ui": "frontend-design",
    # algorithmic-art
    "算法艺术": "algorithmic-art", "p5js": "algorithmic-art", "生成艺术": "algorithmic-art",
    "generative": "algorithmic-art",
    # mcp-builder
    "mcp": "mcp-builder", "model context": "mcp-builder",
    # slack-gif
    "gif": "slack-gif-creator", "动图": "slack-gif-creator",
    # canvas-design
    "海报": "canvas-design", "poster": "canvas-design", "设计图": "canvas-design",
    # web-artifacts-builder
    "artifact": "web-artifacts-builder", "tailwind": "web-artifacts-builder",
    "shadcn": "web-artifacts-builder",
    # Go 语言 - 各种表达方式
    "golang": "go", "go语言": "go", ".go": "go", "goroutine": "go",
    "gin框架": "go", "grpc": "go", "go mod": "go", "go build": "go", "go test": "go",
    "用go": "go", "go程序": "go", "go并发": "go", "go项目": "go",
    "go写": "go", "写go": "go", "go代码": "go", "go服务": "go",
    # 研究分析 - 股票/加密货币/量化/数据分析
    "分析股票": "research-analyst", "股票分析": "research-analyst",
    "股价分析": "research-analyst", "量化": "research-analyst",
    "回测": "research-analyst", "技术分析": "research-analyst",
    "基本面": "research-analyst", "a股": "research-analyst",
    "美股": "research-analyst", "加密货币": "research-analyst",
    "行情分析": "research-analyst", "数据分析": "research-analyst",
    "研究调研": "research-analyst", "市场分析": "research-analyst",
    "比特币": "research-analyst", "以太坊": "research-analyst",
    "btc": "research-analyst", "eth": "research-analyst",
    "走势": "research-analyst", "涨跌": "research-analyst",
    "k线": "research-analyst", "macd": "research-analyst",
    "均线": "research-analyst", "rsi": "research-analyst",
    "股市": "research-analyst", "炒股": "research-analyst",
    "投资分析": "research-analyst", "财务分析": "research-analyst",
    "etf": "research-analyst", "期货": "research-analyst",
    "帮我分析": "research-analyst", "帮我看看": "research-analyst",
    "分析一下": "research-analyst",
    # crypto-tracker - 加密货币实时数据
    "币价": "crypto-tracker", "币安": "crypto-tracker", "binance": "crypto-tracker",
    "coingecko": "crypto-tracker", "实时价格": "crypto-tracker", "现货": "crypto-tracker",
    "合约": "crypto-tracker", "usdt": "crypto-tracker", "交易量": "crypto-tracker",
    "市值": "crypto-tracker", "深度": "crypto-tracker", "盘口": "crypto-tracker",
    "山寨币": "crypto-tracker", "defi": "crypto-tracker",
    # linux-admin - 系统运维
    "服务器": "linux-admin", "运维": "linux-admin", "进程": "linux-admin",
    "磁盘": "linux-admin", "内存": "linux-admin", "cpu": "linux-admin",
    "gpu监控": "linux-admin", "nvidia-smi": "linux-admin", "systemd": "linux-admin",
    "systemctl": "linux-admin", "ipmi": "linux-admin", "温度": "linux-admin",
    "负载": "linux-admin", "uptime": "linux-admin",
    # data-scraper - 数据抓取
    "抓取": "data-scraper", "爬虫": "data-scraper", "爬取": "data-scraper",
    "scrape": "data-scraper", "crawl": "data-scraper", "采集": "data-scraper",
    "spider": "data-scraper", "xpath": "data-scraper", "beautifulsoup": "data-scraper",
    # data-analysis - 数据分析
    "pandas": "data-analysis", "matplotlib": "data-analysis", "可视化": "data-analysis",
    "图表": "data-analysis", "统计": "data-analysis", "直方图": "data-analysis",
    "折线图": "data-analysis", "散点图": "data-analysis", "热力图": "data-analysis",
    "数据清洗": "data-analysis", "dataframe": "data-analysis",
    # docker-ops - Docker管理
    "docker": "docker-ops", "容器": "docker-ops", "镜像": "docker-ops",
    "dockerfile": "docker-ops", "docker-compose": "docker-ops", "compose": "docker-ops",
    "harbor": "docker-ops", "registry": "docker-ops",
    # api-builder - API开发
    "fastapi": "api-builder", "flask": "api-builder", "restful": "api-builder",
    "api接口": "api-builder", "写接口": "api-builder", "后端服务": "api-builder",
    "swagger": "api-builder", "endpoint": "api-builder",
    # log-analyzer - 日志分析
    "日志分析": "log-analyzer", "分析日志": "log-analyzer", "journalctl": "log-analyzer",
    "error.log": "log-analyzer", "access.log": "log-analyzer", "日志统计": "log-analyzer",
    "报错日志": "log-analyzer", "排查日志": "log-analyzer",
    # network-tools - 网络诊断
    "网络诊断": "network-tools", "ping": "network-tools", "traceroute": "network-tools",
    "nmap": "network-tools", "端口扫描": "network-tools", "防火墙": "network-tools",
    "iptables": "network-tools", "tcpdump": "network-tools", "抓包": "network-tools",
    "ssl证书": "network-tools", "dns": "network-tools", "网络不通": "network-tools",
    # db-toolkit - 数据库
    "数据库": "db-toolkit", "database": "db-toolkit", "sqlite": "db-toolkit",
    "postgresql": "db-toolkit", "mysql": "db-toolkit", "redis": "db-toolkit",
    "建表": "db-toolkit", "sql查询": "db-toolkit", "sqlalchemy": "db-toolkit",
    "索引": "db-toolkit", "数据迁移": "db-toolkit",
    # automation - 自动化
    "自动化": "automation", "定时任务": "automation", "cron": "automation",
    "crontab": "automation", "计划任务": "automation", "自动备份": "automation",
    "定期执行": "automation", "开机自启": "automation", "守护进程": "automation",
    "supervisor": "automation", "systemd timer": "automation", "webhook": "automation",
    # [v1.4] longtext-processing — 超长文本提炼/总结/翻译/改写
    "长文本": "longtext-processing", "总结长文": "longtext-processing",
    "提炼总结": "longtext-processing", "翻译文档": "longtext-processing",
    "翻译长文": "longtext-processing", "逐段翻译": "longtext-processing",
    "批量翻译": "longtext-processing", "文档切片": "longtext-processing",
    "改写文档": "longtext-processing", "translate document": "longtext-processing",
    "summarize long": "longtext-processing", "摘要": "longtext-processing",
    # [v1.4] novel-writing — 小说创作 / 章节重写 / 状态跟踪
    "小说": "novel-writing", "章节": "novel-writing", "剧情": "novel-writing",
    "写作": "novel-writing", "续写": "novel-writing", "重写章节": "novel-writing",
    "章节重写": "novel-writing", "人物设定": "novel-writing", "故事大纲": "novel-writing",
    "剧情推进": "novel-writing", "小说状态": "novel-writing",
    # [v1.4] large-project — 重型项目 bug 定位 / 代码库导航
    "大型项目": "large-project", "codebase": "large-project", "monorepo": "large-project",
    "代码库": "large-project", "整个项目": "large-project", "全局搜索": "large-project",
    "batch refactor": "large-project", "重构": "large-project",
    "复杂项目": "large-project", "跨文件调用": "large-project",
    # [2026-07-26] companion — 陪伴模式 (情绪价值, 不给方案)
    # [2026-08-25 P0-2] 去掉裸单字别名 "累" — 会命中"劳累/连累/累计/
    # 日积月累"等无关内容, 误触发陪伴人格注入; 语义已有 "心累"/"疲惫" 覆盖.
    "撑不下去": "companion", "心累": "companion",
    "疲惫": "companion", "emo": "companion", "情绪低落": "companion",
    "孤单": "companion", "一个人": "companion", "没人聊": "companion",
    "睡不着": "companion", "失眠": "companion", "焦虑": "companion",
    "委屈": "companion", "难过": "companion", "想哭": "companion",
    "心情不好": "companion", "心烦": "companion", "心塞": "companion",
    "崩溃": "companion", "被骂": "companion", "被否定": "companion",
    "被卷": "companion", "内耗": "companion", "陪聊": "companion",
    "陪我聊聊": "companion", "陪伴": "companion", "说说话": "companion",
    "聊聊天": "companion",
    # [2026-07-26] mao-zedong — 战斗决策 (毛主席思想框架)
    "毛主席": "mao-zedong", "毛泽东": "mao-zedong",
    "主席思想": "mao-zedong", "毛泽东思想": "mao-zedong",
    "战斗分析": "mao-zedong", "决策分析": "mao-zedong",
    "主要矛盾": "mao-zedong", "持久战": "mao-zedong",
    "游击战": "mao-zedong", "阵地战": "mao-zedong",
    "运动战": "mao-zedong", "统一战线": "mao-zedong",
    "敌进我退": "mao-zedong", "集中优势": "mao-zedong",
    "打持久战": "mao-zedong", "打游击": "mao-zedong",
    "战略防御": "mao-zedong", "战略相持": "mao-zedong",
    "战略反攻": "mao-zedong", "保存实力": "mao-zedong",
    "敌强我弱": "mao-zedong", "以弱胜强": "mao-zedong",
    "农村包围": "mao-zedong", "农村包围城市": "mao-zedong",
    "十大军事": "mao-zedong", "十大军事原则": "mao-zedong",
    "矛盾论": "mao-zedong", "实践论": "mao-zedong",
    "论持久战": "mao-zedong",
    # [v1.4] bug-localization — 从症状/堆栈精确定位到行号
    "bug 定位": "bug-localization", "bug定位": "bug-localization",
    "定位错误": "bug-localization", "追踪报错": "bug-localization",
    "找 bug": "bug-localization", "找bug": "bug-localization",
    "复现 bug": "bug-localization", "线上事故": "bug-localization",
    "错误溯源": "bug-localization", "栈跟踪": "bug-localization",
    "错误堆栈": "bug-localization", "root cause": "bug-localization",
    "production bug": "bug-localization", "项目级 bug": "bug-localization",
    "项目级bug": "bug-localization",
}

def _emit_skill_telemetry(user_message: str, matched: dict, gated: bool = False):
    """写入 skill 命中埋点 jsonl, 失败静默 (不影响主流程).

    v1.8 P36-c: 走 core/telemetry.py 统一入口；业务字段保留向后兼容。
    """
    try:
        import hashlib
        msg_hash = hashlib.sha256(user_message.encode("utf-8", errors="replace")).hexdigest()[:12]
        # 业务字段（向后兼容）
        fields = {
            "msg_len": len(user_message),
            "msg_hash": msg_hash,
            "msg_preview": user_message[:80].replace("\n", " "),
            "gated": gated,
            "matched": [
                {"name": n, "score": s}
                for n, (_c, s) in matched.items()
            ],
        }
        # 走统一入口（自动加 trace_id/session_id/event）
        try:
            from core.telemetry import emit as _emit
            _emit(
                event="skill_match" if matched else ("skill_gated" if gated else "skill_miss"),
                fields=fields,
                jsonl="skills.jsonl",
            )
        except Exception:
            # fallback：直写（保护主流程）
            SKILLS_TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
            entry = {"ts": time.time(), **fields}
            with SKILLS_TELEMETRY_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[telemetry/skills] write failed: {e}")


def auto_load_skills(user_message: str) -> str:
    """
    Match user message against skill index.
    [v7] 增加门控: 短消息 (<50 字) 且无工具意图关键词时, 跳过匹配。
    避免简单问答场景加载无关 Skill 膨胀 System Prompt。
    [2026-07-26] 情绪/毛泽东思想类 skill 靠短消息触发, 直接命中别名时跳过门控。
    """
    msg_lower  = user_message.lower()

    # [v7] 门控: 短消息跳过 Skill 匹配
    _TOOL_INTENT_KEYWORDS = {
        '写', '创建', '生成', '搜索', '分析', '脚本', '代码', '文件',
        '报告', '小说', '图表', 'write', 'create', 'build', 'search',
        'script', 'code', 'file', 'report', 'novel', 'chart',
        '.py', '.js', '.ts', '.go', '.md', '.csv', '.xlsx', '.pdf',
    }
    # [2026-07-26] 短消息直接命中别名时豁免门控 (companion/mao-zedong 靠短消息触发)
    _has_alias_hit = any(alias in msg_lower for alias in _CN_SKILL_MAP)
    if (len(user_message) < 50
            and not any(k in msg_lower for k in _TOOL_INTENT_KEYWORDS)
            and not _has_alias_hit):
        _emit_skill_telemetry(user_message, {}, gated=True)
        return ""

    msg_words  = set(re.findall(r'[a-zA-Z]{3,}', msg_lower))
    msg_exts   = set(re.findall(r'\.\w{2,6}', msg_lower))
    # Chinese bigrams for better partial matching
    _cn_runs = re.findall(r'[\u4e00-\u9fa5]+', user_message)
    msg_cn   = set()
    for _run in _cn_runs:
        if len(_run) >= 2:
            msg_cn.add(_run)
        for _n in (2, 3):
            for _i in range(len(_run) - _n + 1):
                msg_cn.add(_run[_i:_i+_n])

    # Build skill name → (content, score) dict
    matched: dict = {}   # {skill_name: (content, score)}

    # 1. Direct alias lookup
    for alias, skill_name in _CN_SKILL_MAP.items():
        if alias in msg_lower:
            for name, _, content, _ in _SKILL_INDEX:
                if name == skill_name and name not in matched:
                    matched[name] = (content, 10)  # high score for direct match

    # 2. Keyword overlap
    msg_all = (msg_words - _STOP) | msg_exts | msg_cn
    for name, keywords, content, _ in _SKILL_INDEX:
        if name in matched:
            continue
        overlap = keywords & msg_all
        if len(overlap) >= 2:
            matched[name] = (content, len(overlap))

    if not matched:
        _emit_skill_telemetry(user_message, {}, gated=False)
        return ""

    # Sort by score, take top 2
    ranked = sorted(matched.items(), key=lambda x: x[1][1], reverse=True)
    parts  = [f"[Active Skill: {name}]\n{content}" for name, (content, _) in ranked[:2]]
    _emit_skill_telemetry(user_message, matched, gated=False)
    return "\n\n".join(parts)

def load_tools_md() -> str:
    p = BASE / "TOOLS.md"
    return p.read_text(errors="replace") if p.exists() else ""
