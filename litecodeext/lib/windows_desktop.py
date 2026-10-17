"""经已有 SSH 别名初始化 Windows，并在登录用户桌面执行任务。"""
import base64
import json
from pathlib import Path
import time
import uuid

# 老版已部署代理支持的动作: 新动作不在此列, 对版本落后的节点拒绝下发, 等其自动更新。
LEGACY_ACTIONS = ('initialize', 'screenshot', 'wechat_login', 'wechat_status', 'ocr', 'click_text', 'type_secret',
                  'open_url', 'hotkey', 'type_text', 'windows', 'focus_window', 'close_window', 'scroll', 'click', 'drag', 'clipboard', 'right_click', 'hover')
NEW_ACTIONS = ('system_info', 'processes', 'kill_process', 'services', 'run_powershell',
               'list_dir', 'download', 'upload', 'launch_app', 'window_state', 'double_click', 'inspect_file')
# inspect_file 是纯服务端编排的动作: 不下发给节点, 由服务端复用已有节点动作完成 (inspect_file = download + 服务端解析),
# 因此节点端无需新代码; 仍放进 NEW_ACTIONS, 让版本门禁按它依赖的 download 能力把关。
ACTIONS = LEGACY_ACTIONS + NEW_ACTIONS
# 幂等/只读动作: 网络类错误可安全重试; 点击/输入/结束进程/传输等有副作用动作绝不自动重试。
IDEMPOTENT_ACTIONS = ('screenshot', 'system_info', 'processes', 'list_dir', 'services', 'windows', 'inspect_file')


def desktop_request(args, workspace):
    request = {key: args.get(key, '') for key in ('text', 'url', 'keys', 'title')}
    request['amount'] = args.get('amount', -5)
    request['input_method'] = args.get('input_method', 'clipboard')
    request['window_handle'] = args.get('window_handle')
    if args.get('action') in ('click', 'double_click', 'right_click', 'hover', 'drag') or args.get('x') is not None or args.get('y') is not None:
        from PIL import Image
        reference = Path(args.get('reference_image', '')).resolve()
        if not reference.is_relative_to((Path(workspace) / 'uploads').resolve()) or not reference.is_file():
            raise ValueError('必须提供当前工作区截图 reference_image')
        if time.time() - reference.stat().st_mtime > 300:
            raise ValueError('截图已超过五分钟，请重新截图定位')
        # 压缩截图的真实屏幕尺寸记在同名 .screen 旁车里, 坐标换算仍以原始屏幕为准。
        sidecar = reference.with_name(reference.name + '.screen')
        if sidecar.is_file():
            request['screen_size'] = json.loads(sidecar.read_text())
        else:
            with Image.open(reference) as image:
                request['screen_size'] = list(image.size)
        for key in ('x', 'y', 'end_x', 'end_y', 'image_width', 'image_height'):
            request[key] = args.get(key)
    if args.get('compress'):
        request['compress'] = True
    action = args.get('action')
    if action == 'type_secret':
        field = args.get('field')
        credential = args.get('credential', '')
        if field not in ('username', 'password') or not credential:
            raise ValueError('必须提供凭据引用及 username/password 字段')
        store = json.loads((Path(workspace) / '.secrets/windows_credentials.json').read_text())
        value = store.get(credential, {}).get(field)
        if not isinstance(value, str) or not value:
            raise ValueError('该凭据字段不存在')
        request.update(text=value, sensitive=True)
    elif action == 'processes':
        request.update(sort=args.get('sort', 'memory'), top=int(args.get('top', 15)),
                       name=str(args.get('name', '')))
    elif action == 'kill_process':
        if args.get('pid') is not None:
            request['pid'] = args['pid']
        if args.get('name'):
            request['name'] = str(args['name'])
        request['force'] = bool(args.get('force', False))
        if args.get('all') is True:
            request['all'] = True   # 旧代理不认识该字段, 忽略即可
    elif action == 'services':
        request['name'] = str(args.get('name', ''))
    elif action == 'run_powershell':
        script = args.get('script', '')
        if not isinstance(script, str) or not script.strip():
            raise ValueError('run_powershell 需要非空 script')
        request.update(script=script, timeout=int(args.get('timeout', 60)))
        if args.get('cwd'):
            request['cwd'] = str(args['cwd'])
    elif action == 'list_dir':
        path = args.get('path', '')
        if not isinstance(path, str) or not path.strip():
            raise ValueError('list_dir 需要目录 path')
        request['path'] = path
    elif action == 'launch_app':
        name = args.get('name', '')
        if not isinstance(name, str) or not name.strip():
            raise ValueError('launch_app 需要应用名称 name')
        request['name'] = name
    elif action == 'window_state':
        mode = args.get('window_state_mode')
        if mode not in ('maximize', 'minimize', 'restore'):
            raise ValueError('window_state 需要 maximize / minimize / restore')
        request['window_state_mode'] = mode
    return request


