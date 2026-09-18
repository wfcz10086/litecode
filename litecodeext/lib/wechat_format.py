"""lib/wechat_format.py — 微信桥出站格式化家族（从 wechat_bridge.py 抽取, P2-7）
消息分段 / Markdown 去标记 / 超长代码块摘要 / Agent 回复格式化为微信友好文本。
纯搬运，逻辑未变。
"""
import re
from typing import List

_MAX_WX_MSG_LEN = 2000                # 微信单条消息长度上限（保守值）


# ── 消息分段 ──────────────────────────────────────────────────
def _split_message(text: str, max_len: int = _MAX_WX_MSG_LEN) -> List[str]:
    """智能分段：优先按段落/换行切分，避免截断代码块"""
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = max_len
        for sep in ["\n\n", "\n", "。", ".", "！", "!", "？", "?", "；", ";", " "]:
            pos = remaining[:max_len].rfind(sep)
            if pos > max_len * 0.3:
                cut = pos + len(sep)
                break
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


# ── 微信回复格式化（非流式，工具调用优美展示）────────────────
_TOOL_ICONS = {
    "execute_shell": "▶️",
    "write_file":    "📝",
    "read_file":     "📖",
    "search":        "🔍",
    "browser":       "🌐",
    "deep_search":   "🔬",
    "patch_file":    "🔧",
}


def _strip_markdown(text: str) -> str:
    """
    将 Markdown 格式转为微信友好的纯文本：
    - 去掉 **bold** / *italic* 标记
    - 把 ```代码块``` 转为缩进格式
    - 把 ### 标题 转为 【标题】
    - 把 | 表格 | 转为对齐文本
    - 保留列表符号
    """
    # ── 预处理: Markdown 表格 → 纯文本 ──
    table_lines = []
    non_table_parts = []
    current_non_table = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3:
            if current_non_table:
                non_table_parts.append(("\n".join(current_non_table), None))
                current_non_table = []
            table_lines.append(stripped)
        else:
            if table_lines:
                non_table_parts.append((None, table_lines))
                table_lines = []
            current_non_table.append(line)
    if table_lines:
        non_table_parts.append((None, table_lines))
    if current_non_table:
        non_table_parts.append(("\n".join(current_non_table), None))

    rebuilt = []
    for text_part, tbl in non_table_parts:
        if text_part is not None:
            rebuilt.append(text_part)
        elif tbl:
            # 解析表格行 → 纯文本列表
            for row in tbl:
                cells = [c.strip() for c in row.strip("|").split("|")]
                # 跳过分隔行 (|---|---|)
                if all(set(c) <= {"-", ":", " "} for c in cells):
                    continue
                # 第一行视为表头，后续行为数据
                rebuilt.append("  ".join(cells))

    text = "\n".join(rebuilt)

    lines = text.split("\n")
    result = []
    in_code = False
    code_lang = ""

    for line in lines:
        # 代码块开始/结束
        if line.strip().startswith("```"):
            if not in_code:
                in_code = True
                code_lang = line.strip()[3:].strip()
                if code_lang:
                    result.append(f"── {code_lang} ──")
                else:
                    result.append("── code ──")
            else:
                in_code = False
                result.append("──────")
            continue

        if in_code:
            # 代码行保持原样（不做 markdown 处理）
            result.append(f"  {line}")
            continue

        # 标题 → 【标题】
        if line.startswith("### "):
            result.append(f"【{line[4:].strip()}】")
        elif line.startswith("## "):
            result.append(f"【{line[3:].strip()}】")
        elif line.startswith("# "):
            result.append(f"【{line[2:].strip()}】")
        else:
            # 去掉内联 markdown 标记
            cleaned = line
            cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)   # **bold**
            cleaned = re.sub(r'\*(.+?)\*', r'\1', cleaned)       # *italic*
            cleaned = re.sub(r'`(.+?)`', r'\1', cleaned)         # `code`
            cleaned = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1(\2)', cleaned)  # [text](url)
            result.append(cleaned)

    return "\n".join(result)


