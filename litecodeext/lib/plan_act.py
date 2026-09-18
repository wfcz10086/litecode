"""
plan_act.py — Plan/Act 分离模式 (P14)
=======================================
两阶段处理: 先 Plan (只规划不执行) → 用户确认 → Act (执行).

Mode:
  - "plan_only": LLM 仅输出 plan, 工具调用全部禁用, 末尾给 [PLAN_DONE]
  - "act_only":  跳过 plan, 直接执行 (兼容老行为, 默认)
  - "plan_then_act": 先 plan, 再自动 act (无需用户确认; 适合自动化)

API:
  inject_plan_prompt(messages, mode) → 修改后的 messages (加 plan instruction)
  is_plan_done(text) → bool (检查 LLM 是否标 PLAN_DONE)
  extract_plan(text) → str (从 LLM 输出抽 plan 段)

不依赖 LLM, 仅作为 system prompt 注入 + 文本解析.
"""
import re
from typing import List, Dict


_PLAN_INSTRUCTION = """
## Plan/Act Mode: PLAN ONLY
现在是 PLAN 阶段. 你**不能**调用任何工具.
只输出一个清晰的执行计划:

## 执行计划
1. 第一步要做什么 (使用什么工具/读什么文件)
2. 第二步...
3. 第三步...

## 风险与回退
- 可能失败的点 + 怎么回退

## 验证标准
- 怎么确认任务完成

末尾必须独立一行: [PLAN_DONE]
"""

_PLAN_THEN_ACT_INSTRUCTION = """
## Plan/Act Mode: PLAN THEN ACT
先输出执行计划 (## 执行计划 章节), 然后立即开始执行.
计划要包含: 步骤 / 风险 / 验证标准.
计划结束后用 [PLAN_DONE] 分隔, 然后调用工具开始 ACT.
"""


def inject_plan_prompt(messages: List[Dict], mode: str = "act_only") -> List[Dict]:
    """在 messages 开头插一条 system 提示, 切换 plan/act 模式.

    mode:
      - "plan_only": 只规划不执行
      - "plan_then_act": 先规划再执行
      - "act_only" (默认): 不注入, 兼容老行为
    """
    # [v1.8 P36-b] plan_act 埋点
    try:
        from core.telemetry import emit as _emit
        _emit(event="plan_act_inject",
              fields={"mode": mode, "msg_count": len(messages or []),
                      "injected": mode != "act_only"},
              jsonl="plan_act.jsonl")
    except Exception:
        pass
    if mode == "act_only" or not messages:
        return messages

    instruction = ""
    if mode == "plan_only":
        instruction = _PLAN_INSTRUCTION
    elif mode == "plan_then_act":
        instruction = _PLAN_THEN_ACT_INSTRUCTION
    else:
        return messages
    
    # 找已有 system message, append; 没有则 prepend 一条
    new_msgs = list(messages)
    has_system = False
    for i, m in enumerate(new_msgs):
        if m.get("role") == "system":
            new_msgs[i] = {**m, "content": (m.get("content", "") or "") + "\n\n" + instruction}
            has_system = True
            break
    if not has_system:
        new_msgs = [{"role": "system", "content": instruction}] + new_msgs
    return new_msgs


_PLAN_DONE_RE = re.compile(r"\[PLAN_DONE\]", re.IGNORECASE)


def is_plan_done(text: str) -> bool:
    """检查 LLM 输出是否标记 [PLAN_DONE]"""
    return bool(_PLAN_DONE_RE.search(text or ""))


def extract_plan(text: str) -> str:
    """从 LLM 输出抽取 plan 段 (## 执行计划 ... [PLAN_DONE])"""
    if not text:
        return ""
    # 找 ## 执行计划 起始
    m = re.search(r"##\s*执行计划", text)
    if not m:
        return ""
    start = m.start()
    # 找 [PLAN_DONE] 结束
    end_m = _PLAN_DONE_RE.search(text, start)
    end = end_m.start() if end_m else len(text)
    return text[start:end].strip()


def list_modes() -> List[str]:
    return ["act_only", "plan_only", "plan_then_act"]