def powershell(client, script, timeout=30):
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    _, out, err = client.exec_command('powershell -NoProfile -NonInteractive -EncodedCommand ' + encoded,
                                     timeout=timeout)
    text = out.read().decode('utf-8-sig', errors='replace').strip()
    error = err.read().decode('utf-8', errors='replace')
    if out.channel.recv_exit_status() != 0:
        raise RuntimeError(error[:1500] or text[:1500])
    return text


def _run_node_job(nodes, alias, action, request, timeout, idempotent=False):
    """派发单个节点任务并等待结果；幂等/只读动作遇网络类错误重试 1-2 次。"""
    attempts = 3 if idempotent else 1
    last = None
    for attempt in range(attempts):
        job_id = nodes.job(alias, action, request, timeout)
        try:
            return nodes.wait(job_id, timeout)
        except (TimeoutError, RuntimeError) as exc:
            last = exc
            if not idempotent or attempt == attempts - 1:
                raise
            import time
            time.sleep(1)
    raise last


def _node_upload(nodes, alias, args, workspace, timeout):
    """服务端工作区文件 → Windows Downloads\\LiteCode，分块 + sha256 校验。"""
    import base64
    from lib.windows_transfer import resolve_upload_source, sha256_hex, chunk_count, slice_chunk, sanitize_download_name, CHUNK
    source = resolve_upload_source(Path(workspace), args.get('source', ''))
    if not source.is_file():
        raise ValueError('上传源文件必须是工作区 uploads/ 目录内的已有文件')
    data = source.read_bytes()
    total = chunk_count(len(data))   # 同时校验 ≤50MB
    digest = sha256_hex(data)
    name = sanitize_download_name(args.get('dest_name') or source.name)
    transfer_id = _uuid()
    result = None
    for index in range(total):
        request = {'transfer_id': transfer_id, 'name': name, 'index': index, 'total': total,
                   'sha256': digest, 'data': base64.b64encode(slice_chunk(data, index)).decode()}
        result = _run_node_job(nodes, alias, 'upload', request, timeout)
        if not result.get('ok'):
            raise RuntimeError('上传失败: ' + str(result.get('error', '未知错误')))
    return {'ok': True, 'state': 'upload_done', 'path': result.get('path'), 'name': result.get('name'),
            'size': len(data), 'sha256': digest, 'chunks': total}


def _node_download(nodes, alias, args, workspace, timeout, idempotent=False):
    """Windows 文件 → 服务端工作区 uploads/，分块 + sha256 校验。"""
    import base64
    from lib.windows_transfer import reassemble, sanitize_download_name
    meta = _run_node_job(nodes, alias, 'download', {'path': args.get('path', '')}, timeout, idempotent)
    if not meta.get('ok'):
        raise RuntimeError('读取远端文件失败: ' + str(meta.get('error', '未知错误')))
    total = int(meta['chunks'])
    chunks = {}
    for index in range(total):
        part = _run_node_job(nodes, alias, 'download', {'path': args.get('path', ''), 'index': index}, timeout, idempotent)
        if not part.get('ok'):
            raise RuntimeError('下载分块失败: ' + str(part.get('error', '未知错误')))
        chunks[index] = base64.b64decode(part['chunk'], validate=True)
    data = reassemble(chunks, meta['sha256'])
    target = Path(workspace) / 'uploads' / ('Node-Download-' + _uuid()[:8] + '-' + sanitize_download_name(meta['name']))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {'ok': True, 'state': 'download_done', 'path': str(target), 'name': meta['name'],
            'size': len(data), 'sha256': meta['sha256']}


