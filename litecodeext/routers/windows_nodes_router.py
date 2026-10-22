"""Windows 节点登记与主动回连；管理入口沿用 Web 登录。"""
import asyncio
import logging
from pathlib import Path
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from routers.auth_router import require_auth
from lib.windows_nodes import AuthError, Nodes

log = logging.getLogger(__name__)
# 只有真正的认证失败 (凭据不符/节点已撤销) 才带这个标记; 新代理只对带标记的 403 连续 3 次才停用自己。
AUTH_REVOKED = {'X-LiteCode-Auth': 'revoked'}


def auth_failed():
    return HTTPException(403, '设备认证失败', headers=AUTH_REVOKED)


def internal_error(where, exc):
    """非认证类异常 (任务文件损坏、磁盘错误等) 返回 500: 代理按网络错误退避重试, 不会误判撤销。"""
    log.exception('Windows 节点接口 %s 异常: %s', where, exc)
    return HTTPException(500, '控制端内部错误，请稍后重试')

def no_store(response: Response):
    response.headers['Cache-Control'] = 'no-store'


router = APIRouter(dependencies=[Depends(no_store)])
ORDER = 150  # 注册顺序 (routers.discover 按此升序 include, 路径重叠时先注册者生效)
ASSETS = {'agent.py': 'windows_node_agent.py', 'worker.py': 'windows_desktop_worker.py',
          'windows_desktop_actions.py': 'windows_desktop_actions.py', 'windows_desktop_ocr.py': 'windows_desktop_ocr.py',
          'windows_system_actions.py': 'windows_system_actions.py', 'windows_file_actions.py': 'windows_file_actions.py',
          'windows_transfer.py': 'windows_transfer.py',
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


# [2026-10-19] 以前所有接口都在认证前读最多 24MB 请求体: 未认证的人可对 enroll/status/result 反复灌大包,
# 控制端每个请求先吃满内存再拒绝。现在设备接口先核 Bearer 再读体; 登记/状态只是小 JSON, 上限 64KB;
# 只有已认证的结果回传 (含截图 base64) 才放宽到 24MB, 且先看 Content-Length 再边读边限。
SMALL_BODY = 64 * 1024
RESULT_BODY = 24 * 1024 * 1024


async def body(request, limit=SMALL_BODY):
    declared = request.headers.get('content-length', '')
    if declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, '请求过大')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
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
    return {'nodes': await asyncio.to_thread(manager().list)}


@router.post('/api/windows/nodes')
async def create_node(request: Request):
    require_management(request)
    data = await body(request)
    enable_shell = data.get('enable_shell', False)
    if not isinstance(enable_shell, bool):
        raise HTTPException(400, 'enable_shell 必须为布尔值')
    from core.tools.handlers.ssh_hosts import _load
    try:
        return await asyncio.to_thread(manager().create, data.get('alias', ''),
                                       data.get('server_url') or str(request.base_url), _load(),
                                       enable_shell=enable_shell)
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


@router.post('/api/windows/nodes/{node_id}/shell')
async def set_shell(node_id: str, request: Request):
    """开/关单个节点的 run_powershell 命令行能力。需管理权限; 工具层无法调用此接口。"""
    require_management(request)
    data = await body(request)
    if not isinstance(data.get('enabled'), bool):
        raise HTTPException(400, 'enabled 必须为布尔值')
    try:
        return manager().set_capability(node_id, 'shell', data['enabled'])
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
        await asyncio.to_thread(manager().authenticate, node_id, credential(request))
    except AuthError:
        raise auth_failed()
    except Exception as exc:
        raise internal_error('assets', exc)
    if asset not in ASSETS:
        raise HTTPException(404, '不存在的节点组件')
    return PlainTextResponse(Path(__file__).resolve().parents[1].joinpath('lib', ASSETS[asset]).read_text(),
                             headers={'Cache-Control': 'no-store'})


# [2026-10-20] 安装时 Windows 可选功能装不上 OpenSSH (连不上 Windows Update / WSUS) 就下载这里自带的微软官方
# Win32-OpenSSH 包; 设备端按 bootstrap 里写死的 sha256 校验, 再验 sshd.exe 的微软签名。
VENDOR = {'openssh.zip': 'OpenSSH-Win64-v10.0.0.0p2.zip'}


@router.get('/api/windows/device/{node_id}/vendor/{name}')
async def vendor(node_id: str, name: str, request: Request):
    try:
        await asyncio.to_thread(manager().authenticate, node_id, credential(request))
    except AuthError:
        raise auth_failed()
    except Exception as exc:
        raise internal_error('vendor', exc)
    if name not in VENDOR:
        raise HTTPException(404, '不存在的安装包')
    from fastapi.responses import FileResponse
    path = Path(__file__).resolve().parents[1].joinpath('lib', 'windows_vendor', VENDOR[name])
    return FileResponse(path, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})


def _versions_payload(workspace):
    from lib.windows_nodes import asset_hashes, component_versions
    from lib import windows_node_signing as signing
    # asset_hashes 是新增字段: 新代理逐个校验下载的组件, 旧代理忽略。
    # signed_manifest: 控制端私钥签名的清单 (版本 + 各文件 sha256); 已固定公钥的节点只认它, 旧代理忽略。
    payload = {'versions': component_versions(), 'asset_hashes': asset_hashes()}
    if signing.available():
        payload['signed_manifest'] = signing.signed_manifest(workspace)
    return payload


