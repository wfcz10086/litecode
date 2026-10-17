"""Windows 主动回连节点：一次性登记、设备凭据和有期限的桌面任务。"""
import base64
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit
import uuid


def server_url(value):
    url = urlsplit(value)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise ValueError('请提供不含账户、路径和参数的 LiteCode Web 地址')
    if not re.fullmatch(r'[A-Za-z0-9.:_-]+', url.hostname):
        raise ValueError('服务器域名包含不支持的字符')
    _ = url.port
    if url.scheme == 'http':
        try:
            address = ipaddress.ip_address(url.hostname)
            local = address.is_private or address.is_loopback
        except ValueError:
            local = url.hostname == 'localhost'
        if not local:
            raise ValueError('公网接入必须使用 HTTPS；HTTP 仅允许本机或内网地址')
    return value.rstrip('/')


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


_LIB = Path(__file__).resolve().parent
# 与设备端 windows_node_agent.ASSETS 顺序一致; 路由用 read_text() 原样以 UTF-8 下发, 两端按相同字节算哈希。
# 新增 Windows 端文件必须同时加进这里、windows_node_agent.ASSETS 和 routers 的 ASSETS 映射, 否则节点拿不到。
WORKER_FILES = ('windows_desktop_worker.py', 'windows_desktop_actions.py', 'windows_desktop_ocr.py',
                'windows_system_actions.py', 'windows_file_actions.py', 'windows_transfer.py')
_VERSION_RE = re.compile(r'[0-9a-f]{16}')


def component_versions():
    """当前控制端将下发的代理与桌面组件版本 (内容哈希前 16 位)。"""
    agent = hashlib.sha256(_LIB.joinpath('windows_node_agent.py').read_text().encode()).hexdigest()[:16]
    assets = hashlib.sha256()
    for name in WORKER_FILES:
        assets.update(_LIB.joinpath(name).read_text().encode())
    return {'agent': agent, 'assets': assets.hexdigest()[:16]}


def update_state(node, current):
    """legacy=旧代理不上报版本需本机更新一次; updating=设备将自动更新; current=与控制端一致。"""
    if not node.get('agent_version'):
        return 'legacy'
    if node['agent_version'] != current['agent'] or node.get('assets_version') != current['assets']:
        return 'updating'
    return 'current'


def _task(node, job, state):
    """保留不含动作参数或凭据的最近任务摘要。"""
    node['last_task'] = {'id': job['id'], 'action': job['action'], 'state': state, 'updated': time.time()}


def _command(body):
    return 'powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand ' + base64.b64encode(body.encode('utf-16le')).decode()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False)
    os.replace(temporary, path)


