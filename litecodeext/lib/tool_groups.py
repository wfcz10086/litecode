"""lib/tool_groups.py — 工具按需暴露: 核心工具常驻, 低频工具组按会话用到才加入。

为什么: 82 个工具的 JSON schema 每轮都发 (约 3 万 t, 占固定前缀一半以上), 而 Windows / SSH /
定时器与 DAG / 代码工程 / git / 开源检索这些组大多数会话根本用不到。

规则 (一个会话内只增不减, 保 KV 前缀缓存):
  - 核心工具 (不属于任何组) 永远在, 顺序同 TOOL_DEFS。
  - 组被激活的条件, 任一即可:
      1. 用户原话命中组的关键词 / 组内工具名 / 已保存的主机或节点别名 / IP
         (本轮挂的 <context> 动态上下文不算: 自动匹配的技能正文常顺带提到无关工具);
      2. 对话里 (助手正文、工具调用、工具结果、框架提示) 出现组内工具名 —— 例如 PROJECT_MAP
         守卫提示"请调用 update_map", 下一次调用模型时 update_map 就在列表里;
      3. 模型调用 enable_tools(groups=[...]) 或直接调用了组内工具。
  - 激活的组记在会话目录 .tool_groups.json (重启后不丢), 之后该会话每轮都带上。
  - 发送顺序: 核心工具在前, 已激活组按激活先后追加在后 —— 新组加入只改动列表尾部。

开关: config.json agent.tool_groups = false 关闭 (每轮发全部工具, 回到旧行为);
      也可写成 {"enabled": true, "always": ["git"]} 让某些组常驻。
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

GROUPS: dict[str, dict] = {
    "windows": {
        "label": "已接入的 Windows 电脑: 节点/界面操作/系统与传文件",
        "tools": ("windows_nodes", "windows_desktop", "windows_system"),
        "keywords": ("windows", "win-", "win10", "win11", "记事本", "notepad", "powershell", "远程电脑",
                     "远程桌面", "远程操控", "那台电脑", "另一台电脑", "电脑上", "虚拟机", "wps",
                     "资源管理器", "pc微信", "电脑版微信", "微信登录", "operator", "操作员", "windows 节点"),
    },
    "desktop": {
        "label": "本容器 Linux 桌面 (:99/noVNC)",
        "tools": ("desktop_exec", "desktop_screenshot", "desktop_key", "desktop_type", "desktop_click"),
        "keywords": ("桌面", "desktop", "vnc", "xvfb", "tradingview", "k线", "截屏", "屏幕", "打开浏览器",
                     "打开网页", "chrome", "chromium", "xdotool", "窗口"),
    },
    "remote": {
        "label": "SSH 远程主机 (含宿主机 local)、远程 Docker",
        "tools": ("ssh_exec", "docker_remote_ps", "docker_remote_inspect", "docker_remote_recreate",
                  "ssh_write_file", "ssh_sync_files", "ssh_str_replace", "ssh_read_file",
                  "ssh_save_host", "ssh_list_hosts", "ssh_remove_host"),
        "keywords": ("ssh", "sftp", "scp", "远程", "服务器", "远程主机", "主机上", "这台主机", "目标机", "宿主机", "本机", "这台机器",
                     "docker", "容器", "gpu", "显卡", "nvidia", "vllm", "部署", "运维", "集群"),
    },
    "workflow": {
        "label": "定时任务/提醒 与 DAG 工作流",
        "tools": ("create_timer", "list_timers", "update_timer", "delete_timer", "run_timer_now",
                  "get_timer_history", "create_dag", "update_dag", "delete_dag", "patch_dag_node", "run_dag"),
        "keywords": ("定时", "提醒", "每天", "每周", "每小时", "每隔", "每晚", "每早", "闹钟",
                     "cron", "timer", "计划任务", "周期", "dag", "流水线", "工作流", "workflow", "pipeline",
                     "编排", "jenkins"),
    },
    "project": {
        "label": "代码工程地图与按符号定位/改写",
        "tools": ("project_init", "locate_symbol", "edit_symbol", "update_map"),
        "keywords": ("代码库", "仓库", "repo", "codebase", "重构", "refactor", "符号", "symbol",
                     "project_map", "架构", "调用链", "monorepo"),
    },
    "git": {
        "label": "git、文件快照回滚、子代理工作树",
        "tools": ("git_status", "git_diff", "git_log", "git_commit", "git_checkout", "git_stash",
                  "snapshot_list", "snapshot_restore", "agent_worktree"),
        "keywords": ("git", "commit", "提交", "分支", "branch", "diff", "stash", "checkout", "回滚", "撤销",
                     "还原", "快照", "snapshot", "worktree", "工作树", "merge"),
    },
    "market": {
        "label": "股票/指数实时行情",
        "tools": ("stock_quote",),
        "keywords": ("a股", "港股", "美股", "股市", "股价", "股票", "行情", "大盘", "指数", "涨跌", "涨停", "跌停",
                     "收盘", "开盘", "上证", "深证", "创业板", "科创", "沪深", "恒指", "恒生", "纳指", "纳斯达克",
                     "道指", "标普", "etf", "板块", "市值", "k线"),
    },
    "kb": {
        "label": "内置知识库检索 (用户上传的文档/手册/资料)",
        "tools": ("kb_search",),
        "keywords": ("知识库", "资料库", "文档库", "文档里", "资料里", "手册", "根据资料", "根据文档", "查资料",
                     "内部资料", "规章", "制度", "kb_search"),
    },
    "research": {
        "label": "GitHub/包仓库/StackOverflow/按报错搜解法",
        "tools": ("github_search", "github_repo", "package_search", "github_search_issues",
                  "stackoverflow_search", "search_code_error"),
        "keywords": ("github", "gitee", "开源", "选型", "pypi", "npm", "crate", "stackoverflow", "issue",
                     "报错", "traceback", "exception", "第三方库", "开源库", "依赖包"),
    },
    "voice": {
        "label": "文字转语音 (朗读/配音, 出 mp3)",
        "tools": ("text_to_speech",),
        "keywords": ("朗读", "配音", "语音合成", "合成语音", "文字转语音", "转语音", "转成语音", "tts", "读出来",
                     "念出来", "念一下", "读一遍", "播报", "音色", "发音人", "的声音读", "语音版"),
    },
    "providers": {
        "label": "能力接入: 外部生图/生视频/配乐/通知等服务 (网页「🔌 能力接入」里配置)",
        "tools": ("list_providers", "get_provider", "generate_image", "generate_video", "generate_music", "send_notify"),
        "keywords": ("生图", "文生图", "画图", "画一张", "画一幅", "画一只", "画个", "出图", "生成图片", "图片生成", "ai绘画",
                     "生成视频", "生视频", "文生视频", "图生视频", "视频生成", "配乐", "背景音乐", "bgm", "生成音乐", "做首歌",
                     "发通知", "群机器人", "webhook", "发邮件", "能力接入", "接入的", "provider", "comfyui",
                     "seedream", "seedance", "即梦", "可灵", "海螺", "万相", "searxng", "博查", "tavily", "对象存储"),
    },
    "settings": {
        "label": "大模型配置: 新增/测试/切换默认/删除",
        "tools": ("manage_models",),
        "keywords": ("加模型", "加个模型", "加一个模型", "添加模型", "新增模型", "接入模型", "接入大模型", "换模型",
                     "切换模型", "切模型", "删模型", "删除模型", "模型列表", "默认模型", "模型配置", "api key", "apikey",
                     "api_key", "base_url", "baseurl", "sk-"),
    },
}

TOOL_TO_GROUP: dict[str, str] = {t: g for g, spec in GROUPS.items() for t in spec["tools"]}
def _kw_regex(words) -> re.Pattern:
    """英文关键词按词边界匹配 (否则 report 命中 repo、merged.xlsx 命中 merge); 中文按子串。"""
    parts = []
    for w in words:
        w = w.lower()
        if re.fullmatch(r"[a-z0-9_.\- ]+", w):
            tail = "" if w.endswith("-") else r"(?![a-z0-9_]|\.[a-z0-9])"   # diff.md 这种文件名不算
            parts.append(r"(?<![a-z0-9_.])" + re.escape(w) + tail)
        else:
            parts.append(re.escape(w))
    return re.compile("|".join(parts))


_KW_RE = {g: _kw_regex(spec["keywords"]) for g, spec in GROUPS.items()}
_TOOL_NAME_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, TOOL_TO_GROUP), key=len, reverse=True)) + r")\b")
_ALIAS_STOP = {"local", "localhost", "default", "test", "host", "server"}
_STATE_FILE = ".tool_groups.json"

_lock = threading.Lock()
_sticky: dict[str, set] = {}          # session_id → 已激活组 (内存; 会话目录里有持久副本)
_select_cache: dict[tuple, tuple] = {}   # 组顺序 → (tool_defs, 发送列表)
_alias_cache: list = [0.0, {}]        # [时间戳, {alias: group}]


# ── 配置 ─────────────────────────────────────────────────────

def _cfg() -> dict:
    try:
        from .config import CFG
        raw = (CFG.get("agent") or {}).get("tool_groups", True)
    except Exception:
        raw = True
    if isinstance(raw, dict):
        return {"enabled": bool(raw.get("enabled", True)), "always": tuple(raw.get("always") or ())}
    return {"enabled": bool(raw), "always": ()}


def enabled() -> bool:
    return _cfg()["enabled"]


# ── 检测 ─────────────────────────────────────────────────────

def _aliases() -> dict:
    """已保存的 SSH 主机 / Windows 节点别名 → 组 (60 秒缓存, 读失败就当没有)。"""
    now = time.time()
    if now - _alias_cache[0] < 60:
        return _alias_cache[1]
    out: dict = {}
    try:
        from .config import WORKSPACE
        p = Path(WORKSPACE) / ".secrets/windows_nodes/nodes.json"
        for a in (json.loads(p.read_text()) if p.exists() else {}):
            out[str(a).lower()] = "windows"
    except Exception:
        pass
    try:
        import os
        p = Path(os.environ.get("LITECODE_SSH_HOSTS", "/tmp/litecode_workspace/.secrets/ssh_hosts.json"))
        for a in (json.loads(p.read_text()) if p.exists() else {}):
            out.setdefault(str(a).lower(), "remote")
    except Exception:
        pass
    out = {a: g for a, g in out.items() if len(a) >= 3 and a not in _ALIAS_STOP}
    _alias_cache[0], _alias_cache[1] = now, out
    return out


def tool_names_in(text: str) -> set:
    """文本里出现的组内工具名 → 组。"""
    if not text:
        return set()
    return {TOOL_TO_GROUP[m] for m in _TOOL_NAME_RE.findall(text)}


def detect_user(text: str) -> set:
    """用户侧文本 (用户消息 + 本轮动态上下文): 关键词 / 工具名 / 已知主机别名。"""
    if not text:
        return set()
    low = text.lower()
    hit = {g for g, rx in _KW_RE.items() if rx.search(low)}
    hit |= tool_names_in(text)
    if re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", low):
        hit.add("remote")
    for alias, g in _aliases().items():
        if g not in hit and re.search(r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])", low):
            hit.add(g)
    return hit


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):   # 多模态: 只看文字段
        return "\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return ""


def detect_messages(messages) -> set:
    """对话消息里的激活信号。system 消息跳过 (提示词本身提到很多工具名, 不能当信号)。"""
    hit: set = set()
    for m in messages or ():
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "system":
            continue
        text = _text_of(m.get("content"))
        if role == "user":
            # 只看用户原话; 挂在前面的 <context> 段 (自动匹配的技能正文等) 不算信号:
            # 技能里顺带提到 windows_desktop 之类不代表本任务要用 (实测误匹配的技能一次带进 1 万 t 工具定义)。
            # 真要用时模型调 enable_tools 或直接调组内工具, 下一步就在列表里。
            own = text
            if text.startswith("<context>") and (end := text.find("</context>")) >= 0:
                own = text[end + len("</context>"):]
            hit |= detect_user(own)
        else:
            hit |= tool_names_in(text)
        for tc in m.get("tool_calls") or ():
            try:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                if name in TOOL_TO_GROUP:
                    hit.add(TOOL_TO_GROUP[name])
                elif name == "enable_tools":
                    hit |= parse_groups(fn.get("arguments"))
            except AttributeError:
                continue
    return hit


def parse_groups(raw) -> set:
    """enable_tools 的 groups 参数 (list / 逗号分隔串 / JSON 串) → 合法组名。工具名也接受, 映射到所在组。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            pass
    if isinstance(raw, dict):
        raw = raw.get("groups")
    if isinstance(raw, str):
        raw = re.split(r"[,\s，、]+", raw)
    out = set()
    for x in raw or ():
        x = str(x).strip().lower()
        if x in GROUPS:
            out.add(x)
        elif x in TOOL_TO_GROUP:
            out.add(TOOL_TO_GROUP[x])
        elif x == "all":
            out |= set(GROUPS)
    return out


