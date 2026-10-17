"""运行于登录用户桌面的出站节点代理，不监听任何网络端口。"""
import base64
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import time
import urllib.error
import urllib.request

# 顺序与控制端 lib.windows_nodes.WORKER_FILES 一致, 版本哈希按这个顺序拼接 (uninstall.ps1 随更新刷新)
ASSETS = ('worker.py', 'windows_desktop_actions.py', 'windows_desktop_ocr.py',
          'windows_system_actions.py', 'windows_file_actions.py', 'windows_transfer.py',
          'uninstall.ps1')
WORKER_MODULES = ('windows_desktop_worker', 'windows_desktop_actions', 'windows_desktop_ocr',
                  'windows_system_actions', 'windows_file_actions', 'windows_transfer')
VERSIONS = {'agent': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16], 'assets': ''}
BOM = b'\xef\xbb\xbf'
# 带这个标记的 403 才是凭据撤销; 连续这么多次才停用计划任务, 其他 403/5xx 一律按网络错误退避
REVOKED_HEADER = 'X-LiteCode-Auth'
REVOKE_CONFIRMATIONS = 3
# 单张截图超过此原始字节数就转 JPEG 再上报: 控制端单图上限 12MB, 请求体上限 24MB (两张 8MB base64 后约 21MB)
IMAGE_SOFT_LIMIT = 8 * 1024 * 1024


def _disk_bytes(name, data):
    """PowerShell 5.1 按 ANSI 读无 BOM 脚本, 中文会乱码: .ps1 落盘补 UTF-8 BOM; 版本哈希按下发字节 (不含 BOM)。"""
    return BOM + data if name.endswith('.ps1') and not data.startswith(BOM) else data


def assets_version(root):
    digest = hashlib.sha256()
    for asset in ASSETS:
        data = (root / asset).read_bytes()
        digest.update(data[len(BOM):] if asset.endswith('.ps1') and data.startswith(BOM) else data)
    return digest.hexdigest()[:16]


def load_assets(config, root, expected=None, hashes=None):
    """更新桌面组件, 全有或全无: 先全部下载到临时目录并逐个校验 (逐文件 sha256 + 整体版本哈希),
    全部通过才逐个 os.replace 到位; 任一下载失败或哈希不符则放弃本次更新, 旧文件原样保留。
    成功后丢弃已导入的旧模块, 下一次任务即用新代码, 代理本身不用重启。"""
    import shutil
    staging = root / '.update'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        combined = hashlib.sha256()
        for asset in ASSETS:
            data = request(config, '/assets/' + asset)
            served = 'windows_desktop_worker.py' if asset == 'worker.py' else (
                'windows_node_uninstall.ps1' if asset == 'uninstall.ps1' else asset)
            want = (hashes or {}).get(served)
            if want and hashlib.sha256(data).hexdigest() != want:
                raise ValueError('组件哈希与控制端公布不符: ' + asset)
            combined.update(data)
            (staging / asset).write_bytes(_disk_bytes(asset, data))
        if expected and combined.hexdigest()[:16] != expected:
            raise ValueError('组件整体版本与控制端公布不符')
        (staging / 'windows_desktop_worker.py').write_bytes((staging / 'worker.py').read_bytes())
        for name in ASSETS + ('windows_desktop_worker.py',):
            os.replace(staging / name, root / name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    for name in WORKER_MODULES:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    VERSIONS['assets'] = combined.hexdigest()[:16]


def update_agent(config, root, expected):
    """下载新代理, 哈希与控制端公布一致才原子替换, 再借计划任务重启自己 (仍受计划任务管理)。
    返回 True 表示已安排重启、调用方应退出。"""
    data = request(config, '/assets/agent.py')
    if hashlib.sha256(data).hexdigest()[:16] != expected:
        return False
    temporary = root / 'agent.py.new'
    temporary.write_bytes(data)
    os.replace(temporary, root / 'agent.py')
    if not re.fullmatch(r'[0-9a-f]{32}', config.get('id', '')):
        return False
    subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command',
                      "Start-Sleep -Seconds 3; Start-ScheduledTask -TaskName 'LiteCode-Node-" + config['id'] + "'"],
                     creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return True


def unprotect_token(raw):
    """用当前 Windows 用户的 DPAPI 解密；密钥由系统管理。"""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, target = Blob(len(raw), buffer), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError('设备凭据无法解密，请用安装时的 Windows 用户运行')
    try:
        return ctypes.string_at(target.data, target.size).decode('utf-8')
    finally:
        kernel.LocalFree(target.data)


def load_config(root):
    config = json.loads((root / 'node.json').read_text(encoding='utf-8-sig'))
    if 'token' in config or not config.get('token_dpapi'):
        raise ValueError('请重新执行原安装命令，将设备凭据升级为 Windows 用户加密')
    encrypted = base64.b64decode(config.pop('token_dpapi'), validate=True)
    config['token'] = unprotect_token(encrypted)
    return config