def _node_inspect(nodes, alias, args, workspace, timeout):
    """回读核对: 复用 download 把文件取回服务端 uploads/ (保留副本便于审计), 再在服务端解析真实内容。

    路径 (含 %USERPROFILE% 等环境变量) 由节点端 download 原样展开; 节点端无需新代码。
    """
    from lib.doc_inspect import inspect
    path = args.get('path', '')
    if not isinstance(path, str) or not path.strip():
        raise ValueError('inspect_file 需要 Windows 文件路径 path')
    fetched = _node_download(nodes, alias, args, workspace, timeout, idempotent=True)
    parsed = inspect(fetched['path'])
    parsed.update(remote_path=path, local_copy=fetched['path'],
                  basis='以下为文件真实内容 (服务端解析), 汇报里的数字/文字以此为准, 不以截图/OCR 为准')
    return parsed


def _uuid():
    return uuid.uuid4().hex


UPDATE_WAIT_SECONDS = 90
UPDATE_POLL_SECONDS = 2


def wait_until_updated(nodes, alias, node, limit=None):
    """节点正在自动更新 (update_state=updating) 时最多等 90 秒, 直到它以新版本回连 (current) 再下发。
    execute 由工具层经 asyncio.to_thread 调用, 这里的 sleep 只占工作线程, 不阻塞事件循环。"""
    deadline = time.monotonic() + (UPDATE_WAIT_SECONDS if limit is None else limit)
    while node is not None and node.get('update_state') == 'updating':
        if time.monotonic() >= deadline:
            raise ValueError(f'节点正在更新，请稍后重试：已等待 {UPDATE_WAIT_SECONDS} 秒仍未完成自动更新')
        time.sleep(UPDATE_POLL_SECONDS)
        node = next((n for n in nodes.list() if n['alias'] == alias), None)
    if node is None:
        raise ValueError('节点不存在或已被删除')
    return node


