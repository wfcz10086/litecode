"""
dag_ai_gen.py — AI 一键生成 DAG (P5-c)
========================================
极速版: 提供一个 prompt 模板, 让 LLM 输出符合 DAG_JSON_SCHEMA 的 JSON.
不真调 LLM, 只做 prompt 构造 + 解析 JSON 输出.

API:
  build_dag_gen_prompt(user_request) → str (供调用方丢给 LLM)
  parse_llm_dag_response(text) → dict (解析 LLM 输出, 提 JSON 块)
"""
import json
import re
from typing import Dict, Optional


_DAG_GEN_TEMPLATE = """你是一个 DAG 编排规划师. 用户给的需求, 请输出一份 DAG JSON.

用户需求:
{request}

输出规则:
1. **只输出 JSON 块**, 用 ```json ... ``` 包裹
2. JSON 必须含 "steps" 数组, 每个 step 含: id (alnum/-/_), task (描述), agent_type (coder/explorer/researcher/analyst/tester/shell/writer/critic 之一), depends_on (id 数组, 可空)
3. 步骤要拆细 (3-8 步通常合适), 利用 depends_on 形成 DAG
4. 同层并行的 step 不要互依赖
5. 末尾可加一个 critic 步骤做总检 (auto_critic 默认会加)

示例输出:
```json
{{
  "steps": [
    {{"id":"s1","task":"分析需求","agent_type":"researcher"}},
    {{"id":"s2","task":"找相关代码","agent_type":"explorer","depends_on":["s1"]}},
    {{"id":"s3","task":"写实现","agent_type":"coder","depends_on":["s2"]}},
    {{"id":"s4","task":"写测试","agent_type":"tester","depends_on":["s3"]}}
  ]
}}
```

现在请为用户需求生成 DAG JSON:
"""


def build_dag_gen_prompt(user_request: str) -> str:
    return _DAG_GEN_TEMPLATE.format(request=(user_request or "").strip())


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.S)


def parse_llm_dag_response(text: str) -> Optional[Dict]:
    """从 LLM 输出抽 JSON. 优先 ```json ``` 块, 否则尝试整段."""
    if not text:
        return None
    # ```json``` 块
    for m in _JSON_BLOCK_RE.finditer(text):
        try:
            return json.loads(m.group(1))
        except Exception:
            continue
    # 尝试整文 (可能 LLM 直接输出 JSON 不包块)
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            return json.loads(text)
        except Exception:
            pass
    return None


# ── NL 修改 DAG ────────────────────────────────────────
_DAG_EDIT_TEMPLATE = """你是 DAG 编辑助手. 用户给了一份现有 DAG 和一条修改指令, 请输出**修改后**的完整 DAG JSON.

现有 DAG:
```json
{existing}
```

修改指令:
{instruction}

输出规则:
1. **只输出 JSON 块**, 用 ```json ... ``` 包裹
2. 保留原 DAG 未提及的 step 不动 (id/task/agent_type/depends_on/tool_name 等)
3. 只对指令涉及的 step 做增/删/改; 若删 step, 同时清理其 depends_on 引用
4. 新增 step 用有意义的 id (alnum/-/_, 3-16 字符), agent_type 只能是 coder/explorer/researcher/analyst/tester/shell/writer/critic/tool 之一
5. 保持 depends_on 无环
6. 输出结构 = {{"steps": [...]}}, 顶层可保留原 name/description

现在请输出修改后的 DAG JSON:
"""


def build_dag_edit_prompt(existing_dag: Dict, instruction: str) -> str:
    return _DAG_EDIT_TEMPLATE.format(
        existing=json.dumps(existing_dag, ensure_ascii=False, indent=2),
        instruction=(instruction or "").strip(),
    )


# ── NL → cron 表达式 ───────────────────────────────────
_NL_CRON_TEMPLATE = """你是 cron 表达式专家. 用户给一句自然语言时间描述, 请输出对应的 5 段 cron 表达式.

用户描述:
{nl}

输出规则:
1. **只输出 JSON 块**, 用 ```json ... ``` 包裹
2. JSON 结构: {{"cron": "分 时 日 月 周", "human": "人类可读的说明"}}
3. 5 段 cron 顺序: 分 时 日 月 周 (0-59 · 0-23 · 1-31 · 1-12 · 0-6, 0=周日)
4. 支持 * (任意), */N (每 N), a-b (范围), a,b (列表)
5. 如果无法确定, cron 设 "" 并在 human 里说明

示例:
- "每天上午 9 点" → {{"cron":"0 9 * * *", "human":"每天 09:00"}}
- "每周一早上 8 点半" → {{"cron":"30 8 * * 1", "human":"每周一 08:30"}}
- "每 15 分钟" → {{"cron":"*/15 * * * *", "human":"每 15 分钟一次"}}
- "工作日下午 6 点" → {{"cron":"0 18 * * 1-5", "human":"周一到周五 18:00"}}

现在请输出:
"""


def build_nl_cron_prompt(nl: str) -> str:
    return _NL_CRON_TEMPLATE.format(nl=(nl or "").strip())
