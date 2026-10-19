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
import threading
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


def atomic_bytes(path, data):
    temporary = path.with_name(path.name + '.new')
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def forget_workers(root):
    # 同一秒内更新同长度源码时, 只 invalidate_caches 仍可能命中旧 .pyc。
    for name in WORKER_MODULES:
        sys.modules.pop(name, None)
        for cache in (root / '__pycache__').glob(name + '.*.pyc'):
            cache.unlink(missing_ok=True)
    importlib.invalidate_caches()


def recover_assets(root):
    """恢复被中断的多文件替换; 恢复失败时保留备份, 调用方不得继续执行任务。"""
    import shutil
    staging = root / '.update'
    journal = staging / 'transaction.json'
    if journal.is_file() and not (staging / 'committed').exists():
        originals = json.loads(journal.read_text(encoding='utf-8'))
        names = ASSETS + ('windows_desktop_worker.py',)
        if not isinstance(originals, dict) or set(originals) != set(names) \
                or any(type(value) is not bool for value in originals.values()):
            raise ValueError('组件更新恢复记录损坏, 请保留本机 .update 备份并检查恢复记录')
        for name in names:
            if originals[name]:
                atomic_bytes(root / name, (staging / 'backup' / name).read_bytes())
            else:
                (root / name).unlink(missing_ok=True)
        forget_workers(root)
    # 先解除恢复标记再清理备份; 清理中断不能把已提交的新版本误判为待回滚。
    journal.unlink(missing_ok=True)
    shutil.rmtree(staging, ignore_errors=True)


def load_assets(config, root, expected=None, hashes=None, strict=False):
    """更新桌面组件, 全有或全无: 先全部下载到临时目录并逐个校验 (逐文件 sha256 + 整体版本哈希),
    全部通过才逐个 os.replace 到位; 替换前保留完整备份与恢复记录, 失败立即回滚,
    进程中途退出则下次启动先恢复, 不执行混合版本。Python 源码还须通过语法检查。
    strict (已验签清单): 每个文件都必须有公布哈希且必须给出整体版本, 缺一即拒绝。
    成功后丢弃已导入的旧模块, 下一次任务即用新代码, 代理本身不用重启。"""
    if strict and not expected:
        raise ValueError('签名清单缺少组件版本')
    recover_assets(root)
    staging = root / '.update'
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
            if asset.endswith('.py'):
                try:
                    compile(data, asset, 'exec')
                except (SyntaxError, ValueError) as exc:
                    raise ValueError('组件语法检查失败: ' + asset) from exc
            combined.update(data)
            (staging / asset).write_bytes(_disk_bytes(asset, data))
        if expected and combined.hexdigest()[:16] != expected:
            raise ValueError('组件整体版本与控制端公布不符')
        (staging / 'windows_desktop_worker.py').write_bytes((staging / 'worker.py').read_bytes())
        names = ASSETS + ('windows_desktop_worker.py',)
        backup = staging / 'backup'
        backup.mkdir()
        originals = {name: (root / name).is_file() for name in names}
        for name in names:
            if originals[name]:
                atomic_bytes(backup / name, (root / name).read_bytes())
        atomic_bytes(staging / 'transaction.json', json.dumps(originals).encode())
        for name in names:
            os.replace(staging / name, root / name)
        forget_workers(root)
        atomic_bytes(staging / 'committed', b'ok')
    except BaseException:
        recover_assets(root)
        raise
    else:
        recover_assets(root)
    VERSIONS['assets'] = combined.hexdigest()[:16]