def request(config, path, data=None):
    encoded = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    req = urllib.request.Request(config['server'] + '/api/windows/device/' + config['id'] + path,
          data=encoded, headers={'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json',
                                 'X-Agent-Version': VERSIONS['agent'], 'X-Assets-Version': VERSIONS['assets']})
    with urllib.request.urlopen(req, timeout=40) as response:
        return response.read()


def shrink_large_images(folder, result, limit=IMAGE_SOFT_LIMIT):
    """未压缩截图 (4K/多屏 PNG) 超过上限时就地转压缩 JPEG, 与 compress=true 同一机制:
    result 带 screen_size (原始屏幕像素) 供控制端写 .screen 旁车, 点击坐标换算不受缩放影响。"""
    for key in ('image', 'before_image'):
        name = result.get(key)
        path = folder / name if name in ('screen.png', 'before.png') else None
        if path is None or not path.is_file() or path.stat().st_size <= limit:
            continue
        from windows_desktop_actions import compress_screenshot_file
        meta = compress_screenshot_file(path)
        result.setdefault('screen_size', meta['screen_size'])
        if key == 'image':
            result.update(image_size=meta['image_size'], orig_bytes=meta['orig_bytes'],
                          bytes=meta['bytes'], compressed=True, auto_compressed=True)


def run_job(config, root, job, capabilities=None):
    from windows_desktop_worker import run
    folder = root / 'jobs' / job['id']
    folder.mkdir(parents=True, exist_ok=True)
    try:
        # run_powershell 需本地二次校验: 开关来源是服务端经 Bearer 认证下发的节点能力。
        if job['action'] == 'run_powershell' and not (capabilities or {}).get('shell'):
            return {'result': {'ok': False, 'state': 'shell_disabled',
                               'error': '节点未开启 shell 能力，拒绝执行 PowerShell'}, 'images': {}}
        # 单进程串行执行，桌面句柄属于登录用户会话。
        result = run(job['action'], folder, job['request'])
        shrink_large_images(folder, result)
        images = {}
        for key in ('image', 'before_image'):
            name = result.get(key)
            if name in ('screen.png', 'before.png') and (folder / name).is_file():
                images[name] = base64.b64encode((folder / name).read_bytes()).decode()
        return {'result': result, 'images': images}
    except Exception as exc:
        return {'result': {'ok': False, 'state': 'error', 'error': str(exc)}, 'images': {}}
    finally:
        for file in folder.glob('*'):
            file.unlink(missing_ok=True)
        folder.rmdir()


TASK_HARDENED = 'task-hardened-v1'


def kill_tree(process):
    """taskkill /T /F 结束整个进程树 (只杀 powershell.exe 会留下继承了句柄的子进程); 失败再退回 kill()。"""
    try:
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        pass
    try:
        process.kill()
    except Exception:
        pass


def run_quiet(command, timeout=60):
    """不接管道运行命令 (输出丢弃), 保证 timeout+约 15 秒内返回: 超时结束进程树, 返回 returncode=None。
    不用 capture_output: 超时后 communicate() 会被继承了管道的孙进程卡住, 代理随之挂死。"""
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return subprocess.CompletedProcess(command, None)
    return subprocess.CompletedProcess(command, code)


def powershell(script, timeout=60):
    return run_quiet(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                      '-Command', script], timeout=timeout)


def harden_task(config, root):
    """早期安装只在登录时启动且崩溃仅重启 3 次; 代理以最高权限运行, 启动时自行补齐自愈配置 (只做一次)。"""
    if (root / TASK_HARDENED).exists() or not re.fullmatch(r'[0-9a-f]{32}', config.get('id', '')):
        return
    name = "'LiteCode-Node-" + config['id'] + "'"
    result = powershell(
        "$ErrorActionPreference='Stop'; $t=Get-ScheduledTask -TaskName " + name + "; "
        "$s=New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-MultipleInstances IgnoreNew -StartWhenAvailable; "
        "$g=@((New-ScheduledTaskTrigger -AtLogOn -User $t.Principal.UserId), "
        "(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))); "
        "Set-ScheduledTask -TaskName " + name + " -Settings $s -Trigger $g | Out-Null")
    if result.returncode == 0:
        (root / TASK_HARDENED).write_text('ok', encoding='utf-8')


def disable_task(config):
    """凭据被撤销后停用自己的计划任务, 免得每 5 分钟补拉一次空跑。"""
    if re.fullmatch(r'[0-9a-f]{32}', config.get('id', '')):
        powershell("Disable-ScheduledTask -TaskName 'LiteCode-Node-" + config['id'] + "' | Out-Null")


