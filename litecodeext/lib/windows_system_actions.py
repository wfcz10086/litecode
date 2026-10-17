"""Windows 节点系统运维：系统信息、进程、结束进程、服务、受控 PowerShell。

在 Windows 登录会话内由节点代理或计划任务调用。所有重库 (psutil/ctypes/subprocess)
都在函数内导入，便于在 Linux 上以 mock 做逻辑测试。不拼接用户输入进 shell。
"""
import os

# 绝不结束的系统进程（即使带 force）。比较时忽略大小写与 .exe 后缀，见 _base。
CRITICAL = {'system', 'system idle process', 'idle', 'registry', 'memory compression', 'secure system',
            'csrss', 'wininit', 'winlogon', 'services', 'lsass', 'lsaiso', 'smss',
            'svchost', 'dwm', 'sihost', 'fontdrvhost', 'msmpeng', 'nissrv', 'securityhealthservice',
            'spoolsv', 'taskhostw', 'runtimebroker', 'ctfmon', 'conhost', 'dllhost', 'audiodg',
            'wudfhost', 'searchindexer', 'startmenuexperiencehost', 'shellexperiencehost',
            'textinputhost', 'searchhost', 'userinit', 'logonui', 'lockapp'}
# 需要 force=True 才允许结束（会影响用户桌面但可恢复）
GUARDED = {'explorer'}
OUTPUT_LIMIT = 8000
# 超时后结束进程树并收尾的额外宽限 (秒): run_powershell 保证在 timeout + KILL_GRACE 内返回
KILL_GRACE = 15


def _norm(name):
    return (name or '').strip().lower()


def _base(name):
    """进程名归一: 去空白、小写、去 .exe 后缀 (SVCHOST.EXE / svchost / Svchost.exe 视为同一个)。"""
    name = _norm(name)
    return name[:-4] if name.endswith('.exe') else name


def _session_id(pid):
    """进程所在会话号; 0 为服务会话。非 Windows 或查询失败返回 None。"""
    try:
        import ctypes
        from ctypes import wintypes
        session = wintypes.DWORD()
        if ctypes.windll.kernel32.ProcessIdToSessionId(wintypes.DWORD(pid), ctypes.byref(session)):
            return session.value
    except Exception:
        pass
    return None


def system_info(args=None):
    import platform
    import socket
    import getpass
    import time
    import psutil
    virtual = psutil.virtual_memory()
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({'device': part.device, 'mountpoint': part.mountpoint,
                      'fstype': part.fstype, 'total': usage.total,
                      'used': usage.used, 'free': usage.free, 'percent': usage.percent})
    cpu_model = platform.processor() or ''
    try:
        boot = psutil.boot_time()
        uptime = max(0, int(time.time() - boot))
    except Exception:
        uptime = None
    return {'ok': True, 'state': 'system_info', 'os': platform.platform(),
            'hostname': socket.gethostname(), 'user': getpass.getuser(),
            'cpu_model': cpu_model, 'cpu_logical': psutil.cpu_count(),
            'cpu_physical': psutil.cpu_count(logical=False),
            'cpu_percent': psutil.cpu_percent(interval=0.3),
            'memory_total': virtual.total, 'memory_used': virtual.used,
            'memory_percent': virtual.percent, 'disks': disks, 'uptime_seconds': uptime}


def processes(args):
    import psutil
    args = args or {}
    order = args.get('sort', 'memory')
    if order not in ('cpu', 'memory'):
        raise ValueError('sort 只能是 cpu 或 memory')
    top = args.get('top', 15)
    if not isinstance(top, int) or isinstance(top, bool) or not 1 <= top <= 100:
        raise ValueError('top 应为 1 到 100 的整数')
    needle = _norm(args.get('name', ''))
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            proc.cpu_percent(None)   # 第一次采样置零，下面统一读取
        except psutil.Error:
            pass
    import time
    time.sleep(0.3)
    rows = []
    for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
        try:
            info = proc.info
            name = info.get('name') or ''
            if needle and needle not in name.lower():
                continue
            memory = getattr(info.get('memory_info'), 'rss', 0) or 0
            rows.append({'pid': info['pid'], 'name': name,
                         'memory': memory, 'cpu_percent': proc.cpu_percent(None)})
        except psutil.Error:
            continue
    key = 'cpu_percent' if order == 'cpu' else 'memory'
    rows.sort(key=lambda r: r[key], reverse=True)
    return {'ok': True, 'state': 'processes', 'sort': order,
            'count': len(rows), 'processes': rows[:top]}