# ---------------- 代理自更新回滚 ----------------
# [2026-10-19] 审计发现: 自更新写了 agent.py.previous 却从不使用。签名通过、能编译但启动即崩溃的新代理会让
# 计划任务每分钟拉起都失败, 节点永久离线、也收不到卸载/撤销任务。守护逻辑放在一个极小的启动器里
# (只用标准库, 在导入代理之前运行; 坏掉的新代理碰不到它): 新代理连续 ROLLBACK_AFTER 次启动都没有完成
# 一次成功轮询 (= 健康), 就换回 agent.py.previous, 回滚后的旧代理上报一次并不再自动升级到这个坏版本。
LAUNCHER_FILE = 'launcher.py'
PENDING_FILE = 'agent-update.json'
ROLLBACK_FILE = 'agent-rollback.json'
ROLLBACK_AFTER = 3
LAUNCHER_SOURCE = '''"""LiteCode 节点代理启动器: 计划任务运行本文件, 再由它运行 agent.py。由代理写入, 只用标准库。
[2026-10-19] 新代理能编译但启动即崩溃时, 连续 3 次启动未报告健康就换回 agent.py.previous (见 agent-update.json)。"""
import hashlib
import json
import os
import runpy
import sys
import time
from pathlib import Path

ROLLBACK_AFTER = 3


def _write(path, data):
    temporary = path.with_name(path.name + '.new')
    temporary.write_bytes(data)
    os.replace(temporary, path)


def guard(root):
    """每次启动计数; 新代理在第 ROLLBACK_AFTER 次启动后仍未把记录删掉 (= 一次都没轮询成功) 就回滚。"""
    pending_file = root / 'agent-update.json'
    try:
        pending = json.loads(pending_file.read_text(encoding='utf-8'))
        attempts, version = int(pending['attempts']), str(pending['version'])
    except (OSError, ValueError, KeyError, TypeError):
        return
    previous = root / 'agent.py.previous'
    try:
        current = hashlib.sha256((root / 'agent.py').read_bytes()).hexdigest()[:16]
    except OSError:
        current = ''
    if current != version or not previous.is_file():
        pending_file.unlink(missing_ok=True)   # 记录与磁盘上的代理对不上: 不回滚, 只清理
        return
    if attempts >= ROLLBACK_AFTER:
        _write(root / 'agent.py', previous.read_bytes())
        _write(root / 'agent-rollback.json', json.dumps({'bad_version': version, 'attempts': attempts,
                                                          'rolled_back_at': time.time()}).encode())
        pending_file.unlink(missing_ok=True)
        return
    pending['attempts'] = attempts + 1
    _write(pending_file, json.dumps(pending).encode())


if __name__ == '__main__':
    root = Path(sys.argv[1]).resolve()
    try:
        guard(root)
    except Exception:
        pass   # 守护本身出错也必须继续启动代理
    sys.argv = [str(root / 'agent.py'), str(root)]
    runpy.run_path(str(root / 'agent.py'), run_name='__main__')
'''


def ensure_launcher(root):
    """把启动器写到节点目录 (内容不同才写); 计划任务由 harden_task 迁移为运行启动器。"""
    data = LAUNCHER_SOURCE.encode()
    path = root / LAUNCHER_FILE
    if not path.is_file() or path.read_bytes() != data:
        compile(data, LAUNCHER_FILE, 'exec')
        atomic_bytes(path, data)
    return path


def mark_healthy(root):
    """新代理第一次轮询成功: 删除待确认记录, 启动器不再计数/回滚。"""
    (root / PENDING_FILE).unlink(missing_ok=True)


def load_rollback(root):
    try:
        record = json.loads((root / ROLLBACK_FILE).read_text(encoding='utf-8'))
        return record if isinstance(record, dict) and isinstance(record.get('bad_version'), str) else None
    except (OSError, ValueError):
        return None


def report_rollback(config, root, record):
    """回滚后的旧代理第一次连上控制端时上报一次 (状态 error + 原因), 之后在请求头里持续标注。"""
    message = ('代理新版本 %s 连续 %s 次启动未能连上控制端, 已自动回滚到旧版本 %s' %
               (record['bad_version'], record.get('attempts', ROLLBACK_AFTER), VERSIONS['agent']))
    try:
        (root / 'state.txt').write_text(message, encoding='utf-8')
    except OSError:
        pass
    request(config, '/status', {'state': 'error', 'error': message})
    record['reported'] = True
    atomic_bytes(root / ROLLBACK_FILE, json.dumps(record).encode())


