"""写类/操作类工具名单: agent 主循环各守卫闸判定"有没有在推进"的同一份来源。"""

# [READONLY]/[EXEC-GATE] 共用: "算改了代码文件"的写类工具名单 (单一来源, 避免两处各写一份漂移)。
# 不含 ssh_write_file (远程写不产生本地 git diff), 不含 create_dag/create_skill/create_timer/
# patch_dag_node/task_create (不改代码文件本身)。
_WRITE_TOOL_NAMES = ("write_file", "patch_file", "apply_blocks", "edit_symbol")
_WRITE_TOOL_NAMES_STR = " / ".join(_WRITE_TOOL_NAMES)  # 提醒文案里展示用, 同一份来源
# 操作类工具: 产出在远端/桌面/网页上而不在本地磁盘, 用过即视为在推进, 豁免写代码那套 PLAN/EXEC/READONLY 闸
_ACTION_TOOL_PREFIXES = ("windows_desktop", "desktop_", "ssh_", "docker_remote_")
_ACTION_TOOL_NAMES = ("browser_act", "browser_login")


def _is_action_tool(name: str) -> bool:
    return name in _ACTION_TOOL_NAMES or name.startswith(_ACTION_TOOL_PREFIXES)
