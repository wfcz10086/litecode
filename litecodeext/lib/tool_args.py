"""tool_args.py — 模型给出的工具参数 (JSON 字符串) → dict。

小模型 (尤其量化版) 常见的坏参数: 包在 ```json 围栏里、被二次 JSON 编码成字符串、
长代码里裸换行/未转义引号。能无损恢复的就地恢复; 恢复不了的返回一条可行动的错误,
由调用方作为工具结果回给模型重发, 而不是静默当成空参数 (那样模型只会看到"缺字段")。
"""
from __future__ import annotations

import json
import re

_FENCE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.S)


def _loads_obj(s: str):
    v = json.loads(s)
    if isinstance(v, str):
        v = json.loads(v)
    return v


def parse_tool_args(raw, tool_name: str = "") -> tuple[dict, str | None]:
    """返回 (args, error)。error 非 None 时 args 为 {}, error 是给模型看的说明。"""
    if isinstance(raw, dict):
        return raw, None
    text = (raw or "").strip()
    if not text:
        return {}, None
    candidates = [text]
    m = _FENCE.match(text)
    if m:
        candidates.append(m.group(1).strip())
    candidates.append(text.replace("\r\n", "\n"))

    first_err = None
    for cand in candidates:
        try:
            v = _loads_obj(cand)
        except json.JSONDecodeError as e:
            first_err = first_err or e
            continue
        except Exception as e:
            first_err = first_err or e
            continue
        if isinstance(v, dict):
            return v, None
        return {}, (f"ERROR: 工具 {tool_name or '?'} 的参数必须是 JSON 对象 {{...}}, "
                    f"收到的是 {type(v).__name__}。请按工具 schema 重新调用。")

    if isinstance(first_err, json.JSONDecodeError):
        pos = first_err.pos
        near = text[max(0, pos - 40):pos + 40].replace("\n", "⏎")
        where = f"第 {pos} 个字符附近 ({first_err.msg}): …{near}…"
    else:
        where = str(first_err)
    return {}, (f"ERROR: 工具 {tool_name or '?'} 的参数不是合法 JSON, 本次未执行。{where}\n"
                f"请重新调用: arguments 必须是一个 JSON 对象; 字符串里的双引号写成 \\\", "
                f"换行写成 \\n。内容很长 (如整段代码) 时可以分多次写入。")
