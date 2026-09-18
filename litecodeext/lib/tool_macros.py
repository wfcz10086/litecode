"""
tool_macros.py — 工具编排预定义 (P26)
======================================
原子工具组合宏: 给 LLM 一些"做某件事的标准套路", 减少 token 浪费.

每个 macro 定义一个工具组合 + 描述, LLM 可一次调用替代多步:
  - read_then_patch: read_file → patch_file (常见: 读源文件再修改)
  - find_and_read: find_files → read_file (找文件再读)
  - search_then_apply: search_code → apply_blocks (搜后多块改)
  - explore_dir: get_tree → find_files → read_file (探索目录)

实现策略 (极简版):
  - 不真合并工具实现, 仅作为提示模式注入 system prompt
  - LLM 可参考宏顺序, 但实际仍是多步
  - 后续可用 spawn_agent 把组合做成原子操作

API:
  get_macro_prompt() → str   注入 system prompt 的描述
  list_macros() → List[Dict]
"""
from typing import List, Dict


_MACROS: List[Dict] = [
    {
        "name": "read_then_patch",
        "tools": ["read_file", "patch_file"],
        "desc": "读文件 → 找到要改的位置 → patch_file 替换. 适合定点修改.",
        "tip": "patch_file 的 old_str 必须严格匹配 read_file 看到的内容(空白/换行不能错).",
    },
    {
        "name": "find_and_read",
        "tools": ["find_files", "read_file"],
        "desc": "find_files 用 glob 找文件 → read_file 读其中一个/多个.",
        "tip": "find 后挑最可能命中的, 不要 read 全部.",
    },
    {
        "name": "search_then_apply",
        "tools": ["search_code", "apply_blocks"],
        "desc": "search_code 找出所有相关位置 → apply_blocks 一次多块改.",
        "tip": "apply_blocks 是 Aider 风格 SEARCH/REPLACE, 单调用替代多次 patch_file.",
    },
    {
        "name": "explore_dir",
        "tools": ["get_tree", "find_files", "read_file"],
        "desc": "get_tree 看目录结构 → find_files 用 glob 缩小范围 → read_file 读核心.",
        "tip": "陌生项目先 explore, 别上来就 grep.",
    },
    {
        "name": "find_symbol_then_read",
        "tools": ["find_symbol", "read_file"],
        "desc": "find_symbol 拿到 (文件,行号) → read_file 用 lines='N:M' 读上下文.",
        "tip": "比 search_code 准, 比 grep 噪声少.",
    },
]


def list_macros() -> List[Dict]:
    return list(_MACROS)


def get_macro_prompt() -> str:
    """生成可注入 system prompt 的工具组合提示"""
    lines = ["## 工具组合套路 (常见任务的标准做法)"]
    lines.append("以下是高频任务的工具组合, 不是必须严格遵守, 但能减少试错:")
    for m in _MACROS:
        chain = " → ".join(m["tools"])
        lines.append(f"\n**{m['name']}**: {chain}")
        lines.append(f"  用途: {m['desc']}")
        lines.append(f"  注意: {m['tip']}")
    return "\n".join(lines)