def launch_uninstall(root):
    process = run_quiet(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
        'Bypass', '-File', str(root / 'uninstall.ps1'), '-Root', str(root), '-Schedule'], timeout=60)
    if process.returncode != 0:
        raise RuntimeError('卸载清理任务启动失败，请使用本机管理员卸载命令')


def startup_assets(config, root):
    """启动时按控制端公布的版本与逐文件哈希加载组件; 校验不符但本地已有完整旧组件时先用旧组件。"""
    published = {}
    try:
        published = json.loads(request(config, '/versions'))
        if not isinstance(published, dict):
            published = {}
    except urllib.error.HTTPError as exc:
        if exc.code != 404:   # 404 = 控制端没有该接口, 不带校验直接下载
            raise
    except ValueError:
        published = {}        # 响应不是 JSON: 同样不带校验
    try:
        load_assets(config, root, (published.get('versions') or {}).get('assets'), published.get('asset_hashes'))
    except ValueError:
        if not all((root / asset).is_file() for asset in ASSETS):
            raise
        VERSIONS['assets'] = assets_version(root)


def revoked(exc):
    """只有控制端明确标记的 403 才算凭据撤销; 反代/防火墙返回的裸 403 按网络错误处理。"""
    headers = getattr(exc, 'headers', None) or {}
    return exc.code == 403 and str(headers.get(REVOKED_HEADER, '')).lower() == 'revoked'


def main():
    root = Path(sys.argv[1]).resolve()
    try:
        config = load_config(root)
    except Exception:
        (root / 'state.txt').write_text('凭据读取失败，请使用安装时的用户重跑原安装命令', encoding='utf-8')
        return
    sys.path.insert(0, str(root))
    # 计划任务启动的进程 cwd 是 C:\Windows\system32, 由代理拉起的应用"另存为"会默认跳到 System32;
    # 切到用户主目录 (已安装节点无需重装, 新安装另由计划任务 -WorkingDirectory 兜底)。
    try:
        os.chdir(Path.home())
    except OSError:
        pass
    delay = 2
    pending_result = None
    loaded = False
    rejections = 0   # 连续收到"凭据已撤销"标记的次数
    tried = set()   # 已尝试过的目标版本: 下载内容与公布哈希不符时不反复重下/重启
    while True:
        try:
            if not loaded:
                startup_assets(config, root)
                loaded = True
                try:
                    harden_task(config, root)
                except Exception:
                    pass   # 自愈配置失败不影响桌面任务, 下次启动再试
            if pending_result:
                job_id, payload = pending_result
                request(config, '/result/' + job_id, payload)
                pending_result = None
            response = json.loads(request(config, '/poll'))
            rejections = 0
            capabilities = response.get('capabilities') or {}
            job = response.get('job')
            if job:
                if job['action'] == 'uninstall':
                    try:
                        launch_uninstall(root)
                    except Exception:
                        request(config, '/status', {'state': 'uninstall_failed', 'error': '清理任务未能启动，请使用本机卸载命令'})
                        continue
                    return
                pending_result = (job['id'], run_job(config, root, job, capabilities))
            target = response.get('versions') or {}
            # 只在空闲时更新, 不打断正在执行或待回传的桌面动作
            if not job and target.get('assets') and target['assets'] != VERSIONS['assets'] \
                    and ('assets', target['assets']) not in tried:
                try:
                    # 网络失败抛出, 下一轮重试; 哈希不符则放弃本次更新 (旧文件保留), 同一目标版本不再重下
                    load_assets(config, root, target['assets'], response.get('asset_hashes'))
                except ValueError:
                    pass
                tried.add(('assets', target['assets']))
            if not job and target.get('agent') and target['agent'] != VERSIONS['agent'] \
                    and ('agent', target['agent']) not in tried:
                if update_agent(config, root, target['agent']):
                    (root / 'state.txt').write_text('代理已更新，正在重启', encoding='utf-8')
                    return
                tried.add(('agent', target['agent']))
            delay = 2
        except urllib.error.HTTPError as exc:
            rejections = rejections + 1 if revoked(exc) else 0
            if rejections >= REVOKE_CONFIRMATIONS:
                (root / 'state.txt').write_text('节点凭据已撤销，代理停止', encoding='utf-8')
                try:
                    disable_task(config)
                except Exception:
                    pass
                return
            if exc.code == 400 and pending_result:
                pending_result = None  # 过期结果不重做动作。
            time.sleep(delay)
            delay = min(delay * 2, 60)
        except Exception as exc:
            (root / 'state.txt').write_text(type(exc).__name__ + ': 连接或桌面任务异常', encoding='utf-8')
            time.sleep(delay)
            delay = min(delay * 2, 60)


if __name__ == '__main__':
    main()
