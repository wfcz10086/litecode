"""lib/tools/defs — 49 个 OpenAI function-calling 工具定义 (#52 T-52a: 1323 行拆包).

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
)

__all__ = [
    "TOOL_DEFS",
    "SHELL_DEFS", "WEB_DEFS", "MEDIA_DEFS", "CODE_DEFS",
    "AGENT_DEFS", "WORKFLOW_DEFS", "PROJECT_DEFS", "REMOTE_DEFS",
    "GIT_DEFS", "SNAPSHOT_DEFS", "TASK_DEFS", "SSH_HOSTS_DEFS",
]
