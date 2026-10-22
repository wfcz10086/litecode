"""lib/skills.py — Skill 索引构建 + 关键词自动匹配 + CN 别名映射"""
import re
from .skill_match import score as _match_score, boilerplate as _boilerplate, alias_hit as _alias_hit
import time
import json
from pathlib import Path
from .config import SKILLS_DIR, BASE, WORKSPACE, log
from .skill_body import AUTO_PREVIEW_CHARS, render as _render_body

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

# Skill keyword index: [(name, keyword_set, auto_preview, one_liner)]
# 第三项只是"自动注入用的预览": 正文 <= AUTO_PREVIEW_CHARS 时即全文, 超过时是开头几节 + 摘要/续读提示。
# 正文本身不进索引 —— load_skill 经 _SKILL_PATHS 读源文件 (lib/skill_body.py)。
_SKILL_INDEX: list = []
_SKILL_PATHS: dict = {}   # name → SKILL.md 路径
SUMMARY_MARK = "[摘要: 自动注入只含开头部分"
_SKILL_GROUP: dict = {}   # 子技能名 → (父技能名, 短标签), 来自 frontmatter parent/label
_SKILL_ALIASES: dict = {}   # frontmatter aliases → 技能名 (同名别名覆盖下面 _CN_SKILL_MAP 的映射)
_SKILL_DROP: list = [frozenset()]   # 高频套话词, 建索引时算 (lib/skill_match.py)


