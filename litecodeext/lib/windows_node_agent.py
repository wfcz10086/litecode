"""运行于登录用户桌面的出站节点代理，不监听任何网络端口。"""
import base64
import hashlib
import hmac
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


# ---------------- 代码下发签名 (与控制端 lib.windows_node_signing 对应) ----------------
# 节点 venv 没有密码学库; RSA PKCS#1 v1.5 验签只需 pow + 与期望编码整体比较, 纯标准库实现。
KEY_ALG = 'RSA-PKCS1v15-SHA256'
MANIFEST_FORMAT = 'litecode-windows-manifest-v1'
TRUST_FILE = 'trust.json'
_SHA256_PREFIX = bytes.fromhex('3031300d060960864801650304020105000420')   # DigestInfo(SHA-256) 的 DER 前缀
AGENT_FILE = 'windows_node_agent.py'


def _served(asset):
    return {'worker.py': 'windows_desktop_worker.py', 'uninstall.ps1': 'windows_node_uninstall.ps1'}.get(asset, asset)


def key_fingerprint(key):
    return hashlib.sha256((key['alg'] + ':' + key['n'] + ':' + key['e']).encode()).hexdigest()


def valid_key(key):
    """只接受 RSA-PKCS1v15-SHA256、模数至少 2048 位、公钥指数为奇数且大于 1 的公钥。"""
    try:
        n = int.from_bytes(base64.b64decode(key['n'], validate=True), 'big')
        e = int.from_bytes(base64.b64decode(key['e'], validate=True), 'big')
    except (KeyError, TypeError, ValueError):
        return False
    return key.get('alg') == KEY_ALG and n.bit_length() >= 2048 and e > 1 and e % 2 == 1


def rsa_verify(key, message, signature):
    """RSASSA-PKCS1-v1_5 / SHA-256 验签: 构造完整期望编码后常量时间整体比较, 不解析填充 (避免解析类漏洞)。"""
    if not valid_key(key):
        return False
    n = int.from_bytes(base64.b64decode(key['n']), 'big')
    e = int.from_bytes(base64.b64decode(key['e']), 'big')
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, 'big')
    if value >= n:
        return False
    digest = _SHA256_PREFIX + hashlib.sha256(message).digest()
    expected = b'\x00\x01' + b'\xff' * (size - len(digest) - 3) + b'\x00' + digest
    return hmac.compare_digest(pow(value, e, n).to_bytes(size, 'big'), expected)


def load_trust(root):
    """读取安装时 (或首次信任时) 固定的签名公钥; 没有返回 None。文件损坏抛 ValueError (不降级为不验签)。"""
    path = root / TRUST_FILE
    if not path.exists():
        return None
    try:
        trust = json.loads(path.read_text(encoding='utf-8-sig'))
        key = trust['key']
        if not valid_key(key) or key_fingerprint(key) != trust['fingerprint']:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ValueError('签名公钥文件损坏，已拒绝更新；请重跑原安装命令') from None
    return trust


def save_trust(root, trust):
    temporary = root / (TRUST_FILE + '.new')
    temporary.write_text(json.dumps(trust, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, root / TRUST_FILE)


def pin_first_use(config, root):
    """旧节点 (安装时还没有签名) 升级到本代理后: 经现有设备通道取一次公钥并固定 (首次信任)。
    控制端还不支持签名 (404/503/非 JSON/格式不对) 返回 None, 继续旧的哈希校验方式; 网络错误抛出由调用方重试。"""
    try:
        info = json.loads(request(config, '/signing-key'))
        key = {k: info['key'][k] for k in ('alg', 'n', 'e')}
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 503):
            return None
        raise
    except (ValueError, KeyError, TypeError):
        return None
    if not valid_key(key):
        return None
    trust = {'key': key, 'fingerprint': key_fingerprint(key), 'pinned_by': 'first_use',
             'pinned_at': time.time(), 'last_issued': 0}
    save_trust(root, trust)
    return trust


def verified_manifest(trust, signed):
    """用固定公钥验签清单, 返回清单; 缺失、签名不符、格式不对或早于已接受清单 (回放旧版) 均抛 ValueError。"""
    if not isinstance(signed, dict):
        raise ValueError('控制端未提供签名清单')
    try:
        message = base64.b64decode(signed['manifest'], validate=True)
        signature = base64.b64decode(signed['signature'], validate=True)
    except (KeyError, TypeError, ValueError):
        raise ValueError('签名清单格式错误') from None
    if not rsa_verify(trust['key'], message, signature):
        raise ValueError('清单签名校验失败')
    manifest = json.loads(message)
    files, versions = manifest.get('files'), manifest.get('versions')
    if manifest.get('format') != MANIFEST_FORMAT or not isinstance(files, dict) or not isinstance(versions, dict) \
            or not isinstance(manifest.get('issued'), (int, float)):
        raise ValueError('签名清单格式错误')
    if manifest['issued'] < trust.get('last_issued', 0):
        raise ValueError('签名清单早于已接受的版本，疑似回放旧版本')
    return manifest


def accept_manifest(root, trust, manifest):
    """记录已接受清单的签发时间, 之后拒绝更早的清单。"""
    if manifest['issued'] > trust.get('last_issued', 0):
        trust['last_issued'] = manifest['issued']
        save_trust(root, trust)


