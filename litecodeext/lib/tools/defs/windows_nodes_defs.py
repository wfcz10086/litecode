"""主动回连 Windows 节点管理工具定义。"""
WINDOWS_NODES_DEFS = [{
    'type': 'function', 'function': {
        'name': 'windows_nodes',
        'description': '管理主动回连的 Windows 节点。create 为 Windows 生成十分钟内有效的一次性 PowerShell 安装命令，需 alias 和新电脑能访问的 server_url；不需要 SSH 或开放 Windows 端口。list 查看上线、桌面与最近动作状态。uninstall 按 node_id 请求完全卸载代理、启动任务、独立环境和设备凭据，等 uninstalled 回执才算完成。revoke 仅撤销授权，不删除本机文件。remove 删除已撤销、已卸载或接入码过期的节点记录并释放别名。接入后用 windows_desktop 指定该 alias 操作。生成命令、网络在线都不等于桌面可操作，需查看首张截图和 desktop_state。',
        'parameters': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['create', 'list', 'revoke', 'uninstall', 'remove']},
            'alias': {'type': 'string', 'description': '新节点独立别名，1到50位英文、数字、下划线或连字符，不覆盖已有节点或SSH别名'},
            'server_url': {'type': 'string', 'description': '新电脑可访问的 LiteCode Web 完整地址；公网必须HTTPS，内网可以HTTP/IP，不能填SSH入口或猜测地址'},
            'node_id': {'type': 'string', 'description': 'revoke/uninstall/remove 必填：list 返回的节点ID'},
        }, 'required': ['action']},
    },
}]
