"""将桌面工具结果转换为可持久化的 Web 过程事件，排除输入凭据。"""
import json
from pathlib import Path
import time


def desktop_step(result, args, workspace, step_id):
    payload = {'id': str(step_id), 'alias': args.get('alias', ''),
               'action': args.get('action', ''), 'ts': time.time(), 'ok': False, 'state': 'error'}
    try:
        if not isinstance(result, str):
            raise ValueError('结果不是文本')
        start = result.find('{')
        data, _ = json.JSONDecoder().raw_decode(result[start:]) if start >= 0 else ({}, 0)
        if not isinstance(data, dict):
            data = {}
    except (ValueError, TypeError):
        data = {}
    payload.update(ok=data.get('ok') is True, state=str(data.get('state', 'error')))
    if data.get('error'):
        payload['error'] = str(data['error'])[:500]
    elif not data:
        payload['error'] = '桌面工具未返回有效结果'
    uploads = (Path(workspace) / 'uploads').resolve()
    for key in ('image', 'before_image'):
        value = data.get(key)
        if isinstance(value, str):
            path = Path(value).resolve()
            if path.is_relative_to(uploads) and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'):
                payload[key] = str(path)
    return payload
