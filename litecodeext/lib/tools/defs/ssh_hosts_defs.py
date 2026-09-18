"""ssh_hosts_defs.py — SSH 主机配置持久化工具定义."""

SSH_HOSTS_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "ssh_save_host",
            "description": "持久化保存 SSH 主机凭据到磁盘 (alias → host/port/user/password). 保存后其他 ssh_* 工具可直接用 alias 参数, 不必每次传 password.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias":    {"type": "string", "description": "主机别名, 如 'vllm-gpu' / 'gua-server'"},
                    "host":     {"type": "string", "description": "IP 或域名"},
                    "port":     {"type": "integer", "default": 22},
                    "user":     {"type": "string", "default": "root"},
                    "password": {"type": "string"},
                    "note":     {"type": "string", "description": "备注说明 (可选)"},
                },
                "required": ["alias", "host", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_list_hosts",
            "description": "列出所有已持久化保存的 SSH 主机配置.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ssh_remove_host",
            "description": "删除一个已保存的 SSH 主机配置.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {"type": "string", "description": "要删除的主机别名"},
                },
                "required": ["alias"],
            },
        },
    },
]