def schedule_restart(config, root):
    """借独立计划任务重启本节点代理任务 (自更新、任务卡死后用)。失败抛出。"""
    # 重启助手必须在独立计划任务中运行: 普通子进程仍属于旧任务的进程树,
    # 自己等旧任务退出 Running 会反过来阻止旧任务结束 (Windows 真机发现)。
    task_name = 'LiteCode-Update-' + config['id']
    node_task = "'LiteCode-Node-" + config['id'] + "'"
    helper = root / 'update-restart.ps1'
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    log = quote(root / 'update-restart.log')
    # [2026-10-19] 代理拉起的 GUI 应用 (VS Code/记事本) 留在计划任务的作业对象里, 旧任务在代理退出后仍是 Running;
    # 原来等 30 秒就 throw 放弃, 每分钟的补拉又被 IgnoreNew 丢弃 → 节点离线。等待超时后只对本节点自己的任务
    # Stop-ScheduledTask 再 Start-ScheduledTask (会结束仍挂在该任务下的进程), 并记日志。
    script = ("$ErrorActionPreference='Stop'; $log=" + log + "; "
              "function LcLog($m){try{Add-Content -LiteralPath $log -Value ((Get-Date -Format s)+' '+$m) -Encoding UTF8}catch{}}; "
              "try { $old=Get-Process -Id " + str(os.getpid()) + " -ErrorAction SilentlyContinue; "
              "if($old){$old.WaitForExit()}; "
              "for($attempt=0;$attempt -lt 30;$attempt++){"
              "if((Get-ScheduledTask -TaskName " + node_task + ").State -ne 'Running'){"
              "Start-ScheduledTask -TaskName " + node_task + "; return}; Start-Sleep -Seconds 1}; "
              "LcLog '节点任务 30 秒后仍为 Running (多为代理拉起的应用留在任务中), 强制停止后重新启动'; "
              "Stop-ScheduledTask -TaskName " + node_task + "; Start-Sleep -Seconds 2; "
              "Start-ScheduledTask -TaskName " + node_task + "; LcLog '节点任务已重新启动' "
              "} catch { LcLog ('代理重启失败: '+$_.Exception.Message); throw } finally { "
              "Unregister-ScheduledTask -TaskName '" + task_name + "' -Confirm:$false -ErrorAction SilentlyContinue; "
              "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue }")
    arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + str(helper) + '"'
    launch = ("$ErrorActionPreference='Stop'; $user=[Security.Principal.WindowsIdentity]::GetCurrent().Name; "
              "$a=New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument " + quote(arguments) +
              " -WorkingDirectory " + quote(root) + "; "
              "$p=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest; "
              "$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) "
              "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew; "
              "Register-ScheduledTask -TaskName '" + task_name + "' -Action $a -Principal $p -Settings $s -Force | Out-Null; "
              "Start-ScheduledTask -TaskName '" + task_name + "'")
    try:
        atomic_bytes(helper, _disk_bytes(helper.name, script.encode()))
        if powershell(launch).returncode != 0:
            raise RuntimeError('无法启动独立更新任务, 已恢复旧代理')
    except Exception:
        try:
            powershell("Unregister-ScheduledTask -TaskName '" + task_name + "' -Confirm:$false -ErrorAction SilentlyContinue")
        except Exception:
            pass
        helper.unlink(missing_ok=True)
        raise


def update_agent(config, root, expected, full_hash=None):
    """下载新代理, 哈希与控制端公布一致才原子替换, 再借计划任务重启自己 (仍受计划任务管理)。
    full_hash: 已验签清单里的完整 sha256 (固定公钥后必须给出且一致)。返回 True 表示已安排重启、调用方应退出。"""
    if not re.fullmatch(r'[0-9a-f]{32}', config.get('id', '')):
        return False
    data = request(config, '/assets/agent.py')
    digest = hashlib.sha256(data).hexdigest()
    if digest[:16] != expected or (full_hash is not None and not hmac.compare_digest(digest, full_hash)):
        return False
    try:
        compile(data, 'agent.py', 'exec')
    except (SyntaxError, ValueError):
        return False
    previous = (root / 'agent.py').read_bytes()
    atomic_bytes(root / 'agent.py.previous', previous)
    # 先写待确认记录再换代理: 两步之间中断时, 启动器发现记录与磁盘代理不符只清理不回滚
    atomic_bytes(root / PENDING_FILE, json.dumps({'version': digest[:16], 'previous_version': VERSIONS['agent'],
                                                  'attempts': 0, 'switched_at': time.time()}).encode())
    try:
        atomic_bytes(root / 'agent.py', data)
        schedule_restart(config, root)
    except Exception:
        atomic_bytes(root / 'agent.py', previous)
        (root / PENDING_FILE).unlink(missing_ok=True)
        raise
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