def _own_pids():
    """代理自身及其父进程链，避免自杀导致节点失联。"""
    import psutil
    pids = set()
    try:
        proc = psutil.Process(os.getpid())
        while proc is not None and proc.pid not in pids:
            pids.add(proc.pid)
            proc = proc.parent()
    except psutil.Error:
        pass
    return pids


def kill_process(args):
    args = args or {}
    pid = args.get('pid')
    name = args.get('name')
    force = bool(args.get('force', False))
    if (pid is None) == (not name):
        raise ValueError('kill_process 必须且只能提供 pid 或精确进程名之一')
    if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0):
        raise ValueError('pid 必须为正整数')
    import psutil
    own = _own_pids()
    targets = []
    if pid is not None:
        try:
            targets = [psutil.Process(pid)]
        except psutil.NoSuchProcess:
            raise ValueError('指定 pid 的进程不存在')
    else:
        needle = _base(name)
        targets = [p for p in psutil.process_iter(['pid', 'name']) if _base(p.info.get('name')) == needle]
        if not targets:
            raise ValueError('未找到精确匹配该名称的进程')
        if len(targets) > 1 and args.get('all') is not True:
            pids = sorted(p.pid for p in targets)
            return {'ok': False, 'state': 'kill_process_ambiguous', 'killed': [], 'skipped': [],
                    'matches': pids,
                    'error': f'名称匹配到 {len(pids)} 个进程 (pid: {", ".join(map(str, pids))})，'
                             '请改用 pid 指定，或确认全部结束时传 all=true'}
    killed, skipped = [], []
    for proc in targets:
        try:
            pname = _norm(proc.name())
        except psutil.Error:
            pname = ''
        if proc.pid in own:
            skipped.append({'pid': proc.pid, 'name': pname, 'reason': '代理自身进程，拒绝结束'})
            continue
        if _base(pname) in CRITICAL:
            skipped.append({'pid': proc.pid, 'name': pname, 'reason': '关键系统进程，拒绝结束'})
            continue
        if _session_id(proc.pid) == 0:
            skipped.append({'pid': proc.pid, 'name': pname, 'reason': '会话 0 的服务进程，拒绝结束'})
            continue
        if _base(pname) in GUARDED and not force:
            skipped.append({'pid': proc.pid, 'name': pname, 'reason': '需要 force=true 才能结束'})
            continue
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
            killed.append({'pid': proc.pid, 'name': pname})
        except psutil.Error as exc:
            skipped.append({'pid': proc.pid, 'name': pname, 'reason': '结束失败: ' + type(exc).__name__})
    return {'ok': bool(killed), 'state': 'kill_process', 'killed': killed, 'skipped': skipped}


def services(args):
    import psutil
    args = args or {}
    needle = _norm(args.get('name', ''))
    rows = []
    for service in psutil.win_service_iter():
        try:
            info = service.as_dict()
        except psutil.Error:
            continue
        display = info.get('display_name') or info.get('name') or ''
        if needle and needle not in (info.get('name', '') + display).lower():
            continue
        rows.append({'name': info.get('name'), 'display_name': display,
                     'status': info.get('status'), 'start_type': info.get('start_type')})
    rows.sort(key=lambda r: (r['name'] or '').lower())
    # 代理以普通用户运行，无权启停服务；此处只读列出。
    return {'ok': True, 'state': 'services', 'count': len(rows), 'services': rows[:500]}