def _disk_bytes(name, data):
    """PowerShell 5.1 按 ANSI 读无 BOM 脚本, 中文会乱码: .ps1 落盘补 UTF-8 BOM; 版本哈希按下发字节 (不含 BOM)。"""
    return BOM + data if name.endswith('.ps1') and not data.startswith(BOM) else data


def assets_version(root):
    digest = hashlib.sha256()
    for asset in ASSETS:
        data = (root / asset).read_bytes()
        digest.update(data[len(BOM):] if asset.endswith('.ps1') and data.startswith(BOM) else data)
    return digest.hexdigest()[:16]


def load_assets(config, root, expected=None, hashes=None, strict=False):
    """更新桌面组件, 全有或全无: 先全部下载到临时目录并逐个校验 (逐文件 sha256 + 整体版本哈希),
    全部通过才逐个 os.replace 到位; 任一下载失败或哈希不符则放弃本次更新, 旧文件原样保留。
    strict (已验签清单): 每个文件都必须有公布哈希且必须给出整体版本, 缺一即拒绝。
    成功后丢弃已导入的旧模块, 下一次任务即用新代码, 代理本身不用重启。"""
    if strict and not expected:
        raise ValueError('签名清单缺少组件版本')
    import shutil
    staging = root / '.update'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        combined = hashlib.sha256()
        for asset in ASSETS:
            data = request(config, '/assets/' + asset)
            want = (hashes or {}).get(_served(asset))
            if strict and not want:
                raise ValueError('签名清单缺少组件哈希: ' + asset)
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


def update_agent(config, root, expected, full_hash=None):
    """下载新代理, 哈希与控制端公布一致才原子替换, 再借计划任务重启自己 (仍受计划任务管理)。
    full_hash: 已验签清单里的完整 sha256 (固定公钥后必须给出且一致)。返回 True 表示已安排重启、调用方应退出。"""
    data = request(config, '/assets/agent.py')
    digest = hashlib.sha256(data).hexdigest()
    if digest[:16] != expected or (full_hash is not None and not hmac.compare_digest(digest, full_hash)):
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
        # 桌面动作按同一份能力决定是否放开 launch_app 任意名称/"运行"对话框快捷键; 总是以本地能力覆盖请求里的同名字段。
        request = dict(job.get('request') or {})
        request['shell_allowed'] = (capabilities or {}).get('shell') is True
        # 单进程串行执行，桌面句柄属于登录用户会话。
        result = run(job['action'], folder, request)
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
    """启动时按控制端公布的版本与逐文件哈希加载组件; 校验不符但本地已有完整旧组件时先用旧组件。
    已固定签名公钥: 只认验签通过的清单。未固定 (签名上线前安装的旧节点): 先取公钥固定 (首次信任)。
    返回固定的公钥信息 (None = 控制端不支持签名, 沿用旧的哈希校验)。"""
    try:
        trust = load_trust(root) or pin_first_use(config, root)
    except ValueError:
        trust = {'broken': True}   # 公钥文件损坏: 不更新任何代码, 只用本地已有组件
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
        if trust and trust.get('broken'):
            raise ValueError('签名公钥文件损坏')
        if trust:
            manifest = verified_manifest(trust, published.get('signed_manifest'))
            load_assets(config, root, manifest['versions'].get('assets'), manifest['files'], strict=True)
            accept_manifest(root, trust, manifest)
        else:
            load_assets(config, root, (published.get('versions') or {}).get('assets'), published.get('asset_hashes'))
    except ValueError as exc:
        if not all((root / asset).is_file() for asset in ASSETS):
            raise
        if trust:
            (root / 'state.txt').write_text('组件更新被拒绝: ' + str(exc), encoding='utf-8')
        VERSIONS['assets'] = assets_version(root)
    return trust


def update_target(trust, response):
    """本轮轮询的更新目标 (versions, 逐文件哈希)。已固定公钥时只认验签通过的清单, 验签失败不更新。"""
    if not trust:
        return response.get('versions') or {}, response.get('asset_hashes')
    if trust.get('broken'):
        return {}, None
    try:
        manifest = verified_manifest(trust, response.get('signed_manifest'))
    except ValueError:
        return {}, None
    return dict(manifest['versions'], manifest=manifest), manifest['files']


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
    trust = None
    rejections = 0   # 连续收到"凭据已撤销"标记的次数
    tried = set()   # 已尝试过的目标版本: 下载内容与公布哈希不符时不反复重下/重启
    while True:
        try:
            if not loaded:
                trust = startup_assets(config, root)
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
            # 只在空闲时更新, 不打断正在执行或待回传的桌面动作; 已固定公钥时目标只来自验签通过的清单
            target, hashes = update_target(trust, response) if not job else ({}, None)
            if not job and target.get('assets') and target['assets'] != VERSIONS['assets'] \
                    and ('assets', target['assets']) not in tried:
                try:
                    # 网络失败抛出, 下一轮重试; 哈希不符则放弃本次更新 (旧文件保留), 同一目标版本不再重下
                    load_assets(config, root, target['assets'], hashes, strict=bool(trust))
                    if trust:
                        accept_manifest(root, trust, target['manifest'])
                except ValueError:
                    pass
                tried.add(('assets', target['assets']))
            if not job and target.get('agent') and target['agent'] != VERSIONS['agent'] \
                    and ('agent', target['agent']) not in tried:
                full = (hashes or {}).get(AGENT_FILE, '') if trust else None
                if update_agent(config, root, target['agent'], full):
                    if trust:
                        accept_manifest(root, trust, target['manifest'])
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