# [2026-10-19] 节点异常 (组件恢复失败/任务卡死/代理已回滚) 时, 每个请求都带 ASCII 代码, 控制端轮询心跳
# 不会把单次上报的 error 状态又刷回 online。旧控制端忽略此头。
HEALTH = {}


def request(config, path, data=None):
    encoded = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    headers = {'Authorization': 'Bearer ' + config['token'], 'Content-Type': 'application/json',
               'X-Agent-Version': VERSIONS['agent'], 'X-Assets-Version': VERSIONS['assets']}
    if HEALTH:
        headers['X-Agent-Health'] = ','.join(sorted(HEALTH.values()))
    req = urllib.request.Request(config['server'] + '/api/windows/device/' + config['id'] + path,
          data=encoded, headers=headers)
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


TASK_REPAIR_INTERVAL = 3600


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
    """每次启动及每小时修复启动任务; 兼容旧节点, 不恢复管理员明确禁用的任务。"""
    if not re.fullmatch(r'[0-9a-f]{32}', config.get('id', '')):
        return
    name = "'LiteCode-Node-" + config['id'] + "'"
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    # [2026-10-19] 有启动器就让计划任务运行启动器 (自更新回滚守护), 否则沿用 agent.py
    entry = root / LAUNCHER_FILE if (root / LAUNCHER_FILE).is_file() else root / 'agent.py'
    arguments = '"' + str(entry) + '" "' + str(root) + '"'
    powershell(
        "$ErrorActionPreference='Stop'; $t=Get-ScheduledTask -TaskName " + name + " -ErrorAction SilentlyContinue; "
        "if($t -and -not $t.Settings.Enabled){return}; "
        "$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name; "
        "$a=New-ScheduledTaskAction -Execute " + quote(sys.executable) + " -Argument " + quote(arguments) +
        " -WorkingDirectory " + quote(root) + "; "
        "$p=New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest; "
        "$s=New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-MultipleInstances IgnoreNew -StartWhenAvailable; "
        "$g=@((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $user), "
        "(New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))); "
        "if($t){Set-ScheduledTask -TaskName " + name + " -Action $a -Principal $p -Settings $s -Trigger $g | Out-Null} "
        "else {Register-ScheduledTask -TaskName " + name + " -Action $a -Principal $p -Settings $s -Trigger $g | Out-Null}")


def disable_task(config):
    """凭据被撤销后停用自己的计划任务, 免得每分钟补拉一次空跑。"""
    if re.fullmatch(r'[0-9a-f]{32}', config.get('id', '')):
        powershell("$tag='litecode-node-" + config['id'] + "'; "
                   "foreach($f in @((Join-Path $env:ProgramData 'ssh/administrators_authorized_keys'), "
                   "(Join-Path $env:USERPROFILE '.ssh/authorized_keys'))){try {if(Test-Path $f){"
                   "$lines=@([IO.File]::ReadAllLines($f) | Where-Object {$_ -notmatch ('\\s'+$tag+'$')}); "
                   "[IO.File]::WriteAllLines($f,[string[]]$lines,(New-Object Text.UTF8Encoding($false)))}}catch{}}; "
                   "Disable-ScheduledTask -TaskName 'LiteCode-Node-" + config['id'] + "' | Out-Null")


def launch_uninstall(root):
    process = run_quiet(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
        'Bypass', '-File', str(root / 'uninstall.ps1'), '-Root', str(root), '-Schedule'], timeout=60)
    if process.returncode != 0:
        raise RuntimeError('卸载清理任务启动失败，请使用本机管理员卸载命令')


