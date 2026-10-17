"""Windows 系统运维工具的插件门面。"""
from lib.tools.defs.windows_system_defs import WINDOWS_SYSTEM_DEFS


async def run(args, ctx):
    from core.tools.handlers.windows_system import h_windows_system
    text, _ = await h_windows_system((ctx or {}).get('sid', ''), args)
    return {'ok': not text.startswith('ERROR:') and '"ok": false' not in text, 'result': text}


NAME = 'windows_system'
DESCRIPTION = WINDOWS_SYSTEM_DEFS[0]['function']['description']
SCHEMA = WINDOWS_SYSTEM_DEFS[0]['function']['parameters']
CAPABILITIES = {'safe': False, 'long_running': True}
