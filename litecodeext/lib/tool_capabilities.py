"""tool_capabilities.py — 主 agent 工具的能力标注 (旁挂表)。

不写进 TOOL_DEFS: 那份 JSON 每轮原样发给模型, 多一个字段就会打掉 KV 前缀缓存。
plugins/tools/*.py 自带的 capabilities 由 lib/tool_registry.py 合并进来。

键:
  read_only  无副作用、可与同批其他只读调用并发 (lib/tool_prefetch.py 据此预取)
  dag        False = 不暴露给 DAG 工具步 (需要主循环上下文或会递归编排)
"""
from __future__ import annotations

_READ_ONLY = (
    "read_file", "get_tree", "find_files", "search_code", "find_symbol", "locate_symbol",
    "web_fetch", "web_search", "github_search_issues", "stackoverflow_search", "search_code_error",
    "tail_log", "list_skills", "git_status", "git_diff", "git_log",
    "list_bg", "list_timers", "snapshot_list", "task_list", "ssh_list_hosts",
)

# spawn_agent / update_map 由主循环特殊处理, 不走 execute_tool;
# DAG 增删改跑由 DAG 自身的 dag 步类型负责, 工具步里再开 DAG 会递归
_NOT_FOR_DAG = (
    "spawn_agent", "update_map", "task_done",
    "create_dag", "update_dag", "delete_dag", "patch_dag_node", "run_dag",
)

CAPABILITIES: dict[str, dict] = {}
for _n in _READ_ONLY:
    CAPABILITIES.setdefault(_n, {})["read_only"] = True
for _n in _NOT_FOR_DAG:
    CAPABILITIES.setdefault(_n, {})["dag"] = False
