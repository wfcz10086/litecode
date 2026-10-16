"""让对话也能登记、查看、撤销主动回连的 Windows 节点。"""
import asyncio
import json
from . import register


@register('windows_nodes')
async def h_windows_nodes(sid, args):
    from lib.config import WORKSPACE
    from lib.windows_nodes import Nodes
    from .ssh_hosts import _load
    nodes = Nodes(WORKSPACE)
    try:
        action = args.get('action')
        if action == 'list':
            result = {'nodes': await asyncio.to_thread(nodes.list)}
        elif action == 'create':
            if not args.get('server_url'):
                raise ValueError('请提供新电脑可访问的 LiteCode Web 地址，或在 Web 左侧点“添加 Windows”；不要猜测公网入口')
            result = await asyncio.to_thread(nodes.create, args.get('alias', ''), args['server_url'], _load())
            result['instructions'] = '在新电脑已登录的桌面，用管理员 PowerShell 执行 command。十分钟内有效，安装后自动上线并回传首张截图。生成命令不代表接入成功。'
        elif action == 'revoke':
            result = {'ok': await asyncio.to_thread(nodes.revoke, args.get('node_id', ''))}
        elif action == 'remove':
            result = {'ok': await asyncio.to_thread(nodes.remove, args.get('node_id', ''))}
        elif action == 'uninstall':
            result = await asyncio.to_thread(nodes.uninstall, args.get('node_id', ''))
            result['instructions'] = '只有状态 uninstalled 才表示收到清理完成回执；离线时等待上线，不能把已请求当成卸载完成。'
        else:
            raise ValueError('不支持的节点管理动作')
        return json.dumps(result, ensure_ascii=False), None
    except Exception as exc:
        return 'ERROR: windows_nodes ' + str(exc), None
