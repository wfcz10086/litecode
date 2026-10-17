"""Windows 节点系统运维与文件传输入口（命令行/非视觉通道）。"""
import asyncio
import json
from . import register


@register('windows_system')
async def h_windows_system(sid, args):
    from lib.config import WORKSPACE
    from lib.windows_desktop import execute
    try:
        from lib.agent.subagent_operator import check_windows_call
        args, busy = check_windows_call(sid, 'windows_system', args)
        if busy:
            return busy, None
        action = args.get('action')
        if action not in ('system_info', 'processes', 'kill_process', 'services',
                          'list_dir', 'download', 'upload', 'run_powershell', 'inspect_file'):
            raise ValueError('不支持的系统动作')
        result = await asyncio.to_thread(execute, args, WORKSPACE)
        text = json.dumps(result, ensure_ascii=False)
        if not result.get('ok'):
            text = 'ERROR: windows_system ' + text
        # download 把文件取回工作区, 登记为产出物, 便于对话查看/下载。
        if action == 'download' and result.get('ok') and isinstance(result.get('path'), str):
            from core import tool_dispatch
            artifact = {'filepath': result['path'], 'op': 'download'}
            tool_dispatch._add_artifact(sid, artifact)
            text += f"\n[产出物: {result['path']}]"
        return text, None
    except Exception as exc:
        return f'ERROR: windows_system {type(exc).__name__}: {exc}', None
