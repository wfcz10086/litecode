"""经已有 SSH 别名初始化 Windows，并在登录用户桌面执行任务。"""
import base64
import json
from pathlib import Path
import time
import uuid

ACTIONS = ('initialize', 'screenshot', 'wechat_login', 'wechat_status', 'ocr', 'click_text', 'type_secret',
           'open_url', 'hotkey', 'type_text', 'windows', 'focus_window', 'close_window', 'scroll', 'click', 'drag', 'clipboard', 'right_click', 'hover')


def desktop_request(args, workspace):
    request = {key: args.get(key, '') for key in ('text', 'url', 'keys', 'title')}
    request['amount'] = args.get('amount', -5)
    request['input_method'] = args.get('input_method', 'clipboard')
    request['window_handle'] = args.get('window_handle')
    if args.get('action') in ('click', 'right_click', 'hover', 'drag') or args.get('x') is not None or args.get('y') is not None:
        from PIL import Image
        reference = Path(args.get('reference_image', '')).resolve()
        if not reference.is_relative_to((Path(workspace) / 'uploads').resolve()) or not reference.is_file():
            raise ValueError('必须提供当前工作区截图 reference_image')
        if time.time() - reference.stat().st_mtime > 300:
            raise ValueError('截图已超过五分钟，请重新截图定位')
        with Image.open(reference) as image:
            request['screen_size'] = list(image.size)
        for key in ('x', 'y', 'end_x', 'end_y', 'image_width', 'image_height'):
            request[key] = args.get(key)
    if args.get('action') == 'type_secret':
        field = args.get('field')
        credential = args.get('credential', '')
        if field not in ('username', 'password') or not credential:
            raise ValueError('必须提供凭据引用及 username/password 字段')
        store = json.loads((Path(workspace) / '.secrets/windows_credentials.json').read_text())
        value = store.get(credential, {}).get(field)
        if not isinstance(value, str) or not value:
            raise ValueError('该凭据字段不存在')
        request.update(text=value, sensitive=True)
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


def execute(args, workspace):
    from core.tools.handlers.remote import _conn, _connect, _close
    action = args.get('action', 'screenshot')
    if action not in ACTIONS:
        raise ValueError('不支持的操作')
    request = desktop_request(args, workspace)
    from lib.windows_nodes import Nodes
    nodes = Nodes(workspace)
    if any(n['alias'] == args.get('alias') for n in nodes.list()):
        timeout = min(max(int(args.get('timeout', 60)), 10), 120)
        job_id = nodes.job(args['alias'], action, request, timeout)
        result = nodes.wait(job_id, timeout)
        result.update(alias=args['alias'], action=action)
        return result
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
