"""Windows 节点登记与主动回连；管理入口沿用 Web 登录。"""
import asyncio
from pathlib import Path
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from routers.auth_router import require_auth
from lib.windows_nodes import Nodes

def no_store(response: Response):
    response.headers['Cache-Control'] = 'no-store'


router = APIRouter(dependencies=[Depends(no_store)])
ASSETS = {'agent.py': 'windows_node_agent.py', 'worker.py': 'windows_desktop_worker.py',
          'windows_desktop_actions.py': 'windows_desktop_actions.py', 'windows_desktop_ocr.py': 'windows_desktop_ocr.py',
          'uninstall.ps1': 'windows_node_uninstall.ps1'}


def manager():
    from lib.config import WORKSPACE
    return Nodes(WORKSPACE)


def require_management(request):
    from plugins.auth import core as auth
    if not auth._AUTH_ENABLED:
        raise HTTPException(503, '请先开启 LiteCode Web 登录保护，再管理桌面节点')
    require_auth(request)


def credential(request):
    header = request.headers.get('authorization', '')
    return header[7:] if header.startswith('Bearer ') else ''


async def body(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 24 * 1024 * 1024:
            raise HTTPException(413, '请求过大')
    import json
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except ValueError:
        raise HTTPException(400, 'JSON 格式错误')


@router.get('/api/windows/nodes')
async def list_nodes(request: Request):
    require_management(request)
    return {'nodes': manager().list()}


@router.post('/api/windows/nodes')
async def create_node(request: Request):
    require_management(request)
    data = await body(request)
    from core.tools.handlers.ssh_hosts import _load
    try:
        return manager().create(data.get('alias', ''), data.get('server_url') or str(request.base_url), _load())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete('/api/windows/nodes/{node_id}')
async def revoke_node(node_id: str, request: Request):
    require_management(request)
    return {'ok': manager().revoke(node_id)}


@router.post('/api/windows/nodes/{node_id}/remove')
async def remove_node(node_id: str, request: Request):
    require_management(request)
    try:
        return {'ok': manager().remove(node_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post('/api/windows/nodes/{node_id}/uninstall')
async def uninstall_node(node_id: str, request: Request):
    require_management(request)
    try:
        return manager().uninstall(node_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get('/api/windows/nodes/{node_id}/uninstall-command')
async def uninstall_command(node_id: str, request: Request):
    require_management(request)
    try:
        return manager().uninstall_command(node_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get('/api/windows/nodes/{node_id}/update-command')
async def update_command(node_id: str, request: Request):
    require_management(request)
    try:
        return manager().update_command(node_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post('/api/windows/nodes/{node_id}/check')
async def check_node(node_id: str, request: Request):
    require_management(request)
    from lib.windows_desktop import execute
    nodes = manager()
    node = next((n for n in nodes.list() if n['id'] == node_id), None)
    if not node:
        raise HTTPException(404, '节点不存在')
    try:
        return await asyncio.to_thread(execute, {'alias': node['alias'], 'action': 'screenshot', 'timeout': 60}, nodes.workspace)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(400, str(exc))


@router.get('/api/windows/bootstrap.ps1')
async def bootstrap():
    return PlainTextResponse(Path(__file__).resolve().parents[1].joinpath('lib/windows_node_bootstrap.ps1').read_text(),
                             headers={'Cache-Control': 'no-store'})


@router.get('/api/windows/uninstall.ps1')
async def uninstaller():
    return PlainTextResponse(Path(__file__).resolve().parents[1].joinpath('lib/windows_node_uninstall.ps1').read_text(),
                             headers={'Cache-Control': 'no-store'})


@router.post('/api/windows/enroll')
async def enroll(request: Request):
    data = await body(request)
    try:
        return manager().redeem(str(data.get('code', '')), data.get('metadata', {}))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(403, str(exc))


@router.get('/api/windows/device/{node_id}/assets/{asset}')
async def asset(node_id: str, asset: str, request: Request):
    try:
        manager().authenticate(node_id, credential(request))
    except ValueError:
        raise HTTPException(403, '设备认证失败')
    if asset not in ASSETS:
        raise HTTPException(404, '不存在的节点组件')
    return PlainTextResponse(Path(__file__).resolve().parents[1].joinpath('lib', ASSETS[asset]).read_text(),
                             headers={'Cache-Control': 'no-store'})


@router.post('/api/windows/device/{node_id}/status')
async def status(node_id: str, request: Request):
    data = await body(request)
    state = data.get('state', 'online')
    if state not in ('installing', 'online', 'error', 'uninstall_failed'):
        raise HTTPException(400, '状态无效')
    stage = data.get('stage', '')
    if stage not in ('', 'python', 'packages', 'agent'):
        raise HTTPException(400, '安装阶段无效')
    try:
        manager().heartbeat(node_id, credential(request), state, data.get('error', ''), stage)
    except ValueError:
        raise HTTPException(403, '设备认证失败')
    return {'ok': True}


@router.post('/api/windows/device/{node_id}/uninstalled')
async def uninstalled(node_id: str, request: Request):
    try:
        manager().uninstalled(node_id, credential(request))
    except ValueError:
        raise HTTPException(403, '设备认证失败')
    return {'ok': True}


@router.get('/api/windows/device/{node_id}/poll')
async def poll(node_id: str, request: Request):
    from lib.windows_nodes import component_versions
    reported = {'agent': request.headers.get('x-agent-version', ''), 'assets': request.headers.get('x-assets-version', '')}
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            job = manager().claim(node_id, credential(request), reported)
        except ValueError:
            raise HTTPException(403, '设备认证失败')
        if job:
            return {'job': job, 'versions': component_versions()}
        if await request.is_disconnected():
            break
        await asyncio.sleep(1)
    return {'job': None, 'versions': component_versions()}


@router.post('/api/windows/device/{node_id}/result/{job_id}')
async def result(node_id: str, job_id: str, request: Request):
    data = await body(request)
    try:
        manager().finish(node_id, credential(request), job_id, data.get('result'), data.get('images', {}))
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    return {'ok': True}
