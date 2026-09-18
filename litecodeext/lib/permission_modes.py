"""
permission_modes.py — Permission 模式分级 (P16)
=================================================
4 种模式: default / plan / acceptEdits / bypass
- default: 默认提示用户
- plan: 强制 plan_only (不允许执行工具)
- acceptEdits: 默认接受文件编辑 (write/patch/apply_blocks)
- bypass: 全自动 (危险, 仅自托管/CI 用)

API:
  set_mode(mode) / get_mode() / is_tool_allowed(tool_name) / require_confirmation(tool_name)
"""
from typing import Optional


_VALID_MODES = ("default", "plan", "acceptEdits", "bypass")
_TOOL_DANGER_LEVEL = {
    # high: 写文件 / 执行命令
    "write_file": "edit", "patch_file": "edit", "apply_blocks": "edit",
    "execute_shell": "exec",
    # low: 只读
    "read_file": "read", "find_files": "read", "search_code": "read",
    "find_symbol": "read", "get_tree": "read",
    # neutral: agent / 工具内部
    "spawn_agent": "agent", "update_global_context": "agent",
}


_current_mode = "default"


def set_mode(mode: str) -> bool:
    global _current_mode
    if mode not in _VALID_MODES:
        return False
    _current_mode = mode
    return True


def get_mode() -> str:
    return _current_mode


def is_tool_allowed(tool_name: str) -> bool:
    """模式下工具是否允许调用"""
    if _current_mode == "bypass":
        return True
    if _current_mode == "plan":
        # plan 模式: 只允许 read 类
        return _TOOL_DANGER_LEVEL.get(tool_name, "neutral") == "read"
    # default / acceptEdits 都允许 (差别在 require_confirmation)
    return True


def require_confirmation(tool_name: str) -> bool:
    """是否需要用户确认 (UI 弹窗)"""
    if _current_mode == "bypass":
        return False
    if _current_mode == "acceptEdits":
        # edit 类不再确认, exec 仍要
        return _TOOL_DANGER_LEVEL.get(tool_name, "neutral") == "exec"
    if _current_mode == "plan":
        # plan 模式不该到这里 (被 is_tool_allowed 拦)
        return True
    # default: edit / exec 都要确认
    return _TOOL_DANGER_LEVEL.get(tool_name, "neutral") in ("edit", "exec")