def _summarize_code_blocks(text: str, max_code_lines: int = 30) -> str:
    """
    如果回复中有超长代码块，截断并提示用户代码已保存。
    微信不适合展示超长代码。
    """
    lines = text.split("\n")
    result = []
    in_code = False
    code_lines = 0
    code_truncated = False

    for line in lines:
        if line.strip().startswith("```"):
            if not in_code:
                in_code = True
                code_lines = 0
                code_truncated = False
                result.append(line)
            else:
                in_code = False
                if code_truncated:
                    result.append(f"  ... (共 {code_lines} 行，已省略)")
                result.append(line)
            continue

        if in_code:
            code_lines += 1
            if code_lines <= max_code_lines:
                result.append(line)
            elif not code_truncated:
                code_truncated = True
        else:
            result.append(line)

    return "\n".join(result)


def _format_wx_reply(reply_text: str, agent_result: dict) -> str:
    """
    将 Agent 回复 + 工具调用信息格式化为微信友好的文本。

    微信不是流式展示，所以 tool call 应作为「执行摘要」
    放在回复正文前面，让用户知道 AI 做了什么操作。
    """
    tools = agent_result.get("tools", [])
    diffs = agent_result.get("diffs", [])
    usage = agent_result.get("usage")

    parts = []

    # ── 工具调用摘要 ──
    if tools:
        # 去重 + 截断过长的 detail
        seen = set()
        unique_tools = []
        for t in tools:
            short = t[:120]
            if short not in seen:
                seen.add(short)
                unique_tools.append(t)

        if unique_tools:
            lines = ["🔧 执行操作:"]
            max_show = 8
            for i, t in enumerate(unique_tools[:max_show]):
                # 解析工具名和参数: "execute_shell: ls -la"
                colon = t.find(": ")
                if colon > 0:
                    tool_name = t[:colon].strip()
                    tool_arg = t[colon+2:].strip()
                else:
                    tool_name = t.strip()
                    tool_arg = ""

                icon = _TOOL_ICONS.get(tool_name, "⚙️")
                connector = "└" if i == min(len(unique_tools), max_show) - 1 else "├"
                arg_display = tool_arg[:60] + ("…" if len(tool_arg) > 60 else "")
                lines.append(f"  {connector} {icon} {tool_name}" +
                             (f": {arg_display}" if arg_display else ""))

            if len(unique_tools) > max_show:
                lines.append(f"  └ … 共 {len(unique_tools)} 步")
            parts.append("\n".join(lines))

    # ── 文件变更摘要 ──
    if diffs:
        diff_lines = []
        for dv in diffs[:5]:
            fp = (dv.get("filepath", "") or "").split("/")[-1]
            df = dv.get("diff", "")
            adds = len(re.findall(r"^\+[^+]", df, re.MULTILINE))
            dels = len(re.findall(r"^-[^-]", df, re.MULTILINE))
            diff_lines.append(f"  📄 {fp}  +{adds} -{dels}")
        if diff_lines:
            parts.append("📋 文件变更:\n" + "\n".join(diff_lines))

    # ── 正文（去 markdown + 代码摘要）──
    if parts:
        parts.append("━" * 18)
    # 先截断超长代码块，再去掉 markdown 标记
    cleaned_reply = _summarize_code_blocks(reply_text, max_code_lines=30)
    cleaned_reply = _strip_markdown(cleaned_reply)
    parts.append(cleaned_reply)

    # ── Token 统计（紧凑显示）──
    if usage:
        pt = usage.get("prompt_tokens", 0)
        ct = usage.get("completion_tokens", 0)
        elapsed = usage.get("elapsed_seconds", 0)
        iters = usage.get("iterations", 1)
        stats_parts = [f"📊 Token: {pt+ct:,}"]
        if iters and iters > 1:
            stats_parts.append(f"{iters}轮")
        if elapsed:
            stats_parts.append(f"{elapsed}s")
        parts.append(" · ".join(stats_parts))

    return "\n".join(parts)