# ── 会话级状态 (只增不减) ─────────────────────────────────────

def _state_path(session_id: str):
    try:
        from .config import SESSIONS_DISK
        d = Path(SESSIONS_DISK) / session_id
        return d / _STATE_FILE if d.is_dir() else None
    except Exception:
        return None


def _ordered(groups) -> tuple:
    """合法组名去重; list/tuple 保留给定顺序, set 按 GROUPS 顺序 (同一批新加入的组顺序确定)。"""
    seq = groups if isinstance(groups, (list, tuple)) else [g for g in GROUPS if g in (groups or ())]
    out: list = []
    for g in seq:
        if g in GROUPS and g not in out:
            out.append(g)
    return tuple(out)


def _merge(base: tuple, extra) -> tuple:
    return base + tuple(g for g in _ordered(extra) if g not in base)


def active(session_id: str) -> tuple:
    """本会话已激活的组, 按激活先后排 (常驻组在最前)。"""
    always = _ordered(list(_cfg()["always"]))
    if not session_id:
        return always
    with _lock:
        s = _sticky.get(session_id)
        if s is None:
            s = []
            p = _state_path(session_id)
            if p is not None and p.exists():
                try:
                    s = list(_ordered(list(json.loads(p.read_text()))))
                except Exception:
                    s = []
            _sticky[session_id] = s
        return _merge(always, s)