def execute(args, workspace):
    from core.tools.handlers.remote import _conn, _connect, _close
    from lib.windows_desktop_actions import shell_guard
    action = args.get('action', 'screenshot')
    if action not in ACTIONS:
        raise ValueError('不支持的操作')
    from lib.windows_nodes import Nodes
    nodes = Nodes(workspace)
    node = next((n for n in nodes.list() if n['alias'] == args.get('alias')), None)
    if node is not None:
        if action in NEW_ACTIONS and node.get('update_state') == 'updating':
            node = wait_until_updated(nodes, args.get('alias'), node)
        if action in NEW_ACTIONS and node.get('update_state') != 'current':
            raise ValueError('代理版本过低，等待自动更新：该节点运行的仍是旧版代理，暂不支持此动作')
        shell_on = node.get('capabilities', {}).get('shell') is True
        if action == 'run_powershell' and not shell_on:
            raise ValueError('该节点未开启 shell 能力；请管理员在 Web 节点面板或 CLI 执行 nodes shell <alias> on 后再用')
        # 服务端先拦: 已部署的旧版桌面组件不认识节点端校验, 也不会收到被禁止的请求
        shell_guard(action, args, shell_on)
        timeout = min(max(int(args.get('timeout', 60)), 10), 120)
        if action == 'run_powershell':
            timeout = min(max(int(args.get('timeout', 60)) + 30, 40), 330)
        if action == 'upload':
            return {**_node_upload(nodes, args['alias'], args, workspace, timeout), 'alias': args['alias'], 'action': action}
        if action == 'download':
            return {**_node_download(nodes, args['alias'], args, workspace, timeout), 'alias': args['alias'], 'action': action}
        if action == 'inspect_file':
            return {**_node_inspect(nodes, args['alias'], args, workspace, timeout), 'alias': args['alias'], 'action': action}
        request = desktop_request(args, workspace)
        result = _run_node_job(nodes, args['alias'], action, request, timeout, idempotent=action in IDEMPOTENT_ACTIONS)
        result.update(alias=args['alias'], action=action)
        return result
    if action in ('run_powershell', 'upload', 'download', 'inspect_file'):
        raise ValueError('该动作仅支持已开启能力的主动回连节点，SSH 别名不可用')
    shell_guard(action, args, False)   # SSH 路径无能力开关, 桌面组件按关闭处理, 这里提前给出同样的错误
    request = desktop_request(args, workspace)
    conn, error = _conn(args)
    if error:
        raise ValueError(error)
    client, jump = _connect(conn)
    sftp = None
    task = 'LiteCode-Desktop-' + uuid.uuid4().hex
    registered = False
    try:
        info = json.loads(powershell(client, "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; "
            "$py=(Get-Command python.exe).Source; @{home=$env:USERPROFILE; python=$py; user=[Security.Principal.WindowsIdentity]::GetCurrent().Name} | ConvertTo-Json -Compress"))
        root = info['home'].replace('\\', '/') + '/AppData/Local/LiteCodeDesktop'
        folder = root + '/' + task
        powershell(client, f"New-Item -ItemType Directory -Force -Path '{folder}' | Out-Null")
        sftp = client.open_sftp()
        if action == 'initialize':
            py = info['python'].replace("'", "''")
            powershell(client, f"$ErrorActionPreference='Stop'; & '{py}' -m pip install --disable-pip-version-check "
                       "mss pillow uiautomation pywin32 pyautogui pyperclip winocr -i https://pypi.tuna.tsinghua.edu.cn/simple; "
                       "if ($LASTEXITCODE -ne 0) { throw '桌面依赖安装失败' }", timeout=180)
        # 每个任务使用独立脚本和结果目录，避免并发覆盖。
        with sftp.open(folder + '/worker.py', 'w') as target:
            target.write(Path(__file__).with_name('windows_desktop_worker.py').read_text())
        with sftp.open(folder + '/windows_desktop_ocr.py', 'w') as target:
            target.write(Path(__file__).with_name('windows_desktop_ocr.py').read_text())
        with sftp.open(folder + '/windows_desktop_actions.py', 'w') as target:
            target.write(Path(__file__).with_name('windows_desktop_actions.py').read_text())
        for extra in ('windows_system_actions.py', 'windows_file_actions.py', 'windows_transfer.py'):
            with sftp.open(folder + '/' + extra, 'w') as target:
                target.write(Path(__file__).with_name(extra).read_text())
        with sftp.open(folder + '/request.json', 'w') as target:
            target.write(json.dumps(request, ensure_ascii=False))
        pyw = str(Path(info['python']).with_name('pythonw.exe')) if '\\' not in info['python'] else info['python'].rsplit('\\', 1)[0] + '\\pythonw.exe'
        remote_action = 'screenshot' if action == 'initialize' else action
        def quote(value):
            return "'" + value.replace("'", "''") + "'"
        arguments = f'"{folder}/worker.py" {remote_action} "{folder}"'
        elevated = args.get('elevated', False)
        if not isinstance(elevated, bool):
            raise ValueError('elevated 必须为布尔值')
        run_level = 'Highest' if elevated else 'Limited'
        ps = ("$ErrorActionPreference='Stop'; "
              f"$a=New-ScheduledTaskAction -Execute {quote(pyw)} -Argument {quote(arguments)}; "
              f"$p=New-ScheduledTaskPrincipal -UserId {quote(info['user'])} -LogonType Interactive -RunLevel {run_level}; "
              f"Register-ScheduledTask -TaskName '{task}' -Action $a -Principal $p -Force | Out-Null; "
              f"Start-ScheduledTask -TaskName '{task}'")
        powershell(client, ps)
        registered = True
        deadline = time.monotonic() + min(max(int(args.get('timeout', 35)), 10), 120)
        while time.monotonic() < deadline:
            try:
                with sftp.open(folder + '/result.json') as source:
                    result = json.loads(source.read().decode('utf-8'))
                break
            except FileNotFoundError:
                time.sleep(0.5)
        else:
            raise TimeoutError('桌面任务未返回：请检查用户已登录、计划任务状态和桌面权限；未报告成功')
        if result.get('image') in ('screen.png', 'before.png'):
            local = Path(workspace) / 'uploads' / (task + '.png')
            local.parent.mkdir(parents=True, exist_ok=True)
            sftp.get(folder + '/' + result['image'], str(local))
            result['image'] = str(local)
            screen_size = result.get('screen_size')
            if isinstance(screen_size, list) and len(screen_size) == 2:
                local.with_name(local.name + '.screen').write_text(json.dumps(screen_size))
            if result.get('before_image') == 'before.png':
                before = local.with_name(local.stem + '-before.png')
                sftp.get(folder + '/before.png', str(before))
                result['before_image'] = str(before)
        result['alias'] = args.get('alias', conn['host'])
        result['action'] = action
        return result
    finally:
        if registered:
            try:
                powershell(client, f"Stop-ScheduledTask -TaskName '{task}' -ErrorAction SilentlyContinue; "
                           f"Unregister-ScheduledTask -TaskName '{task}' -Confirm:$false", timeout=15)
            except Exception:
                pass
        if sftp:
            try:
                sftp.remove(folder + '/request.json')
            except OSError:
                pass
            sftp.close()
        _close(client, jump)
