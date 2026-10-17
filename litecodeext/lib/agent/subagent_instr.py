"""lib/agent/subagent_instr.py — 子代理历史压缩后重新附上仍有效的主代理补充指令。

问题: agent_control send 的补充指令以 user 消息 "[来自主代理的补充指令] ..." 注入子代理历史;
      子代理每 8 轮做一次滚动摘要 (subagent._rolling_summary), 较早的 user 消息被压成
      "[User] 前 80 字", 且只留最近 15 条摘要 —— 补充指令被截断甚至整条丢失, 子代理会"忘了"主代理的纠正。
做法: 登记表 (subagent_registry) 记下每条已送达的指令原文; 摘要之后调用 pin():
      1. 去掉摘要里那些被截断的补充指令行、去掉上一次附上的固定块 (原文以登记表为准);
      2. 仍完整保留在最近消息里的指令不重复附; 其余按原文去重, 从最新往前取, 总长上限 PIN_MAX 字,
         超出的较早指令只记条数;
      3. 固定块接在摘要消息末尾 (没有摘要消息时作为一条 user 消息插在 system 之后)。
      纯规则, 不调 LLM; 没有补充指令时原样返回。
"""
from __future__ import annotations

from typing import List

PIN_HEAD = "[仍然有效的主代理补充指令 (历史压缩后按原文重新附上, 与前面的任务说明冲突时以这里为准)]"
PIN_MAX = 4000
SUMMARY_HEAD = "[SYSTEM: 以下是前几轮操作的摘要"


def _strip_old(messages: list, instr_prefix: str) -> list:
    """去掉上一次附上的固定块, 以及摘要里被截断的补充指令行。返回新列表 (消息 dict 浅拷贝后再改)。"""
    out = []
    for m in messages:
        c = m.get("content")
        if m.get("role") == "system" or not isinstance(c, str):
            out.append(m)
            continue
        if c.startswith(PIN_HEAD):          # 独立的旧固定块 → 丢弃, 下面按登记表重建
            continue
        new = c.split("\n\n" + PIN_HEAD, 1)[0] if PIN_HEAD in c else c
        if new.startswith(SUMMARY_HEAD) and instr_prefix in new:
            new = "\n".join(ln for ln in new.split("\n")
                            if not (ln.startswith("[User]") and instr_prefix in ln))
        out.append(m if new == c else dict(m, content=new))
    return out


def build_block(instructions: List[str], present_text: str, limit: int = PIN_MAX) -> str:
    """选出需要重新附上的指令并拼成固定块; 都已在最近消息里 → 返回空串。"""
    picked, seen, used, omitted = [], set(), 0, 0
    for text in reversed(instructions or []):       # 最新的优先
        t = str(text or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        if t in present_text:                       # 最近消息里还完整在 → 不重复
            continue
        if used + len(t) > limit:
            omitted += 1
            continue
        picked.append(t)
        used += len(t)
    if not picked:
        return ""
    picked.reverse()                                # 恢复时间顺序
    lines = [PIN_HEAD] + [f"{i}. {t}" for i, t in enumerate(picked, 1)]
    if omitted:
        lines.append(f"(另有较早的 {omitted} 条补充指令因总长上限 {limit} 字未附上; 若与上面冲突以上面为准)")
    return "\n".join(lines)


def pin(messages: list, instructions: List[str], instr_prefix: str) -> list:
    """滚动摘要之后调用: 返回附上仍有效补充指令的新消息列表 (无补充指令时原样返回)。"""
    if not instructions:
        return messages
    msgs = _strip_old(messages, instr_prefix)
    first = next((i for i, m in enumerate(msgs) if m.get("role") != "system"), len(msgs))
    summary_at = first if (first < len(msgs) and isinstance(msgs[first].get("content"), str)
                           and msgs[first]["content"].startswith(SUMMARY_HEAD)) else -1
    present = "\n".join(str(m.get("content") or "") for i, m in enumerate(msgs)
                        if m.get("role") != "system" and i != summary_at)
    block = build_block(instructions, present)
    if not block:
        return msgs
    if summary_at >= 0:
        msgs[summary_at] = dict(msgs[summary_at], content=msgs[summary_at]["content"].rstrip() + "\n\n" + block)
    else:
        msgs.insert(first, {"role": "user", "content": block})
    return msgs