def activate(session_id: str, groups) -> list:
    """把组按顺序追加进会话; 返回本次新加入的组。"""
    if not session_id:
        return []
    active(session_id)   # 确保已从磁盘载入
    with _lock:
        s = _sticky.setdefault(session_id, [])
        new = [g for g in _ordered(groups) if g not in s]
        if new:
            s.extend(new)
            p = _state_path(session_id)
            if p is not None:
                try:
                    p.write_text(json.dumps(s))
                except Exception:
                    pass
    return new


def forget(session_id: str) -> None:
    """测试 / 会话删除用。"""
    with _lock:
        _sticky.pop(session_id, None)


# ── 组装发送列表 ──────────────────────────────────────────────

def select(tool_defs: list, groups) -> list:
    """核心工具 (原顺序) + 已激活组 (按激活先后; 传 set 时按 GROUPS 顺序)。
    新激活的组总排在已有组之后 → 发送列表只在尾部增长, 前面字节不变。同一组合返回同一个 list 对象。"""
    key = _ordered(groups)
    cached = _select_cache.get(key)
    if cached is not None and cached[0] is tool_defs:
        return cached[1]
    by_name = {t["function"]["name"]: t for t in tool_defs}
    out = [t for t in tool_defs if t["function"]["name"] not in TOOL_TO_GROUP]
    for g in key:
        out += [by_name[n] for n in sorted(GROUPS[g]["tools"], key=_order(tool_defs)) if n in by_name]
    _select_cache[key] = (tool_defs, out)
    return out