# 固定前缀: 把控制台与管道输出统一到 UTF-8, 避免中文 Windows 默认 GBK/OEM 产生乱码。
PS_ENCODING_PREFIX = (
    "$OutputEncoding=[Console]::OutputEncoding=[Console]::InputEncoding=[Text.Encoding]::UTF8; "
    "$ErrorActionPreference='Continue'; ")


def _decode(data):
    """优先 UTF-8, 失败回退 GBK, 再不行用 replace, 保证中文输出尽量可读。"""
    for codec in ('utf-8-sig', 'gbk'):
        try:
            return data.decode(codec)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def run_powershell(args):
    """执行任意 PowerShell。仅当节点代理确认 shell 开关开启后才会走到这里。

    支持 cwd 工作目录；返回 exit_code/stdout/stderr/耗时；输出按 UTF-8 解码, 失败回退 GBK。
    """
    import subprocess
    import time
    args = args or {}
    script = args.get('script', '')
    if not isinstance(script, str) or not script.strip():
        raise ValueError('script 不能为空')
    if len(script) > 20000:
        raise ValueError('script 过长')
    timeout = args.get('timeout', 60)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 300:
        raise ValueError('timeout 应为 1 到 300 秒')
    cwd = args.get('cwd') or None
    if cwd is not None:
        if not isinstance(cwd, str) or not cwd.strip():
            raise ValueError('cwd 必须是目录路径')
        from pathlib import Path
        if not Path(cwd).expanduser().is_dir():
            raise ValueError('cwd 目录不存在')
        cwd = str(Path(cwd).expanduser())
    started = time.perf_counter()
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-Command', PS_ENCODING_PREFIX + script]
    code, raw_out, raw_err, timed_out = run_bounded(command, timeout, cwd=cwd)
    out, err = _decode(raw_out), _decode(raw_err)
    result = {'ok': code == 0 and not timed_out, 'state': 'run_powershell_timeout' if timed_out else 'run_powershell',
              'returncode': code, 'exit_code': code, 'cwd': cwd, 'timed_out': timed_out,
              'duration_ms': int((time.perf_counter() - started) * 1000),
              'stdout': out[:OUTPUT_LIMIT], 'stdout_truncated': len(out) > OUTPUT_LIMIT,
              'stderr': err[:OUTPUT_LIMIT], 'stderr_truncated': len(err) > OUTPUT_LIMIT}
    if timed_out:
        result['error'] = f'执行超过 {timeout} 秒，已结束整个进程树'
    return result


def _kill_tree(subprocess, process):
    """taskkill /T /F 结束整个进程树 (只结束 powershell.exe 会留下它拉起的子进程); 失败再退回 kill()。"""
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


def run_bounded(command, timeout, cwd=None, limit=1024 * 1024):
    """运行命令, 保证在 timeout + KILL_GRACE 秒内返回 (returncode, stdout, stderr, timed_out)。

    输出重定向到临时文件而不是管道: subprocess.run(capture_output) 超时后只结束 powershell.exe,
    随后无超时的 communicate() 会被继承了管道写端的孙进程一直卡住, 代理随之挂死。
    文件没有这个问题, 超时就 taskkill 进程树, 再读文件里已有的输出 (各读前 limit 字节)。
    """
    import subprocess
    import tempfile
    with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out_file, stderr=err_file,
                                   cwd=cwd, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out, code = True, None
            _kill_tree(subprocess, process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        outputs = []
        for handle in (out_file, err_file):
            handle.seek(0)
            outputs.append(handle.read(limit))
    return code, outputs[0], outputs[1], timed_out


def perform(action, folder, args):
    if action == 'system_info':
        return system_info(args)
    if action == 'processes':
        return processes(args)
    if action == 'kill_process':
        return kill_process(args)
    if action == 'services':
        return services(args)
    if action == 'run_powershell':
        return run_powershell(args)
    raise ValueError('不支持的系统动作')
