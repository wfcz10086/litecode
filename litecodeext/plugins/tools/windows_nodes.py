"""Windows 节点工具的插件门面。"""
from lib.tools.defs.windows_nodes_defs import WINDOWS_NODES_DEFS


async def run(args, ctx):
    from core.tools.handlers.windows_nodes import h_windows_nodes
    text, _ = await h_windows_nodes((ctx or {}).get('sid', ''), args)
    return {'ok': not text.startswith('ERROR:'), 'result': text}


NAME = 'windows_nodes'
DESCRIPTION = WINDOWS_NODES_DEFS[0]['function']['description']
SCHEMA = WINDOWS_NODES_DEFS[0]['function']['parameters']
CAPABILITIES = {'safe': False, 'long_running': False}
