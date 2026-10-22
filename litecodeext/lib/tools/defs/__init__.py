"""lib/tools/defs — OpenAI function-calling 工具定义 (#52 T-52a: 1323 行拆包; 之后按域追加).

API 兼容: TOOL_DEFS 保留原名, 顺序按域重排 (无 TOOL_DEFS[N] / .index() 依赖, 已核实).
"""
from .shell_defs import SHELL_DEFS
from .web_defs import WEB_DEFS
from .media_defs import MEDIA_DEFS
from .code_defs import CODE_DEFS
from .agent_defs import AGENT_DEFS
from .workflow_defs import WORKFLOW_DEFS
from .project_defs import PROJECT_DEFS
from .remote_defs import REMOTE_DEFS
from .git_defs import GIT_DEFS
from .snapshot_defs import SNAPSHOT_DEFS
from .task_defs import TASK_DEFS
from .ssh_hosts_defs import SSH_HOSTS_DEFS
from .browser_ref_defs import BROWSER_REF_DEFS
from .windows_desktop_defs import WINDOWS_DESKTOP_DEFS
from .windows_nodes_defs import WINDOWS_NODES_DEFS
from .windows_system_defs import WINDOWS_SYSTEM_DEFS
from .research_defs import RESEARCH_DEFS
from .tool_group_defs import TOOL_GROUP_DEFS
from .settings_defs import SETTINGS_DEFS
from .kb_defs import KB_DEFS

TOOL_DEFS = (
    SHELL_DEFS
    + WEB_DEFS
    + MEDIA_DEFS
    + CODE_DEFS
    + AGENT_DEFS
    + WORKFLOW_DEFS
    + PROJECT_DEFS
    + REMOTE_DEFS
    + GIT_DEFS
    + SNAPSHOT_DEFS
    + TASK_DEFS
    + SSH_HOSTS_DEFS
    + BROWSER_REF_DEFS
    + WINDOWS_DESKTOP_DEFS
    + WINDOWS_NODES_DEFS
    + WINDOWS_SYSTEM_DEFS
    + RESEARCH_DEFS   # 2026-10-20: 追加在末尾, 不打乱前面工具的 KV 前缀
    + TOOL_GROUP_DEFS   # 2026-10-21: enable_tools (低频工具组按需暴露, lib/tool_groups.py)
    + SETTINGS_DEFS   # 2026-10-22: text_to_speech / manage_models (按需组 voice / settings, 不进常驻前缀)
    + KB_DEFS   # 2026-10-22: kb_search (内置知识库, 按需组 kb)
)

__all__ = [
    "TOOL_DEFS",
    "SHELL_DEFS", "WEB_DEFS", "MEDIA_DEFS", "CODE_DEFS",
    "AGENT_DEFS", "WORKFLOW_DEFS", "PROJECT_DEFS", "REMOTE_DEFS",
    "GIT_DEFS", "SNAPSHOT_DEFS", "TASK_DEFS", "SSH_HOSTS_DEFS", "BROWSER_REF_DEFS", "WINDOWS_DESKTOP_DEFS",
    "WINDOWS_NODES_DEFS", "WINDOWS_SYSTEM_DEFS", "RESEARCH_DEFS", "TOOL_GROUP_DEFS", "SETTINGS_DEFS", "KB_DEFS",
]
