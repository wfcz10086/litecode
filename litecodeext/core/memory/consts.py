"""memory/consts.py — 配置与提示词常量.

从 config.json 读 memory + model 段, 计算 soft/hard token 阈值.
COMPACTION_PROMPT: 全量压缩用的 system prompt.
_SMART_MEMORY_PROMPT: smart_auto_memory 判断"值得跨 session 记忆"用的 prompt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ── 从 config.json 读取 memory + model 配置 ────────────────────
_CFG_MEM: dict = {}
_CFG_MODEL: dict = {}
try:
    _cfg_path = Path(__file__).parent.parent / "config.json"
    if _cfg_path.exists():
        _full_cfg = json.loads(_cfg_path.read_text())
        _CFG_MEM = _full_cfg.get("memory", {})
        _CFG_MODEL = _full_cfg.get("model", {})
except Exception:
    pass

_DEFAULT_CONTEXT_WINDOW = int(os.environ.get("CONTEXT_WINDOW", "200000"))
SOFT_TOKEN_LIMIT = _CFG_MEM.get("soft_token_limit") or int(_DEFAULT_CONTEXT_WINDOW * 0.45)
HARD_TOKEN_LIMIT = _CFG_MEM.get("hard_token_limit") or int(_DEFAULT_CONTEXT_WINDOW * 0.75)

# 中英混合 token 估算: CJK 1字≈1.5t, 英文 4chars≈1t → 折中 2.5
CHARS_PER_TOKEN = 2.5
L1_MAX_LINES = 200

MSG_SOFT_COUNT = _CFG_MEM.get("msg_soft_count", 60)
MSG_HARD_COUNT = _CFG_MEM.get("msg_hard_count", 120)

_FORCE_FULL_EVERY = 20  # 每 N 次写盘强制全量校验

COMPACTION_PROMPT = """\
You are compressing a session memory file for a developer AI assistant.
The goal: extract what is truly important and worth remembering across sessions.

<output-format>
## User Intent
What the user is trying to accomplish (1-3 sentences).

## Completed Work
- Bullet: each completed and verified item

## In-Progress
Exact state of anything unfinished. Be precise: paths, IDs, last known state.

## Errors & Corrections
[PROTECTED - NEVER TRUNCATE]
Verbatim error messages + root causes + fixes. Never paraphrase.
Keep ALL entries, even old ones. Format:
- ERROR: <exact error text> | ROOT CAUSE: <what caused it> | FIX: <what fixed it>
Examples of what MUST be kept:
- logger variable overridden by stdlib `logging.getLogger` -> fix: remove the override line
- PYTHONPATH required: always use `cd /project && PYTHONPATH=. python3 ...`
- (exit -15)/(exit -9) are NOT errors, they are normal signal termination
- [STDERR_WARNINGS - exit 0] is NOT an error, command succeeded

## Key References
[PROTECTED - NEVER TRUNCATE]
- Exact paths, URLs, IDs, API keys, hostnames
- Specific values: numbers, dates, configs, port numbers
- Environment requirements: PYTHONPATH patterns, startup commands

## Next Steps
1. Numbered actionable next steps

## Question Log
Chronological list of what the user asked (up to 50 items, one per line, keep concise).
Format: Q1: <question summary> | Q2: <question summary> ...

## Code Architecture (only if coding work happened)
- Project root path
- Module map: file -> one-line responsibility
- Key interfaces changed: function signatures that callers depend on
- Known bugs fixed: symptom + root cause (verbatim error if available)
- Architecture decisions: why X was chosen over Y
- Startup command: exact command to start the service
</output-format>

<rules>
ALWAYS keep verbatim: file paths, error messages, user corrections, specific values.
NEVER truncate or summarize Errors & Corrections or Key References sections.
OMIT: pleasantries, redundant context, intermediate steps no longer relevant.
Weight RECENT messages more than older ones.
Keep total output under 150 lines (increased from 100 to preserve error history).
Respond ONLY with the compressed content, no preamble.
</rules>

{content}
"""

SMART_MEMORY_PROMPT = """\
你是记忆提取器。只提取 0-2 条真正值得跨 session 长期保留的信息。

强制约束:
- items 最多 2 条; 每条 content ≤ 200 字符
- 命中下列任一才可提取, 否则 worth=false
- 不要泛化、不要总结、不要重述用户问题

section 允许的分类（严格按此选）:
- Vault: SSH 配置/凭据、命令行命令、URL、IP:port、密钥/token、配置文件片段
- UserIntent: 用户明确表达的长期目标或身份 ("我是XX工程师"/"我在做YY项目")
- Completed: 已验证完成的里程碑 (含验证方式)
- Correction: 用户直接纠正 ("不要用XX"/"改成YY") + 原因
- Reference: 外部资源指针 (Linear/Jira/仓库/文档 URL + 用途)

以下一律 worth=false:
- 一次性问答、闲聊、调试过程
- 通用知识、模糊描述、无具体值
- 你不确定的信息

输出严格 JSON:
{{"worth": true, "items": [{{"section": "Vault|UserIntent|Completed|Correction|Reference", "content": "≤200字符具体内容"}}]}}
或:
{{"worth": false}}

只输出 JSON, 不要 markdown, 不要说明。

---对话片段---
{conversation}
"""
