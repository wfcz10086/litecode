"""Windows 桌面工具的插件门面。"""
from lib.tools.defs.windows_desktop_defs import WINDOWS_DESKTOP_DEFS


async def run(args, ctx):
    from core.tools.handlers.windows_desktop import h_windows_desktop
    text, artifact = await h_windows_desktop((ctx or {}).get('sid', ''), args)
    result = {'ok': not text.startswith('ERROR:') and '"ok": false' not in text, 'result': text}
    if artifact:
        result['artifact'] = artifact
    return result


NAME = 'windows_desktop'
DESCRIPTION = WINDOWS_DESKTOP_DEFS[0]['function']['description']
SCHEMA = WINDOWS_DESKTOP_DEFS[0]['function']['parameters']
CAPABILITIES = {'safe': False, 'long_running': True}
