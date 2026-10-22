"""lib/session_model.py — 会话级模型 (对话里切模型, 不动全局默认).

存储: WORKSPACE/session_models.json  {sid: model_id}
  - 只有 web_ui 写 (PUT /api/sessions/{sid}/model 和 /model 指令), litecode_server 每轮读。
  - 不放进会话 JSON: web 缓存会被 server 会话整份重导 (web_ui.load_session), 字段会丢。
解析: 每轮现读 config.json 的 models[] (lib.config.find_model_entry); 配置里已删掉的 id
      → 本轮回落全局默认, 并给出提示。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from lib.config import WORKSPACE, find_model_entry, load_cfg

STORE: Path = WORKSPACE / "session_models.json"
_lock = threading.Lock()

CLEAR_WORDS = {"default", "auto", "reset", "none", "默认", "跟随默认", "全局"}


def _read() -> dict:
    try:
        d = json.loads(Path(STORE).read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def get_model_id(sid: Optional[str]) -> str:
    """会话指定的模型 id; 没指定返回 ""。"""
    if not sid:
        return ""
    v = _read().get(sid)
    return v if isinstance(v, str) else ""


def set_model_id(sid: str, model_id: Optional[str]) -> None:
    """model_id 为空 / None = 跟随全局默认 (删掉记录)。"""
    from lib.atomic_io import atomic_write_json
    with _lock:
        d = _read()
        if model_id:
            d[sid] = model_id
        else:
            d.pop(sid, None)
        atomic_write_json(STORE, d)


def resolve(sid: Optional[str], cfg: Optional[dict] = None) -> tuple[dict, str, str]:
    """返回 (本轮用的模型条目 or {}, 提示, 会话记录的 id)。
    条目为 {} 表示跟随全局默认; 提示非空表示记录的模型已不在配置里。"""
    mid = get_model_id(sid)
    if not mid:
        return {}, "", ""
    entry = find_model_entry(mid, cfg=cfg)
    if entry:
        return entry, "", mid
    return {}, f"本会话指定的模型 {mid} 已不在配置中, 本轮改用全局默认模型", mid


# ── /model 指令 ────────────────────────────────────────────────

def parse_command(text: str) -> Optional[str]:
    """`/model` → "" (列出); `/model xxx` → "xxx"; 其它消息 → None。"""
    t = (text or "").strip()
    if t == "/model":
        return ""
    if t.startswith("/model ") or t.startswith("/model\t"):
        return t[len("/model"):].strip()
    return None


def _label(m: dict) -> str:
    return str(m.get("label") or m.get("id") or "")


def match_model(arg: str, models: list) -> tuple[Optional[dict], list]:
    """按 id / label 匹配: 先精确, 再子串 (不区分大小写)。返回 (唯一命中, 候选列表)。"""
    a = arg.strip().lower()
    if not a:
        return None, []
    for key in ("id", "label"):
        hit = [m for m in models if str(m.get(key) or "").lower() == a]
        if len(hit) == 1:
            return hit[0], hit
    sub = [m for m in models if a in str(m.get("id") or "").lower() or a in _label(m).lower()]
    # 同 id 多条 (直连 + 网关) 只算一个候选
    uniq = list({m.get("id"): m for m in sub}.values())
    if len(uniq) == 1:
        return uniq[0], uniq
    return None, uniq


def handle_command(sid: str, text: str, active_id: str = "") -> Optional[str]:
    """消息是 /model 指令就执行并返回回复文本; 不是返回 None (照常交给模型)。"""
    arg = parse_command(text)
    if arg is None:
        return None
    try:
        models = [m for m in (load_cfg().get("models") or []) if isinstance(m, dict) and m.get("id")]
    except Exception:
        models = []
    cur = get_model_id(sid)

    def _listing() -> str:
        lines = []
        for m in models:
            mark = " ← 本会话" if m.get("id") == cur else ""
            lab = _label(m)
            lines.append(f"- `{m['id']}`" + (f" ({lab})" if lab != m["id"] else "") + mark)
        return "\n".join(lines) or "(配置里没有模型)"

    if arg == "":
        now = f"`{cur}`" if cur else f"跟随默认 (`{active_id or '?'}`)"
        return (f"本会话当前模型: {now}\n\n可选模型:\n{_listing()}\n\n"
                f"用法: `/model <id 或名称片段>` 切换本会话; `/model default` 恢复跟随默认。")
    if arg.lower() in CLEAR_WORDS:
        set_model_id(sid, None)
        return f"✓ 本会话改为跟随默认模型 (`{active_id or '?'}`), 下一条消息起生效。"
    hit, cands = match_model(arg, models)
    if hit is None:
        if cands:
            names = "\n".join(f"- `{m['id']}` ({_label(m)})" for m in cands)
            return f"“{arg}” 匹配到多个模型, 请写得更具体:\n{names}"
        return f"没找到模型 “{arg}”。可选模型:\n{_listing()}"
    set_model_id(sid, hit["id"])
    return (f"✓ 本会话已切换到 `{hit['id']}`" +
            (f" ({_label(hit)})" if _label(hit) != hit["id"] else "") +
            ", 下一条消息起生效; 其它会话仍用默认模型。")


# ── 会话级思考强度 [session-thinking 2026-10-21] ─────────────────────
# 输入框「🧠」原来 PATCH 全局 config (影响所有会话 + 微信); 现在只记在本会话。
# 存储: WORKSPACE/session_thinking.json {sid: "off"|"low"|"mid"|"high"}; 没记录 = 跟随全局默认。
# 生效: gateway/openai_compat 本轮把 enable_thinking / thinking_budget 叠到 _TURN_MODEL 覆盖层,
# thinking_adapter 现读 _LIVE 时就拿到本会话的值, 不改全局、不影响并发的其它会话。
THINK_STORE: Path = WORKSPACE / "session_thinking.json"
THINK_LEVELS = {
    "off": {"enable_thinking": False, "thinking_budget": 0},
    "low": {"enable_thinking": True, "thinking_budget": 2048},
    "mid": {"enable_thinking": True, "thinking_budget": 4096},
    "high": {"enable_thinking": True, "thinking_budget": 16384},
}


def _read_think() -> dict:
    try:
        d = json.loads(Path(THINK_STORE).read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def get_thinking(sid: Optional[str]) -> str:
    """会话指定的思考强度 (off/low/mid/high); 没指定返回 ""。"""
    if not sid:
        return ""
    v = _read_think().get(sid)
    return v if v in THINK_LEVELS else ""


def set_thinking(sid: str, level: Optional[str]) -> None:
    """level 为空 / None = 跟随全局默认; 非法值抛 ValueError。"""
    if level and level not in THINK_LEVELS:
        raise ValueError(f"unknown thinking level: {level}")
    from lib.atomic_io import atomic_write_json
    with _lock:
        d = _read_think()
        if level:
            d[sid] = level
        else:
            d.pop(sid, None)
        atomic_write_json(THINK_STORE, d)


def thinking_overlay(sid: Optional[str]) -> Optional[dict]:
    """本轮要叠到 _TURN_MODEL 上的思考参数; 会话没指定返回 None。"""
    lv = get_thinking(sid)
    return dict(THINK_LEVELS[lv]) if lv else None


def level_of(enable: bool, budget: int) -> str:
    """全局 (enable_thinking, thinking_budget) → 档位名, 口径同前端 _syncThinkChip。"""
    if not enable:
        return "off"
    b = int(budget or 0)
    return "low" if b <= 2048 else ("mid" if b <= 8000 else "high")