def _order(tool_defs):
    idx = {t["function"]["name"]: i for i, t in enumerate(tool_defs)}
    return lambda n: idx.get(n, 1 << 30)


def tools_for(ctx, tool_defs: list) -> list:
    """每次调用模型前: 扫描自上次以来的新消息, 更新会话激活组, 返回要发的工具列表。"""
    if not enabled():
        return tool_defs
    msgs = getattr(ctx, "messages", None) or []
    start = getattr(ctx, "tool_groups_scanned", 0) or 0
    if start > len(msgs):   # 历史被裁剪过, 从头再扫 (只增不减, 重复扫无害)
        start = 0
    sid = getattr(ctx, "session_id", None) or ""
    found = detect_messages(msgs[start:])
    try:
        ctx.tool_groups_scanned = len(msgs)
    except Exception:
        pass
    if sid:
        activate(sid, found)
        groups = active(sid)
    else:   # 无会话 (一次性调用): 挂在 ctx 上
        prev = tuple(getattr(ctx, "tool_groups", ()) or ())
        groups = _merge(_merge(_ordered(list(_cfg()["always"])), prev), found)
        try:
            ctx.tool_groups = groups
        except Exception:
            pass
    return select(tool_defs, groups)


def describe() -> str:
    """enable_tools 描述用: 每组一行 (固定文本, 不随会话变化)。"""
    return "\n".join(f"  {g}: {spec['label']} — {', '.join(spec['tools'])}" for g, spec in GROUPS.items())
