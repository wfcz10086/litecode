# plugins/tools/remote.py — S1-J (#30)
# 远程 SSH + 远程 Docker 8 件套的 plugin ABI 门面.
#
# 真正实现留在 core/tools/handlers/remote.py (@register 装饰的每个 handler),
# 本文件只做"给 registry 看" 的门面:
#   - 让 registry.list() / openai_tools() 里能扫到 ssh_exec / ssh_read_file /
#     ssh_write_file / ssh_sync_files / ssh_str_replace / docker_remote_ps /
#     docker_remote_inspect / docker_remote_recreate,
#   - schema 从这一份声明生成 (跟 lib/tools/defs/remote_defs.py 同源),
#   - 未来搬 handler 实现进插件目录时, 只改本文件 run 内部, 门面不动.
#
# 输入契约: (args: dict, ctx: dict) → dict
#   ctx.sid 会作为 sid 传入 core.execute_tool.
#
# ⚠ SSH 凭据按 memory 规则 — 走 save_memory/update_profile 存储,
#   本 plugin 不负责凭据持久化, 只做调用透传.

from __future__ import annotations
from typing import Any


async def _delegate(name: str, args: dict, ctx: dict) -> dict:
    """把调用转发给 core.tool_dispatch.execute_tool, 归一 (result, artifact) → dict."""
    try:
        from core.tool_dispatch import execute_tool
    except ImportError:
        from tool_dispatch import execute_tool  # type: ignore
    sid = (ctx or {}).get("sid") or ""
    result, artifact = await execute_tool(name, args or {}, sid)
    out: dict[str, Any] = {"ok": True, "result": result}
    if artifact:
        out["artifact"] = artifact
    return out


# 通用 SSH 连接参数 schema 片段
_SSH_CONN = {
    "host":     {"type": "string",  "description": "目标主机 IP/域名"},
    "port":     {"type": "integer", "description": "SSH 端口, 默认 22", "default": 22},
    "user":     {"type": "string",  "description": "SSH 用户, 默认 root", "default": "root"},
    "password": {"type": "string",  "description": "SSH 密码"},
}


TOOLS = [
    {
        "name": "ssh_exec",
        "description": (
            "SSH 到远程主机执行命令 (paramiko, 密码认证). "
            "用于远程排查/修改远程 docker/vllm/服务. 返回 rc + stdout + stderr."
        ),
        "schema": {
            "type": "object",
            "properties": {
                **_SSH_CONN,
                "cmd":     {"type": "string",  "description": "要执行的 shell 命令 (可 && 串多条)"},
                "timeout": {"type": "integer", "description": "命令超时秒, 默认 60", "default": 60},
            },
            "required": ["host", "password", "cmd"],
        },
        "capabilities": {"safe": False, "long_running": True},
        "run": lambda args, ctx: _delegate("ssh_exec", args, ctx),
    },
    {
        "name": "ssh_read_file",
        "description": "SSH SFTP 读取远程文件. 比 ssh_exec cat 更稳定, 支持大文件.",
        "schema": {
            "type": "object",
            "properties": {
                **_SSH_CONN,
                "path":      {"type": "string",  "description": "远程文件绝对路径"},
                "max_chars": {"type": "integer", "description": "返回最大字符数, 默认 20000", "default": 20000},
            },
            "required": ["host", "password", "path"],
        },
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("ssh_read_file", args, ctx),
    },
    {
        "name": "ssh_write_file",
        "description": (
            "SSH SFTP 写文件到远程主机. 适合改远程 HTML/Python/配置, "
            "比 ssh_exec heredoc 更可靠. 写完可配合 ssh_exec 重启验证."
        ),
        "schema": {
            "type": "object",
            "properties": {
                **_SSH_CONN,
                "path":    {"type": "string",  "description": "远程文件绝对路径"},
                "content": {"type": "string",  "description": "要写入的文件完整内容"},
                "backup":  {"type": "boolean", "description": "写入前备份 (path.bak), 默认 true", "default": True},
            },
            "required": ["host", "password", "path", "content"],
        },
        "capabilities": {"safe": False},
        "run": lambda args, ctx: _delegate("ssh_write_file", args, ctx),
    },
    {
        "name": "ssh_sync_files",
        "description": (
            "SSH SFTP 批量写多个文件 (一次连接). 适合同时改 app.py + index.html + 配置. "
            "files 是 {远程绝对路径: 文件内容} 字典."
        ),
        "schema": {
            "type": "object",
            "properties": {
                **_SSH_CONN,
                "files":  {"type": "object",  "description": "远程路径 → 文件内容"},
                "backup": {"type": "boolean", "description": "写入前备份 (.bak), 默认 true", "default": True},
            },
            "required": ["host", "password", "files"],
        },
        "capabilities": {"safe": False},
        "run": lambda args, ctx: _delegate("ssh_sync_files", args, ctx),
    },
    {
        "name": "ssh_str_replace",
        "description": (
            "SSH 远程文件 str_replace — 等价于 Claude Code str_replace_based_edit_tool. "
            "只传 old_str/new_str 片段, 不必传整个文件. old_str 必须唯一出现 1 次."
        ),
        "schema": {
            "type": "object",
            "properties": {
                **_SSH_CONN,
                "path":    {"type": "string", "description": "远程文件绝对路径"},
                "old_str": {"type": "string", "description": "要替换的原始片段 (必须唯一)"},
                "new_str": {"type": "string", "description": "替换后新内容 (空串=删除)"},
            },
            "required": ["host", "password", "path", "old_str"],
        },
        "capabilities": {"safe": False},
        "run": lambda args, ctx: _delegate("ssh_str_replace", args, ctx),
    },
    {
        "name": "docker_remote_ps",
        "description": "远程 docker ps. 列出目标主机上的容器 (name/image/ports/status).",
        "schema": {
            "type": "object",
            "properties": {
                **_SSH_CONN,
                "all": {"type": "boolean", "description": "含已停止容器", "default": False},
            },
            "required": ["host", "password"],
        },
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("docker_remote_ps", args, ctx),
    },
    {
        "name": "docker_remote_inspect",
        "description": "远程 docker inspect. 查看容器启动 Cmd/Args/Mounts/Env, 支持 --format go template.",
        "schema": {
            "type": "object",
            "properties": {
                **_SSH_CONN,
                "container": {"type": "string", "description": "容器 name 或 id"},
                "format":    {"type": "string", "description": "可选 go template, 如 '{{.Config.Cmd}}'"},
            },
            "required": ["host", "password", "container"],
        },
        "capabilities": {"safe": True},
        "run": lambda args, ctx: _delegate("docker_remote_inspect", args, ctx),
    },
    {
        "name": "docker_remote_recreate",
        "description": (
            "远程 stop+rm+run 替换容器 (改启动参数用). 需要先 inspect 拿原 image/mounts/net/env, "
            "然后组装完整 run_flags 传入."
        ),
        "schema": {
            "type": "object",
            "properties": {
                **_SSH_CONN,
                "container":  {"type": "string", "description": "容器名 (会被 stop+rm)"},
                "image":      {"type": "string", "description": "镜像 id 或 name:tag"},
                "run_flags":  {"type": "string", "description": "docker run 完整 flag 串"},
                "entrypoint": {"type": "string", "description": "可选 --entrypoint 覆盖"},
                "cmd":        {"type": "string", "description": "image 后跟的命令行 (Args)"},
            },
            "required": ["host", "password", "container", "image", "run_flags"],
        },
        "capabilities": {"safe": False, "long_running": True},
        "run": lambda args, ctx: _delegate("docker_remote_recreate", args, ctx),
    },
]

VERSION = "0.1.0"
