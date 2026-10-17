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

# 顺序与控制端 lib.windows_nodes.WORKER_FILES 一致, 版本哈希按这个顺序拼接
ASSETS = ('worker.py', 'windows_desktop_actions.py', 'windows_desktop_ocr.py',
          'windows_system_actions.py', 'windows_file_actions.py', 'windows_transfer.py')
WORKER_MODULES = ('windows_desktop_worker', 'windows_desktop_actions', 'windows_desktop_ocr',
                  'windows_system_actions', 'windows_file_actions', 'windows_transfer')
VERSIONS = {'agent': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16], 'assets': ''}


def assets_version(root):
    digest = hashlib.sha256()
    for asset in ASSETS:
        digest.update((root / asset).read_bytes())
    return digest.hexdigest()[:16]


def load_assets(config, root):
    """下载桌面组件并丢弃已导入的旧模块, 下一次任务即用新代码, 代理本身不用重启。"""
    for asset in ASSETS:
        (root / asset).write_bytes(request(config, '/assets/' + asset))
    (root / 'windows_desktop_worker.py').write_bytes((root / 'worker.py').read_bytes())
    for name in WORKER_MODULES:
        sys.modules.pop(name, None)
    importlib.invalidate_caches()
    VERSIONS['assets'] = assets_version(root)


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


def powershell(script, timeout=60):
    return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-Command', script], capture_output=True, timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


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
    process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
        'Bypass', '-File', str(root / 'uninstall.ps1'), '-Root', str(root), '-Schedule'],
        capture_output=True, timeout=60)
    if process.returncode:
        raise RuntimeError('卸载清理任务启动失败，请使用本机管理员卸载命令')


def main():
    root = Path(sys.argv[1]).resolve()
    try:
        config = load_config(root)
    except Exception:
        (root / 'state.txt').write_text('凭据读取失败，请使用安装时的用户重跑原安装命令', encoding='utf-8')
        return
    sys.path.insert(0, str(root))
    delay = 2
    pending_result = None
    loaded = False
    tried = set()   # 已尝试过的目标版本: 下载内容与公布哈希不符时不反复重下/重启
    while True:
        try:
            if not loaded:
                load_assets(config, root)
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
                load_assets(config, root)   # 网络失败抛出, 下一轮重试; 成功后记下避免重复下载
                tried.add(('assets', target['assets']))
            if not job and target.get('agent') and target['agent'] != VERSIONS['agent'] \
                    and ('agent', target['agent']) not in tried:
                if update_agent(config, root, target['agent']):
                    (root / 'state.txt').write_text('代理已更新，正在重启', encoding='utf-8')
                    return
                tried.add(('agent', target['agent']))
            delay = 2
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
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