def _frontmatter(txt: str) -> dict:
    """解析 SKILL.md 头部 `---` 包裹的 YAML。支持 `>`/`|` 多行与引号值; 无 PyYAML 时退回逐行解析。"""
    m = re.match(r"\s*---\s*\n(.*?)\n---", txt, re.S)
    if not m:
        return {}
    block = m.group(1)
    try:
        import yaml
        data = yaml.safe_load(block)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    out, key = {}, None
    for line in block.splitlines():
        km = re.match(r"([A-Za-z_][\w-]*):\s*(.*)$", line)
        if km and not line.startswith((" ", "\t")):
            key, val = km.group(1), km.group(2).strip()
            out[key] = "" if val in (">", "|", ">-", "|-") else val.strip("\"'")
        elif key and line.strip():
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def _one_liner(desc: str, limit: int = 120) -> str:
    flat = " ".join(str(desc).split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"

_INDEX_SIG: list = [None]   # 建索引时的 skill_registry.index_signature() (跨进程感知安装/启停)


def _build_skill_index():
    """内置 (SKILLS_DIR) + 已安装 (WORKSPACE/skills) 中启用的技能; 停用的不进索引 (lib/skill_registry.py)。"""
    from . import skill_registry as _reg
    _SKILL_INDEX.clear()  # 就地清空，保持引用不变（import 安全）
    _SKILL_GROUP.clear()
    _SKILL_PATHS.clear()
    _SKILL_ALIASES.clear()
    _CN_SKILL_MAP.clear()
    _CN_SKILL_MAP.update(_LEGACY_ALIASES)
    try:
        _INDEX_SIG[0] = _reg.index_signature(SKILLS_DIR)
        files = _reg.enabled_files(SKILLS_DIR)
    except Exception as e:
        log.warning(f"[skills] registry unavailable, builtin only: {e}")
        files = [(md.parent.name, md) for md in sorted(SKILLS_DIR.glob("*/SKILL.md"))] if SKILLS_DIR.exists() else []
    for name, md in files:
        try:
            txt  = md.read_text(errors="replace")

            fm   = _frontmatter(txt)
            desc = str(fm.get("description") or "")
            for alias in _reg.as_list(fm.get("aliases")):
                _CN_SKILL_MAP[alias.lower()] = name
            if fm.get("parent"):
                _SKILL_GROUP[name] = (str(fm["parent"]), str(fm.get("label") or ""))
            _al = fm.get("aliases") or []
            for _a in ([_al] if isinstance(_al, str) else _al):
                if str(_a).strip():
                    _SKILL_ALIASES[str(_a).strip().lower()] = name
            one_liner = _one_liner(desc)

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
            _SKILL_INDEX.append((name, keywords, _auto_preview(name, txt), one_liner))
            _SKILL_PATHS[name] = md
        except Exception:
            pass
    _SKILL_DROP[0] = _boilerplate([kw for _, kw, _, _ in _SKILL_INDEX])


def ensure_fresh() -> bool:
    """技能被安装/卸载/启停 (可能在 web_ui 进程里) 后重建索引; 返回是否重建了。只做 3 次 stat。"""
    try:
        from . import skill_registry as _reg
        if _INDEX_SIG[0] is not None and _reg.index_signature(SKILLS_DIR) == _INDEX_SIG[0]:
            return False
    except Exception:
        return False
    _build_skill_index()
    return True


def turn_skills_block(user_message: str) -> str:
    """本轮要附加的技能: 用户强制指定了 (chat body `skills`) → 全文注入且不做关键词匹配;
    否则走 auto_load_skills。用到的技能经 lib/skill_turn.note 记入本轮并计使用次数。"""
    from . import skill_turn
    ensure_fresh()
    paths = dict(_SKILL_PATHS)
    for n in skill_turn.forced():          # 草稿「试运行」: 草稿不进索引, 只在被点名的这一轮注入
        if n not in paths:
            from . import skill_registry as _reg
            d = _reg.draft_path(n, SKILLS_DIR)
            if d is not None:
                paths[n] = d / "SKILL.md"
    forced = [n for n in skill_turn.forced() if n in paths]
    if forced:
        text, got = skill_turn.forced_block(forced, paths)
        for n in got:
            skill_turn.note(n)
        return skill_turn.FORCED_LABEL + text if text else ""
    matched = auto_load_skills(user_message)
    for n in re.findall(r"\[Active Skill: (\S+)\]", matched):
        skill_turn.note(n)
    return matched


def _auto_preview(name: str, txt: str) -> str:
    """自动注入用: 不超限原样; 超限按章节取开头 + 明确标注摘要与加载全文的方式。"""
    if len(txt) <= AUTO_PREVIEW_CHARS:
        return txt
    page, _ = _render_body(name, txt, limit=AUTO_PREVIEW_CHARS)
    return (f"{SUMMARY_MARK}, 非全文; 加载全文/续读用 load_skill(name='{name}'), "
            f"长技能按 section 参数分节读取]\n{page}")


def _index_chars() -> int:
    try:
        from .config import CFG
        return int((CFG.get("agent") or {}).get("skills_index_chars", 72))
    except Exception:
        return 72


_SENTENCE_END = re.compile(r"[。；;！!？?]|\. |——| — ")


def index_line(one_liner: str, limit: int) -> str:
    """系统提示里的技能清单只放一句话: 到第一个句末为止 (够长且不超限时), 否则截到 limit 字。
    完整描述 list_skills 仍给 (120 字), 正文 load_skill; 关键词自动匹配用的是索引里的关键词集合, 不受影响。"""
    if limit <= 0:
        return one_liner
    s = one_liner.rstrip("…")
    m = _SENTENCE_END.search(s)
    if m and 10 <= m.start() <= limit:
        return s[:m.start()]
    return s if len(s) <= limit else s[:limit].rstrip(" ,，、:：(（") + "…"


def load_skills_index() -> str:
    """One-liner index of all skills for system prompt.
    frontmatter 声明了 `parent` 的子技能不单独占行, 收进父技能那一行 (见 render_grouped_index)。
    [2026-10-21] 每行只取一句话 (agent.skills_index_chars, 默认 72; 0=旧行为 120 字), 省约 3k t 固定前缀。"""
    ensure_fresh()
    lim = _index_chars()
    return render_grouped_index([(name, index_line(one_liner, lim)) for name, _, _, one_liner in _SKILL_INDEX],
                                _SKILL_GROUP)


def render_grouped_index(entries: list, group: dict) -> str:
    """entries: [(name, one_liner)]; group: {子技能名: (父技能名, 短标签)}。
    父技能不存在时子技能照常单独列出, 不会丢。"""
    names = {n for n, _ in entries}
    kids: dict = {}
    for child, (parent, label) in group.items():
        if parent in names and child in names:
            kids.setdefault(parent, []).append((child, label))
    lines = []
    for name, one_liner in entries:
        if name in group and group[name][0] in kids:
            continue
        line = f"- {name}: {one_liner}"
        if name in kids:
            line += " 子技能: " + " / ".join(f"{c}({lb})" if lb else c for c, lb in sorted(kids[name]))
        lines.append(line)
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

# 中文/英文别名 → 技能名。[2026-10-22] 别名迁到各 SKILL.md frontmatter `aliases:`, 建索引时汇总进
# _CN_SKILL_MAP (就地更新, 旧 import 照常可用); 这里只留没有 SKILL.md 的技能的旧别名 —— 它们命中时
# 仍豁免短消息门控 (与迁移前行为一致), 但索引里没有这些技能, 不会注入任何内容。
_LEGACY_ALIASES: dict = {
    # web-artifacts-builder
    "artifact": "web-artifacts-builder", "tailwind": "web-artifacts-builder",
    "shadcn": "web-artifacts-builder",
    # companion
    "撑不下去": "companion", "心累": "companion", "疲惫": "companion", "emo": "companion", "情绪低落": "companion",
    "孤单": "companion", "一个人": "companion", "没人聊": "companion", "睡不着": "companion", "失眠": "companion",
    "焦虑": "companion", "委屈": "companion", "难过": "companion", "想哭": "companion", "心情不好": "companion",
    "心烦": "companion", "心塞": "companion", "崩溃": "companion", "被骂": "companion", "被否定": "companion",
    "被卷": "companion", "内耗": "companion", "陪聊": "companion", "陪我聊聊": "companion", "陪伴": "companion",
    "说说话": "companion", "聊聊天": "companion",
    # mao-zedong
    "毛主席": "mao-zedong", "毛泽东": "mao-zedong", "主席思想": "mao-zedong", "毛泽东思想": "mao-zedong",
    "战斗分析": "mao-zedong", "决策分析": "mao-zedong", "主要矛盾": "mao-zedong", "持久战": "mao-zedong",
    "游击战": "mao-zedong", "阵地战": "mao-zedong", "运动战": "mao-zedong", "统一战线": "mao-zedong",
    "敌进我退": "mao-zedong", "集中优势": "mao-zedong", "打持久战": "mao-zedong", "打游击": "mao-zedong",
    "战略防御": "mao-zedong", "战略相持": "mao-zedong", "战略反攻": "mao-zedong", "保存实力": "mao-zedong",
    "敌强我弱": "mao-zedong", "以弱胜强": "mao-zedong", "农村包围": "mao-zedong", "农村包围城市": "mao-zedong",
    "十大军事": "mao-zedong", "十大军事原则": "mao-zedong", "矛盾论": "mao-zedong", "实践论": "mao-zedong",
    "论持久战": "mao-zedong",
}
_CN_SKILL_MAP: dict = dict(_LEGACY_ALIASES)

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
    _aliases = {**_CN_SKILL_MAP, **_SKILL_ALIASES}
    _has_alias_hit = any(_alias_hit(alias, msg_lower) for alias in _aliases)
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
    for alias, skill_name in _aliases.items():
        if _alias_hit(alias, msg_lower):
            for name, _, content, _ in _SKILL_INDEX:
                if name == skill_name and name not in matched:
                    matched[name] = (content, 10)  # high score for direct match

    # 2. Keyword overlap
    # 打分规则见 lib/skill_match.py: 互相包含的片段折叠、功能词与高频套话不计分
    msg_all = (msg_words - _STOP) | msg_exts | msg_cn
    for name, keywords, content, _ in _SKILL_INDEX:
        if name in matched:
            continue
        n_hit, _ = _match_score(keywords, msg_all, _SKILL_DROP[0])
        if n_hit >= 2:
            matched[name] = (content, n_hit)

    if not matched:
        _emit_skill_telemetry(user_message, {}, gated=False)
        return ""

    # Sort by score, take top 2
    ranked = sorted(matched.items(), key=lambda x: x[1][1], reverse=True)
    parts  = [f"[Active Skill: {name}]\n{content}" for name, (content, _) in ranked[:2]]
    _emit_skill_telemetry(user_message, matched, gated=False)
    from lib.usage_ledger import note_skills   # [usage-ledger] 记进本轮 turn_skills, 看板技能排行用
    note_skills(name for name, _ in ranked[:2])
    return "\n\n".join(parts)

def load_tools_md() -> str:
    p = BASE / "TOOLS.md"
    return p.read_text(errors="replace") if p.exists() else ""