def startup_assets(config, root):
    """启动时按控制端公布的版本与逐文件哈希加载组件; 校验不符但本地已有完整旧组件时先用旧组件。
    已固定签名公钥: 只认验签通过的清单。未固定 (签名上线前安装的旧节点): 先取公钥固定 (首次信任)。
    返回固定的公钥信息 (None = 控制端不支持签名, 沿用旧的哈希校验)。"""
    recover_assets(root)
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


JOB_HEARTBEAT_INTERVAL = 20
# 组件恢复失败时仍放行的任务: 不依赖桌面组件 (系统运维供管理员远程修复)。与 worker.SYSTEM_ACTIONS 一致。
SYSTEM_JOBS = ('system_info', 'processes', 'kill_process', 'services', 'run_powershell')
# [2026-10-19] 审计发现: 任务没有总时限, 工作代码卡死 (窗口/COM 调用不返回) 时心跳线程仍每 20 秒报 online,
# 控制端以为节点正常却永远等不到结果。每个任务的总时限 = 任务自带 timeout + 余量, 不带则 JOB_DEFAULT_DEADLINE。
JOB_DEFAULT_DEADLINE = 600
JOB_DEADLINE_MARGIN = 120
JOB_DEADLINE_MAX = 3600


class JobStuck(Exception):
    """任务超过总时限: 看门狗已上报失败并安排重启, 主循环应直接退出。"""


def job_deadline(job):
    timeout = (job.get('request') or {}).get('timeout')
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
        return max(JOB_DEFAULT_DEADLINE, min(timeout + JOB_DEADLINE_MARGIN, JOB_DEADLINE_MAX))
    return JOB_DEFAULT_DEADLINE


def kill_job_processes(since):
    """结束本任务期间由代理拉起、仍在运行的子孙进程 (taskkill /T); 任务之前就存在的进程不动。"""
    try:
        import psutil
        children = psutil.Process().children(recursive=True)
    except Exception:
        return
    for child in children:
        try:
            if child.create_time() >= since - 1:
                kill_tree(child)
        except Exception:
            pass


def job_stuck(config, root, job, limit, since):
    """看门狗: 不再报 online, 上报任务失败与节点 error, 结束任务拉起的进程树, 安排重启后退出进程。
    任务在主线程执行 (桌面/COM 对象属于该线程), 卡死的调用无法从别的线程中断, 只能整进程重启。"""
    message = '任务 %s 超过 %d 秒仍未结束, 已判定卡死, 节点代理将结束任务进程并重启' % (job.get('action'), limit)
    HEALTH['job'] = 'job_stuck'
    try:
        (root / 'state.txt').write_text(message, encoding='utf-8')
    except OSError:
        pass
    for path, payload in (('/result/' + job['id'], {'result': {'ok': False, 'state': 'job_timeout', 'error': message},
                                                    'images': {}}),
                          ('/status', {'state': 'error', 'error': message})):
        try:
            request(config, path, payload)
        except Exception:
            pass
    kill_job_processes(since)
    try:
        schedule_restart(config, root)
    except Exception:
        pass   # 重启任务没排上: 仍退出, 由每分钟补拉的计划任务拉起
    os._exit(3)