def _signing_key(workspace):
    from lib import windows_node_signing as signing
    if not signing.available():
        raise HTTPException(503, '控制端未启用代码签名 (缺少 cryptography)')
    return signing.public_key_info(workspace)


@router.get('/api/windows/signing-key')
async def signing_key():
    """签名公钥 (公开信息, 不含私钥)。安装命令里带指纹, 引导脚本下载后核对指纹再使用。"""
    return await asyncio.to_thread(_signing_key, manager().workspace)


@router.get('/api/windows/device/{node_id}/signing-key')
async def device_signing_key(node_id: str, request: Request):
    """已上线旧节点升级到新代理后经设备通道取公钥并固定 (首次信任), 之后的更新都要求验签。"""
    nodes = manager()
    try:
        await asyncio.to_thread(nodes.authenticate, node_id, credential(request))
    except AuthError:
        raise auth_failed()
    except Exception as exc:
        raise internal_error('signing-key', exc)
    return await asyncio.to_thread(_signing_key, nodes.workspace)


@router.get('/api/windows/device/{node_id}/versions')
async def versions(node_id: str, request: Request):
    """代理启动时先取目标版本与逐文件哈希, 下载组件后全部校验通过才替换。"""
    try:
        nodes = manager()
        await asyncio.to_thread(nodes.authenticate, node_id, credential(request))
        return await asyncio.to_thread(_versions_payload, nodes.workspace)
    except AuthError:
        raise auth_failed()
    except Exception as exc:
        raise internal_error('versions', exc)


@router.post('/api/windows/device/{node_id}/status')
async def status(node_id: str, request: Request):
    await _device_auth(node_id, request, 'status')
    data = await body(request)
    state = data.get('state', 'online')
    if state not in ('installing', 'online', 'error', 'uninstall_failed'):
        raise HTTPException(400, '状态无效')
    stage = data.get('stage', '')
    if stage not in ('', 'ssh', 'ssh_skipped', 'python', 'packages', 'agent'):   # ssh_skipped: OpenSSH 没装上但继续安装 (2026-10-20)
        raise HTTPException(400, '安装阶段无效')
    try:
        await asyncio.to_thread(manager().heartbeat, node_id, credential(request), state, data.get('error', ''), stage)
    except AuthError:
        raise auth_failed()
    except Exception as exc:
        raise internal_error('status', exc)
    return {'ok': True}


@router.post('/api/windows/device/{node_id}/uninstalled')
async def uninstalled(node_id: str, request: Request):
    try:
        await asyncio.to_thread(manager().uninstalled, node_id, credential(request))
    except ValueError:   # 含 AuthError 与"已撤销/已卸载"重复回执: 均视为认证失败
        raise auth_failed()
    except Exception as exc:
        raise internal_error('uninstalled', exc)
    return {'ok': True}


async def _device_auth(node_id, request, where):
    """设备接口先认证再读请求体 (见 body 注释)。"""
    try:
        await asyncio.to_thread(manager().authenticate, node_id, credential(request))
    except AuthError:
        raise auth_failed()
    except Exception as exc:
        raise internal_error(where, exc)


def _poll_reply(nodes, node_id, job):
    # capabilities 走已认证通道下发, 供设备对 run_powershell 做本地二次校验; 旧代理忽略新增字段。
    return {'job': job, **_versions_payload(nodes.workspace), 'capabilities': nodes.node_capabilities(node_id)}


@router.get('/api/windows/device/{node_id}/poll')
async def poll(node_id: str, request: Request):
    reported = {'agent': request.headers.get('x-agent-version', ''), 'assets': request.headers.get('x-assets-version', '')}
    nodes = manager()
    token = credential(request)
    deadline = time.monotonic() + 20
    job = None
    while time.monotonic() < deadline:
        try:
            # 加锁读写与磁盘 IO 放到线程里, 不阻塞事件循环
            job = await asyncio.to_thread(nodes.claim, node_id, token, reported)
        except AuthError:
            raise auth_failed()
        except Exception as exc:
            # 只有认证失败才是 403; 其他异常 (含任务文件 JSON 损坏) 返回 500, 代理按网络错误退避
            raise internal_error('poll', exc)
        if job or await request.is_disconnected():
            break
        await asyncio.sleep(1)
    try:
        return await asyncio.to_thread(_poll_reply, nodes, node_id, job)
    except Exception as exc:
        raise internal_error('poll', exc)


@router.post('/api/windows/device/{node_id}/result/{job_id}')
async def result(node_id: str, job_id: str, request: Request):
    await _device_auth(node_id, request, 'result')
    data = await body(request, RESULT_BODY)
    try:
        # base64 解码与图片校验是同步重活, 放线程里做
        await asyncio.to_thread(manager().finish, node_id, credential(request), job_id,
                                data.get('result'), data.get('images', {}))
    except AuthError:
        raise auth_failed()
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc))
    return {'ok': True}
