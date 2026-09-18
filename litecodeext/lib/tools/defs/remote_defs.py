"""remote_defs.py — SSH + Docker 远程管理工具定义 (#30 + #44)."""

REMOTE_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "ssh_exec",
            "description": "SSH 到远程主机执行命令 (基于 paramiko, 密码认证). 用于远程排查/修改远程 docker/vllm/服务. 返回 rc + stdout + stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":     {"type": "string", "description": "目标主机 IP/域名"},
                    "port":     {"type": "integer", "description": "SSH 端口, 默认 22", "default": 22},
                    "user":     {"type": "string", "description": "SSH 用户, 默认 root", "default": "root"},
                    "password": {"type": "string", "description": "SSH 密码"},
                    "cmd":      {"type": "string", "description": "要执行的 shell 命令 (可 && 串多条)"},
                    "timeout":  {"type": "integer", "description": "命令超时秒, 默认 60", "default": 60},
                },
                "required": ["host", "password", "cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_remote_ps",
            "description": "远程 docker ps. 列出目标主机上的容器 (name/image/ports/status).",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":     {"type": "string"},
                    "port":     {"type": "integer", "default": 22},
                    "user":     {"type": "string", "default": "root"},
                    "password": {"type": "string"},
                    "all":      {"type": "boolean", "description": "含已停止容器", "default": False},
                },
                "required": ["host", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_remote_inspect",
            "description": "远程 docker inspect. 用于查看容器的启动 Cmd/Args/Mounts/Env, 支持自定义 --format go template.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":      {"type": "string"},
                    "port":      {"type": "integer", "default": 22},
                    "user":      {"type": "string", "default": "root"},
                    "password":  {"type": "string"},
                    "container": {"type": "string", "description": "容器 name 或 id"},
                    "format":    {"type": "string", "description": "可选 go template, 例如 '{{.Config.Cmd}}'"},
                },
                "required": ["host", "password", "container"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_remote_recreate",
            "description": "远程 stop+rm+run 替换容器 (改启动参数用). 典型场景: 改 vllm --max-model-len. 需要事先用 docker_remote_inspect 拿到原 image/mounts/net/env, 然后组装完整 run_flags 传入.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":       {"type": "string"},
                    "port":       {"type": "integer", "default": 22},
                    "user":       {"type": "string", "default": "root"},
                    "password":   {"type": "string"},
                    "container":  {"type": "string", "description": "容器名 (会被 stop+rm)"},
                    "image":      {"type": "string", "description": "镜像 id 或 name:tag"},
                    "run_flags":  {"type": "string", "description": "docker run 完整 flag 串, 如 '-d --name X --restart unless-stopped --gpus all -p 8000:8000 -v /a:/b'"},
                    "entrypoint": {"type": "string", "description": "可选 --entrypoint 覆盖"},
                    "cmd":        {"type": "string", "description": "image 后跟的命令行 (Args)"},
                },
                "required": ["host", "password", "container", "image", "run_flags"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_write_file",
            "description": "SSH 写文件到远程主机 (基于 paramiko SFTP). 把 content 写入远程 path. 适合改远程 HTML/Python/配置文件, 比 ssh_exec heredoc 更可靠. 写完可配合 ssh_exec 重启服务验证.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":     {"type": "string", "description": "目标主机 IP/域名"},
                    "port":     {"type": "integer", "description": "SSH 端口, 默认 22", "default": 22},
                    "user":     {"type": "string", "description": "SSH 用户, 默认 root", "default": "root"},
                    "password": {"type": "string", "description": "SSH 密码"},
                    "path":     {"type": "string", "description": "远程文件绝对路径, 如 /home/gua/templates/index.html"},
                    "content":  {"type": "string", "description": "要写入的文件完整内容"},
                    "backup":   {"type": "boolean", "description": "写入前先备份原文件 (path.bak), 默认 true", "default": True},
                },
                "required": ["host", "password", "path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_sync_files",
            "description": "SSH 批量写多个文件到远程主机 (SFTP, 一次连接). 适合同时改 app.py + index.html + 配置等多个远程文件. files 是 {远程绝对路径: 文件内容} 字典. 比多次 ssh_write_file 更高效.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":     {"type": "string"},
                    "port":     {"type": "integer", "default": 22},
                    "user":     {"type": "string", "default": "root"},
                    "password": {"type": "string"},
                    "files":    {"type": "object", "description": "远程路径 → 文件内容 的字典, 如 {\"/home/gua/app.py\": \"...\", \"/home/gua/templates/index.html\": \"...\"}"},
                    "backup":   {"type": "boolean", "description": "写入前备份原文件 (加 .bak 后缀), 默认 true", "default": True},
                },
                "required": ["host", "password", "files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_str_replace",
            "description": "SSH 远程文件 str_replace — 等价于 Claude Code 的 str_replace_based_edit_tool 但作用于远程文件. 适合大文件的精确局部修改: 只传 old_str/new_str 片段, 不必传整个文件. old_str 必须在文件中唯一出现 1 次, 否则报 AMBIGUOUS. 写入前自动备份 .bak.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":     {"type": "string", "description": "目标主机 IP/域名"},
                    "port":     {"type": "integer", "default": 22},
                    "user":     {"type": "string", "default": "root"},
                    "password": {"type": "string"},
                    "path":     {"type": "string", "description": "远程文件绝对路径"},
                    "old_str":  {"type": "string", "description": "要替换的原始片段 (必须在文件中唯一出现)"},
                    "new_str":  {"type": "string", "description": "替换后的新内容 (可为空字符串表示删除)"},
                },
                "required": ["host", "password", "path", "old_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_read_file",
            "description": "SSH 读取远程主机文件内容 (基于 paramiko SFTP). 适合读取远程代码/配置/HTML. 比 ssh_exec cat 更稳定, 支持大文件.",
            "parameters": {
                "type": "object",
                "properties": {
                    "host":     {"type": "string"},
                    "port":     {"type": "integer", "default": 22},
                    "user":     {"type": "string", "default": "root"},
                    "password": {"type": "string"},
                    "path":     {"type": "string", "description": "远程文件绝对路径"},
                    "max_chars": {"type": "integer", "description": "返回最大字符数, 默认 20000", "default": 20000},
                },
                "required": ["host", "password", "path"],
            },
        },
    },
]