def run_with_heartbeat(config, root, job, capabilities, runner=None):
    """长任务不再让控制端误判离线；只报告存活，不重复派发或执行动作。
    超过总时限后停止报 online, 交给 job_stuck 上报并重启 (返回前若已判定卡死则抛 JobStuck, 不重复回传结果)。"""
    stopped = threading.Event()
    fired = threading.Event()
    limit = job_deadline(job)
    since = time.time()
    deadline = time.monotonic() + limit

    def heartbeat():
        while not stopped.wait(min(JOB_HEARTBEAT_INTERVAL, max(0.0, deadline - time.monotonic()))):
            if time.monotonic() >= deadline:
                fired.set()
                job_stuck(config, root, job, limit, since)
                return
            try:
                request(config, '/status', {'state': 'online'})
            except Exception:
                pass   # 主循环统一处理重连/撤销, 心跳线程不修改更新或任务状态。

    thread = threading.Thread(target=heartbeat, name='node-job-heartbeat', daemon=True)
    thread.start()
    try:
        result = (runner or run_job)(config, root, job, capabilities)
    finally:
        stopped.set()
        thread.join(timeout=1)
    if fired.is_set():
        raise JobStuck(job['id'])
    return result


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
    task_check_due = 0
    healthy = False
    rollback = load_rollback(root)   # 启动器刚回滚过: 上报一次, 且不再自动升级到那个坏版本
    if rollback:
        HEALTH['rollback'] = 'agent_rolled_back'
    recovery_error = ''   # 非空: 组件恢复失败, 只放行卸载与系统运维任务
    recovery_report_due = 0
    while True:
        try:
            # [2026-10-19] 恢复记录损坏/备份缺失时这里每轮都抛异常, 节点再也不轮询, 收不到卸载/撤销, 也不上报任何状态。
            # 改为: 恢复失败只停用桌面任务, 照常轮询并上报 error 原因, 卸载与系统运维任务照常执行。
            try:
                recover_assets(root)   # 上轮回滚失败时先恢复, 绝不执行半套新组件
                if recovery_error:
                    recovery_error = ''
                    HEALTH.pop('recovery', None)
            except Exception as exc:
                reason = '组件更新恢复失败, 已暂停桌面任务 (卸载与系统任务仍可用): ' + (str(exc) or type(exc).__name__)
                if reason != recovery_error:
                    recovery_error, recovery_report_due = reason, 0
                    HEALTH['recovery'] = 'recovery_failed'
                    try:
                        (root / 'state.txt').write_text(reason, encoding='utf-8')
                    except OSError:
                        pass
            if time.monotonic() >= task_check_due:
                task_check_due = time.monotonic() + TASK_REPAIR_INTERVAL
                try:
                    ensure_launcher(root)
                except Exception:
                    pass   # 写不了启动器: 计划任务继续直接运行 agent.py
                try:
                    harden_task(config, root)
                except Exception:
                    pass   # 启动配置失败不影响现有桌面任务, 下次检查再试
            if not loaded and not recovery_error:
                trust = startup_assets(config, root)
                loaded = True
            if recovery_error and time.monotonic() >= recovery_report_due:
                recovery_report_due = time.monotonic() + 300
                request(config, '/status', {'state': 'error', 'error': recovery_error})
            if pending_result:
                job_id, payload = pending_result
                request(config, '/result/' + job_id, payload)
                pending_result = None
            response = json.loads(request(config, '/poll'))
            rejections = 0
            if not healthy:
                mark_healthy(root)   # 第一次轮询成功 = 新代理健康, 启动器不再回滚
                healthy = True
            if rollback and not rollback.get('reported'):
                try:
                    report_rollback(config, root, rollback)
                except Exception:
                    pass
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
                if recovery_error and job['action'] not in SYSTEM_JOBS:
                    pending_result = (job['id'], {'result': {'ok': False, 'state': 'recovery_failed', 'error': recovery_error},
                                                  'images': {}})
                else:
                    pending_result = (job['id'], run_with_heartbeat(config, root, job, capabilities))
            # 只在空闲时更新, 不打断正在执行或待回传的桌面动作; 已固定公钥时目标只来自验签通过的清单
            target, hashes = update_target(trust, response) if not job and not recovery_error else ({}, None)
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
                    and ('agent', target['agent']) not in tried \
                    and not (rollback and target['agent'] == rollback['bad_version']):
                full = (hashes or {}).get(AGENT_FILE, '') if trust else None
                if update_agent(config, root, target['agent'], full):
                    try:
                        if trust:
                            accept_manifest(root, trust, target['manifest'])
                        (root / ROLLBACK_FILE).unlink(missing_ok=True)   # 换成了另一个新版本, 旧的回滚记录作废
                        (root / 'state.txt').write_text('代理已更新，正在重启', encoding='utf-8')
                    except OSError:
                        pass   # 重启已派发, 状态落盘失败也必须退出, 不能让重启助手永久等旧进程
                    return
                tried.add(('agent', target['agent']))
            delay = 2
        except JobStuck:
            return   # 看门狗已上报失败并安排重启
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
