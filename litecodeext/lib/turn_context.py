"""turn_context.py — 每轮动态上下文: 随用户消息变化的提示块不进 system prompt。

ollama / llama.cpp 只能复用"与上一次请求逐字相同的最长前缀"。模板里 system
后面紧跟约 5 万字的 tools 定义, system 里任何一处按消息变化 (委派指令 / 匹配技能
/ 记忆召回), 下一轮就要把 system + tools + 全部历史重新 prefill。

这里把这类内容收集成一段 <context> 前缀, 挂在本轮 user 消息前面:
  - 各来源用 register(name, fn, order) 插拔注册, fn(TurnInput) -> str, 空串表示无内容;
  - 渲染结果另存在 user 消息的 KEY 字段里随历史持久化 (content 仍是用户原话,
    界面显示不受影响), 下一轮 materialize() 再拼回去, 模型看到的历史与上一轮
    请求逐字一致, 缓存在这条消息处不会断。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("openclaw")

KEY = "turn_context"
_OPEN, _CLOSE = "<context>", "</context>"


@dataclass
class TurnInput:
    user_message: str
    session_id: str = ""
    history: list = field(default_factory=list)


Source = Callable[[TurnInput], str]
_SOURCES: dict[str, tuple[int, Source]] = {}


def register(name: str, fn: Source, order: int = 50) -> None:
    """同名重复注册会覆盖 (模块热重载安全)。order 小的排前面。"""
    _SOURCES[name] = (order, fn)


def unregister(name: str) -> None:
    _SOURCES.pop(name, None)


def sources() -> list[str]:
    return [n for n, _ in sorted(_SOURCES.items(), key=lambda kv: kv[1][0])]


def collect(inp: TurnInput) -> list[tuple[str, str]]:
    """按 order 跑所有来源; 单个来源异常只记日志, 不影响其他来源和本轮对话。"""
    out = []
    for name in sources():
        try:
            text = (_SOURCES[name][1](inp) or "").strip()
        except Exception as e:
            log.warning(f"  [turn-context] source {name} failed: {e}")
            continue
        if text:
            out.append((name, text))
    return out


def render(blocks: list[tuple[str, str]]) -> str:
    if not blocks:
        return ""
    body = "\n\n".join(text for _, text in blocks)
    return f"{_OPEN}\n以下是系统为本条消息附加的上下文, 不是用户原话:\n\n{body}\n{_CLOSE}"


def build(inp: TurnInput) -> str:
    return render(collect(inp))


def attach(content: Any, ctx: str) -> Any:
    """把 ctx 拼到 user content 前。content 可以是字符串或多模态 part 列表。"""
    if not ctx:
        return content
    if isinstance(content, str):
        return f"{ctx}\n\n{content}"
    if isinstance(content, list):
        return [{"type": "text", "text": ctx}] + list(content)
    return content


def materialize(history: list) -> list:
    """带 KEY 的历史消息拼回发送时的样子 (返回副本, 不改会话缓存里共享的 dict)。"""
    out = []
    for m in history:
        if isinstance(m, dict) and KEY in m:
            ctx = m[KEY]
            m = {k: v for k, v in m.items() if k != KEY}
            m["content"] = attach(m.get("content") or "", ctx)
        out.append(m)
    return out


def strip(history: list) -> list:
    """materialize 的逆操作: 去掉 user 消息前的 <context> 段 (给记忆提取等只该看用户原话的地方)。"""
    out = []
    for m in history:
        c = m.get("content") if isinstance(m, dict) and m.get("role") == "user" else None
        if isinstance(c, str) and c.startswith(_OPEN) and (end := c.find(_CLOSE)) >= 0:
            m = dict(m, content=c[end + len(_CLOSE):].lstrip("\n"))
        elif isinstance(c, list) and c and isinstance(c[0], dict) and str(c[0].get("text", "")).startswith(_OPEN):
            m = dict(m, content=c[1:])
        out.append(m)
    return out


def seen_in(history: list) -> str:
    """历史里已经附加过的全部上下文文本 (来源据此去重, 不在每轮重复注入同一条)。"""
    return "\n".join(m[KEY] for m in history if isinstance(m, dict) and m.get(KEY))
