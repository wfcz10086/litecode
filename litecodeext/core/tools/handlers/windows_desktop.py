"""Windows 桌面任务入口，复用已有主机凭据和图片展示协议。"""
import asyncio
import json
from . import register


# (会话, 电脑) → 最近一次返回的截图。模型下一步看到的就是这张, 坐标以它为准;
# 实测模型常漏传 reference_image 被拒后再重试, 白费一轮。
_LAST_SHOT: dict = {}
_POINTER_ACTIONS = ('click', 'right_click', 'hover', 'drag')


def with_reference(sid, args):
    if args.get('reference_image'):
        return args
    pointer = args.get('action') in _POINTER_ACTIONS or args.get('x') is not None or args.get('y') is not None
    last = _LAST_SHOT.get((sid, args.get('alias')))
    return {**args, 'reference_image': last} if pointer and last else args


def remember_shot(sid, alias, result):
    if isinstance(result.get('image'), str):
        _LAST_SHOT[(sid, alias)] = result['image']


@register('windows_desktop')
async def h_windows_desktop(sid, args):
    from lib.config import WORKSPACE
    from lib.windows_desktop import execute
    try:
        args = with_reference(sid, args)
        result = await asyncio.to_thread(execute, args, WORKSPACE)
        remember_shot(sid, args.get('alias'), result)
        text = json.dumps(result, ensure_ascii=False)
        if not result.get('ok'):
            text = 'ERROR: windows_desktop ' + text
        if result.get('before_image'):
            text += f"\n[发送图片: {result['before_image']}]"
        artifact = None
        if result.get('image'):
            path = result['image']
            text += f'\n[发送图片: {path}]'
            artifact = {'filepath': path, 'op': 'screenshot'}
            from core import tool_dispatch
            tool_dispatch._add_artifact(sid, artifact)
        # 核心派发第二返回值走 diff_view；图片已用 _add_artifact 单独登记。
        return text, None
    except Exception as exc:
        return f'ERROR: windows_desktop {type(exc).__name__}: {exc}', None