class Nodes:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.root = self.workspace / '.secrets/windows_nodes'

    @contextmanager
    def locked(self):
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / 'lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'r+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            file = self.root / 'nodes.json'
            data = json.loads(file.read_text()) if file.exists() else {}
            yield data
            _write(file, data)

    def create(self, alias, base_url, ssh_aliases=()):
        if not isinstance(alias, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', alias):
            raise ValueError('节点别名使用1到50位英文、数字、下划线或连字符')
        base_url = server_url(base_url)
        code = secrets.token_urlsafe(32)
        with self.locked() as data:
            if alias in data or alias in ssh_aliases:
                raise ValueError('节点别名已存在，不覆盖已有机器')
            data[alias] = {'id': uuid.uuid4().hex, 'alias': alias, 'state': 'awaiting_join',
                           'code_hash': _digest(code), 'expires': time.time() + 600, 'last_seen': 0,
                           'server_url': base_url, 'encrypted_transport': base_url.startswith('https://'),
                           'capabilities': {'shell': False}}
            node_id = data[alias]['id']
        body = ("$s='" + base_url + "'; $b=Invoke-RestMethod ($s+'/api/windows/bootstrap.ps1'); "
                "& ([scriptblock]::Create($b)) -Server $s -Code '" + code + "'")
        command = _command(body)
        return {'alias': alias, 'id': node_id, 'expires_in': 600, 'command': command, 'state': 'awaiting_join'}

    def redeem(self, code, metadata):
        token = secrets.token_urlsafe(48)
        with self.locked() as data:
            node = next((n for n in data.values() if secrets.compare_digest(n.get('code_hash', ''), _digest(code))), None)
            if not node or node['expires'] < time.time() or node['state'] != 'awaiting_join':
                raise ValueError('接入码无效、已使用或已过期')
            node.pop('code_hash', None)
            node.update(state='installing', stage='enrolled', token_hash=_digest(token), last_seen=time.time(),
                        computer=str(metadata.get('computer', ''))[:100], user=str(metadata.get('user', ''))[:100])
            return {'id': node['id'], 'alias': node['alias'], 'token': token}

    def authenticate(self, node_id, token):
        if not token:
            raise ValueError('设备认证失败')
        with self.locked() as data:
            node = next((n for n in data.values() if n['id'] == node_id), None)
            if not node or node['state'] in ('revoked', 'uninstalled') or not secrets.compare_digest(node.get('token_hash', ''), _digest(token)):
                raise ValueError('设备认证失败')
            return dict(node)

    def heartbeat(self, node_id, token, state='online', error='', stage='', versions=None):
        self.authenticate(node_id, token)
        with self.locked() as data:
            node = next(n for n in data.values() if n['id'] == node_id)
            if node['state'] in ('revoked', 'uninstalled'):
                raise ValueError('节点已撤销')
            for key in ('agent', 'assets'):
                value = (versions or {}).get(key)
                if isinstance(value, str) and _VERSION_RE.fullmatch(value):
                    node[key + '_version'] = value
            if node['state'].startswith('uninstall') and state != 'uninstall_failed':
                node['last_seen'] = time.time()
                return
            node.update(last_seen=time.time(), state=state, error=str(error)[:400])
            if stage:
                node['stage'] = stage

    def list(self):
        with self.locked() as data:
            # 控制端重启后也清理过期任务，避免待输入凭据长期留在磁盘。
            for file in self.root.glob('job-*.json'):
                job = json.loads(file.read_text())
                if job['expires'] < time.time():
                    node = next((n for n in data.values() if n['id'] == job['node_id']), None)
                    if node and node.get('last_task', {}).get('id') == job['id'] and job['state'] in ('pending', 'dispatched'):
                        _task(node, job, 'unconfirmed')
                    file.unlink(missing_ok=True)
            result = []
            current = component_versions()
            for node in data.values():
                item = {k: v for k, v in node.items() if k not in ('code_hash', 'token_hash', 'expires')}
                if item['state'] == 'online' and time.time() - item['last_seen'] > 90:
                    item['state'] = 'offline'
                if item['state'] == 'awaiting_join' and node['expires'] < time.time():
                    item['state'] = 'expired'
                if item['state'] in ('online', 'offline'):
                    item['update_state'] = update_state(node, current)
                item['desktop_ready'] = (item['state'] == 'online' and node.get('desktop_ok') is True
                                         and time.time() - node.get('observed_at', 0) < 90)
                result.append(item)
            return result

    SHELL_CAPABILITY = 'shell'

    def set_capability(self, node_id, name, value):
        """开/关节点能力（目前仅 shell）。只由管理接口/CLI 调用，工具层不得调用。"""
        if name != self.SHELL_CAPABILITY:
            raise ValueError('不支持的能力开关')
        with self.locked() as data:
            node = next((n for n in data.values() if n['id'] == node_id), None)
            if not node:
                raise ValueError('节点不存在')
            if node['state'] in ('revoked', 'uninstalled'):
                raise ValueError('节点已撤销或卸载，无法修改能力')
            node.setdefault('capabilities', {})[name] = bool(value)
            return {'alias': node['alias'], 'capabilities': dict(node['capabilities'])}

    def node_capabilities(self, node_id):
        """节点当前能力；供设备经认证轮询获取，用于本地二次校验。"""
        with self.locked() as data:
            node = next((n for n in data.values() if n['id'] == node_id), None)
            caps = dict(node.get('capabilities', {})) if node else {}
            caps.setdefault(self.SHELL_CAPABILITY, False)
            return caps

    def revoke(self, node_id):
        with self.locked() as data:
            for node in data.values():
                if node['id'] == node_id:
                    node.update(state='revoked')
                    node.pop('token_hash', None)
                    node.pop('code_hash', None)
                    self._remove_jobs(node_id)
                    if node.get('last_task', {}).get('state') in ('queued', 'running'):
                        _task(node, node['last_task'], 'cancelled')
                    return True
        return False

    def remove(self, node_id):
        """删除已结束 (撤销/卸载完成/接入码过期) 的节点记录, 释放别名; 仍可能连回的节点必须先撤销或卸载。"""
        with self.locked() as data:
            alias, node = next(((a, n) for a, n in data.items() if n['id'] == node_id), (None, None))
            if not node:
                raise ValueError('节点不存在')
            expired = node['state'] == 'awaiting_join' and node['expires'] < time.time()
            if node['state'] not in ('revoked', 'uninstalled') and not expired:
                raise ValueError('请先撤销接入或完全卸载，再删除记录')
            self._remove_jobs(node_id)
            del data[alias]
            return True

    def _remove_jobs(self, node_id):
        """由持有锁的调用者清除设备任务及其中可能包含的待输入凭据。"""
        for file in self.root.glob('job-*.json'):
            if json.loads(file.read_text()).get('node_id') == node_id:
                file.unlink(missing_ok=True)

    def uninstall(self, node_id):
        with self.locked() as data:
            node = next((n for n in data.values() if n['id'] == node_id), None)
            if not node:
                raise ValueError('节点不存在')
            if node['state'] == 'uninstalled':
                return {'state': 'uninstalled'}
            if node['state'] == 'revoked':
                raise ValueError('设备已断开授权，无法远程卸载，请在原 Windows 用户下执行本机卸载命令')
            if not node.get('token_hash'):
                node.pop('code_hash', None)
                node['state'] = 'revoked'
                return {'state': 'revoked'}
            self._remove_jobs(node_id)
            if node.get('last_task', {}).get('state') in ('queued', 'running'):
                _task(node, node['last_task'], 'cancelled')
            node.update(state='uninstall_pending', error='', uninstall_requested_at=time.time())
            return {'state': node['state']}

    def uninstall_command(self, node_id):
        node = next((n for n in self.list() if n['id'] == node_id), None)
        if not node:
            raise ValueError('节点不存在')
        base = server_url(node['server_url'])
        body = ("$id='" + node_id + "'; $s='" + base + "'; "
                "$dirs=@(Get-ChildItem (Join-Path $env:LOCALAPPDATA 'LiteCodeNodes') -Directory -ErrorAction SilentlyContinue | "
                "Where-Object { $f=Join-Path $_.FullName 'node.json'; (Test-Path $f) -and ((Get-Content $f -Raw | ConvertFrom-Json).id -eq $id) }); "
                "if($dirs.Count -ne 1){throw '请使用安装时的 Windows 用户，当前用户下未发现唯一节点'}; "
                "$p=Join-Path $dirs[0].FullName 'uninstall.ps1'; "
                "if(Test-Path $p){ & $p -Root $dirs[0].FullName -Schedule } "
                "else { $b=Invoke-RestMethod ($s+'/api/windows/uninstall.ps1'); & ([scriptblock]::Create($b)) -NodeId $id -Schedule }")
        return {'command': _command(body)}

    def update_command(self, node_id):
        """旧代理不会自更新: 本机管理员 PowerShell 用 DPAPI 读出设备凭据, 换上最新代理并重启计划任务。"""
        node = next((n for n in self.list() if n['id'] == node_id), None)
        if not node:
            raise ValueError('节点不存在')
        if node['state'] not in ('online', 'offline', 'error'):
            raise ValueError('只有已接入的电脑可以更新代理')
        base = server_url(node['server_url'])
        body = ("$ErrorActionPreference='Stop'; $id='" + node_id + "'; $s='" + base + "'; "
                "$dirs=@(Get-ChildItem (Join-Path $env:LOCALAPPDATA 'LiteCodeNodes') -Directory -ErrorAction SilentlyContinue | "
                "Where-Object { $f=Join-Path $_.FullName 'node.json'; (Test-Path $f) -and ((Get-Content $f -Raw | ConvertFrom-Json).id -eq $id) }); "
                "if($dirs.Count -ne 1){throw '请使用安装时的 Windows 用户，当前用户下未发现唯一节点'}; "
                "$root=$dirs[0].FullName; Add-Type -AssemblyName System.Security; "
                "$c=Get-Content (Join-Path $root 'node.json') -Raw | ConvertFrom-Json; "
                "$t=[Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect("
                "[Convert]::FromBase64String($c.token_dpapi), $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)); "
                "$new=Join-Path $root 'agent.py.new'; "
                "Invoke-WebRequest ($s+'/api/windows/device/'+$id+'/assets/agent.py') -Headers @{Authorization=('Bearer '+$t)} -OutFile $new -UseBasicParsing; "
                "$task='LiteCode-Node-'+$id; Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue; "
                "Move-Item -Force $new (Join-Path $root 'agent.py'); Start-ScheduledTask -TaskName $task; "
                "Write-Host '代理已更新并重启，请在 LiteCode Web 查看运行版本。'")
        return {'command': _command(body)}

    def uninstalled(self, node_id, token):
        self.authenticate(node_id, token)
        with self.locked() as data:
            node = next(n for n in data.values() if n['id'] == node_id)
            if node['state'] in ('revoked', 'uninstalled'):
                raise ValueError('设备认证失败')
            self._remove_jobs(node_id)
            # 保留结果供 Web 核对，移除凭据、设备身份和截图引用。
            for key in ('token_hash', 'code_hash', 'computer', 'user', 'screenshot', 'last_task'):
                node.pop(key, None)
            node.update(state='uninstalled', desktop_ok=False, error='', uninstalled_at=time.time())

    def job(self, alias, action, request, timeout=60):
        from lib.windows_desktop import ACTIONS
        if action not in ACTIONS:
            raise ValueError('节点只接受已注册的桌面动作')
        node = next((n for n in self.list() if n['alias'] == alias), None)
        if not node or node['state'] != 'online':
            raise ValueError('节点尚未在线，请先执行接入命令并登录 Windows 桌面')
        job_id = uuid.uuid4().hex
        with self.locked() as data:
            node = data[alias]
            if node['state'] != 'online':
                raise ValueError('节点当前不能接受任务')
            for file in self.root.glob('job-*.json'):
                existing = json.loads(file.read_text())
                if existing['node_id'] == node['id'] and existing['expires'] > time.time() and existing['state'] in ('pending', 'dispatched'):
                    raise ValueError('该节点已有桌面任务，等完成后再操作')
            record = {'id': job_id, 'node_id': node['id'], 'state': 'pending', 'action': action,
                      'request': request, 'expires': time.time() + timeout}
            _write(self.root / ('job-' + job_id + '.json'), record)
            _task(node, record, 'queued')
        return job_id

    def claim(self, node_id, token, versions=None):
        self.heartbeat(node_id, token, versions=versions)
        with self.locked() as data:
            node = next(n for n in data.values() if n['id'] == node_id)
            if node['state'] in ('revoked', 'uninstalled'):
                raise ValueError('节点已撤销')
            if node['state'] in ('uninstall_pending', 'uninstalling'):
                node['state'] = 'uninstalling'
                return {'id': node_id, 'action': 'uninstall', 'request': {}}
            if node['state'] == 'uninstall_failed':
                return None
            if not node.get('first_capture_requested'):
                node['first_capture_requested'] = True
                job_id = uuid.uuid4().hex
                _write(self.root / ('job-' + job_id + '.json'), {
                    'id': job_id, 'node_id': node_id, 'state': 'pending', 'action': 'screenshot',
                    'request': {}, 'expires': time.time() + 120, 'initial': True})
            for file in sorted(self.root.glob('job-*.json'), key=lambda p: p.stat().st_mtime):
                job = json.loads(file.read_text())
                if job['node_id'] != node_id or job['state'] != 'pending':
                    continue
                if job['expires'] < time.time():
                    file.unlink(missing_ok=True)
                    continue
                payload = {k: job[k] for k in ('id', 'action', 'request')}
                job.pop('request', None)  # 凭据任务派发后不在控制端任务记录中保留明文。
                job['state'] = 'dispatched'
                _write(file, job)
                _task(node, job, 'running')
                return payload
        return None

    def finish(self, node_id, token, job_id, result, images):
        self.authenticate(node_id, token)
        if not re.fullmatch(r'[a-f0-9]{32}', job_id) or not isinstance(result, dict) or not isinstance(images, dict):
            raise ValueError('结果格式错误')
        with self.locked() as data:
            node = next(n for n in data.values() if n['id'] == node_id)
            if node['state'] != 'online':
                raise ValueError('节点已撤销或正在卸载')
            file = self.root / ('job-' + job_id + '.json')
            job = json.loads(file.read_text()) if file.exists() else {}
            if job.get('node_id') != node_id or job.get('state') != 'dispatched' or job['expires'] < time.time():
                raise ValueError('任务已过期或不属于当前设备')
            for key, name in (('image', 'screen.png'), ('before_image', 'before.png')):
                value = images.get(result.get(key)) if isinstance(result.get(key), str) else None
                result.pop(key, None)
                if value:
                    raw = base64.b64decode(value, validate=True)
                    if len(raw) > 12 * 1024 * 1024:
                        raise ValueError('截图过大')
                    from PIL import Image
                    import io
                    try:
                        with Image.open(io.BytesIO(raw)) as image:
                            image.verify()
                    except (OSError, Image.DecompressionBombError) as exc:
                        raise ValueError('截图格式错误') from exc
                    path = self.workspace / 'uploads' / ('Node-Desktop-' + job_id + '-' + name)
                    path.parent.mkdir(exist_ok=True, parents=True)
                    path.write_bytes(raw)
                    result[key] = str(path)
                    screen_size = result.get('screen_size')
                    if isinstance(screen_size, list) and len(screen_size) == 2:
                        path.with_name(path.name + '.screen').write_text(json.dumps(screen_size))
            result.update(action=job['action'])
            job.update(state='done', result=result)
            _write(file, job)
            if job.get('initial'):
                file.unlink(missing_ok=True)
            node.update(desktop_state=str(result.get('state', 'unknown')),
                        desktop_ok=bool(result.get('ok') is True and result.get('image')),
                        observed_at=time.time(), screenshot=result.get('image', node.get('screenshot', '')))
            _task(node, job, 'returned' if result.get('ok') is True else 'failed')

    def wait(self, job_id, timeout):
        path = self.root / ('job-' + job_id + '.json')
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if path.exists():
                    job = json.loads(path.read_text())
                    if job['state'] == 'done':
                        return job['result']
                else:
                    raise RuntimeError('任务已取消或节点已断开授权，未确认动作是否完成')
                time.sleep(0.25)
            with self.locked() as data:
                for node in data.values():
                    if node.get('last_task', {}).get('id') == job_id:
                        _task(node, node['last_task'], 'unconfirmed')
            raise TimeoutError('节点任务未返回，未确认动作是否完成；请先观察，勿重复发送')
        finally:
            path.unlink(missing_ok=True)
