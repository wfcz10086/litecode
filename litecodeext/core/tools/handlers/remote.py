"""handlers/remote.py — ssh_exec + docker_remote_* (基于 paramiko)."""
from __future__ import annotations

import asyncio
import ntpath
import os
import re
import errno
import shlex
import stat as _stat
import time
import uuid
from typing import Any

from . import register
from .ssh_hosts import resolve_ssh_args

_DEFAULT_TIMEOUT = 60
# [2026-10-19] 主机刚重启时 SSH 握手报 "Error reading SSH protocol banner"/拒连, 原样抛给模型后
# 它盲 sleep 60/75/90/120/150s 反复试。连接阶段内置短重试 (只重试连接, 命令开始后绝不重跑)。
_CONNECT_RETRY_BUDGET = 75
_CONNECT_BACKOFF = (3, 6, 10, 15, 20)


class HostNotReady(Exception):
    """连接/握手阶段失败且重试预算用尽 (主机多半在重启)。"""


def _stall_window(timeout: float) -> float:
    """[2026-10-19] 无输出卡死窗口: wsl.exe docker pull -q 900s 一个字节都没吐, rc=124 白等 15 分钟。
    timeout ≤120 的短命令窗口 ≥ timeout, 等价不检测, 安静的短命令不受影响。"""
    return max(120.0, timeout / 3)


def _clip(s: str, n: int) -> str:
    """[2026-10-19] 远程输出只留头部时, 最后的报错 (往往在末尾) 被截掉; 改为保头+尾。"""
    if len(s) <= n:
        return s
    head = n * 2 // 3
    tail = n - head
    return s[:head] + f"\n...[truncated {len(s) - n} chars]...\n" + s[-tail:]


def _is_not_ready(ex: BaseException) -> bool:
    """连接/握手阶段的'还没起来'类错误 (可重试); 认证失败/DNS 错误不算。"""
    name = type(ex).__name__
    if 'Authentication' in name or 'gaierror' in name:
        return False
    if name == 'NoValidConnectionsError':
        return True
    if isinstance(ex, (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError,
                       EOFError, TimeoutError)):
        return True
    if isinstance(ex, OSError) and ex.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ECONNREFUSED,
                                                errno.ECONNRESET):
        return True
    msg = str(ex)
    return any(k in msg for k in ('Error reading SSH protocol banner', 'Connection reset',
                                  'Connection refused', 'Unable to connect'))

# 检测命令里是否含后台进程 (nohup ... 或 ... &)
_BG_RE = re.compile(r'(?:(?:^|[;|&{\n]\s*)nohup\s|&\s*(?:#[^\n]*)?\s*$)')


def _wrap_bg(cmd: str) -> str:
    """后台进程命令需关闭 stdin, 否则 SSH channel 永远不会自动关闭导致 recv_exit_status 挂死."""
    if _BG_RE.search(cmd.strip()):
        # 把整条命令包进 { ... } 并重定向 stdin 到 /dev/null
        # 这样 nohup/& 子进程不会继承 SSH channel 的 stdin fd
        return f'{{ {cmd}\n}} </dev/null'
    return cmd


def _auth_kwargs(password: str, key_file: str) -> dict:
    """有 key_file 用密钥 (可同时带 password 作为密钥失败后的兜底), 否则纯密码。"""
    kw = {"password": password or None}
    if key_file:
        kw["key_filename"] = os.path.expanduser(key_file)
    return kw


def _open_jump(jump: dict, host: str, port: int):
    """经跳板机开一条到 host:port 的通道 (等价 ssh -J), 返回 (跳板 client, channel)。"""
    import paramiko
    jc = paramiko.SSHClient()
    jc.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    jc.connect(hostname=jump["host"], port=int(jump.get("port") or 22), username=jump.get("user") or "root",
               **_auth_kwargs(jump.get("password", ""), jump.get("key_file", "")),
               timeout=15, banner_timeout=15, auth_timeout=15, allow_agent=False, look_for_keys=False)
    return jc, jc.get_transport().open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0))


def _run_ssh(host: str, port: int, user: str, password: str, cmd: str, timeout: int,
             key_file: str = "", jump: dict | None = None, stdin_data: bytes | None = None,
             powershell_script: bytes | None = None) -> tuple[int, str, str]:
    conn = {"host": host, "port": port, "user": user, "password": password, "key_file": key_file, "jump": jump}
    t0 = time.monotonic()
    client, jc = _connect(conn)
    try:
        # [2026-10-19] 跳板机握手 + 连接重试可吃掉 45~75s+, 之后命令仍按完整 timeout 跑, 外层 asyncio
        # deadline 先到: 调用方报"超时/结果未知", 工作线程却还在远端执行。命令预算从连接前起算,
        # 至少给外层留 15s 收尾; 连接已耗尽预算就不再执行命令。
        left = _hard_deadline(timeout) - 15 - (time.monotonic() - t0)
        if left < 1:
            raise HostNotReady(f"连接 {host}:{port} 耗时 {int(time.monotonic() - t0)}s, 已用尽 ssh_exec 总预算; "
                               "命令未执行。稍后重试或先确认主机/跳板状态。")
        timeout = int(min(timeout, left))
        if powershell_script is not None:
            return _run_powershell_file(client, powershell_script, timeout)
        return _read_ssh_command(client, cmd, timeout, stdin_data)
    finally:
        for c in (client, jc):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass


def _read_ssh_command(client, cmd: str, timeout: float, stdin_data: bytes | None = None) -> tuple[int, str, str]:
    actual_cmd = _wrap_bg(cmd)
    stdin, stdout, stderr = client.exec_command(actual_cmd, timeout=timeout, get_pty=False)

    # channel 级别设超时, 防止 recv_exit_status 无限阻塞
    chan = stdout.channel
    chan.settimeout(timeout)
    start = time.monotonic()
    deadline = start + timeout

    if stdin_data is None:
        # [2026-10-19] 不关 stdin 时 cat / read / python REPL / 交互提示会一直等输入直到超时。
        try:
            chan.shutdown_write()
        except Exception:
            pass
    else:
        # WSL 引导先完整读取 ASCII stdin，随后才执行原始 Bash 脚本。
        # 代码不拼进 Windows 命令行，关闭写侧让 ReadToEnd 收到 EOF。
        try:
            stdin.write(stdin_data)
            stdin.flush()
            stdin.close()
        except TimeoutError:
            chan.close()
            return 124, '', 'SSH command input timed out; remote completion is unknown. Check the remote process before retrying.'

    # 轮询读取输出 + 等待 exit status, 避免先 read() 再 recv_exit_status() 的死锁
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    stall = _stall_window(timeout)
    last_data = start
    stalled = False
    while True:
        now = time.monotonic()
        if now >= deadline:
            break
        if now - last_data >= stall:
            stalled = True
            break
        if chan.exit_status_ready():
            break
        if chan.recv_ready():
            data = chan.recv(8192)
            if data:
                out_chunks.append(data)
                last_data = now
        if chan.recv_stderr_ready():
            data = chan.recv_stderr(8192)
            if data:
                err_chunks.append(data)
                last_data = now
        if not chan.recv_ready() and not chan.recv_stderr_ready():
            time.sleep(0.05)

    # 排干剩余输出
    while chan.recv_ready():
        out_chunks.append(chan.recv(8192))
    while chan.recv_stderr_ready():
        err_chunks.append(chan.recv_stderr(8192))

    completed = chan.exit_status_ready()
    rc = chan.recv_exit_status() if completed else 124
    out = b"".join(out_chunks).decode("utf-8", errors="replace")
    err = b"".join(err_chunks).decode("utf-8", errors="replace")
    if not completed and stalled:
        chan.close()
        err += (f"\nSSH command produced no output for {int(stall)}s (stalled); stopped waiting early, "
                "remote completion is unknown (the process may still be running). For long quiet jobs "
                "(docker pull / build / big downloads) run in background: "
                "nohup <cmd> > /tmp/job.log 2>&1 & — then poll the log with tail/ps instead of waiting.\n")
    elif not completed:
        chan.close()
        err += (f"\nSSH command timed out after {timeout}s; remote completion is unknown. "
                "Check the remote process state before retrying.\n")
    return rc, out, err


def _run_powershell_file(client, script: bytes, timeout: int) -> tuple[int, str, str]:
    """同一连接探测 Windows 临时目录并独占上传；只删除本次创建的脚本。"""
    from lib.windows_ssh import powershell_command, powershell_file_command

    deadline = time.monotonic() + timeout

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('PowerShell file delivery deadline exceeded')
        return value

    probe = powershell_command('[Console]::Out.Write([IO.Path]::GetTempPath())')
    # [2026-10-19] 原 10s 上限: 冷启动/繁忙 VM 上 powershell.exe 启动就要 10s+, 短脚本能跑而长脚本必挂在探测。
    rc, output, error = _read_ssh_command(client, probe, min(30, remaining()))
    if rc != 0:
        return rc, '', 'Windows temporary directory lookup failed.\n' + error
    temp = output.strip()
    if not ntpath.isabs(temp) or any(c in temp for c in '\x00\r\n'):
        raise ValueError('Windows SSH returned an invalid temporary directory')
    path = ntpath.join(temp, 'LiteCodeSSH-' + uuid.uuid4().hex + '.ps1')
    command = powershell_file_command(path)
    # Windows OpenSSH SFTP 接受 C:/... 绝对路径；父目录来自目标机，不使用 Linux mkdir。
    sftp_path = path.replace('\\', '/')
    sftp = client.open_sftp()
    created = False
    cleanup_error = ''
    try:
        sftp.get_channel().settimeout(min(10, remaining()))
        handle = sftp.open(sftp_path, 'wx')
        created = True
        with handle:
            handle.write(script)
            handle.flush()
        result = _read_ssh_command(client, command, remaining())
    except TimeoutError:
        result = (124, '', 'PowerShell script delivery/execution timed out; remote completion is unknown. '
                           'Check the remote process before retrying.')
    finally:
        if created:
            try:
                sftp.get_channel().settimeout(3)
                sftp.remove(sftp_path)
            except Exception as exc:
                cleanup_error = (f'\nTemporary PowerShell script cleanup was not confirmed: {path} '
                                 f'({type(exc).__name__}).')
        _close(sftp)
    rc, output, error = result
    return rc, output, error + cleanup_error


def _hard_deadline(timeout: float) -> float:
    """_ssh 外层 asyncio 总预算; _run_ssh 按同一数字从连接前倒算命令可用时间。"""
    return timeout + 10 + _CONNECT_RETRY_BUDGET + 60


async def _ssh(host: str, port: int, user: str, password: str, cmd: str, timeout: int,
               key_file: str = "", jump: dict | None = None, stdin_data: bytes | None = None,
               powershell_script: bytes | None = None) -> tuple[int, str, str]:
    loop = asyncio.get_event_loop()
    # asyncio 层硬性 deadline, 防止 executor 线程永久阻塞 (含连接阶段重试预算 + 首末两次连接各 30s)
    hard = _hard_deadline(timeout)
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _run_ssh, host, port, user, password, cmd, timeout, key_file, jump, stdin_data,
                                 powershell_script),
            timeout=hard,
        )
    except asyncio.TimeoutError:
        cleanup = ' Temporary script cleanup may still be pending.' if powershell_script is not None else ''
        raise TimeoutError(f"ssh_exec asyncio deadline: {hard}s exceeded; remote completion is unknown. "
                           "Check the remote process before retrying." + cleanup)


@register("ssh_exec")
async def h_ssh_exec(sid: str, args: dict) -> tuple[str, Any]:
    conn, err = _conn(args)
    cmd = args.get("cmd") or args.get("command") or ""
    timeout = int(args.get("timeout") or _DEFAULT_TIMEOUT)
    if not conn or not cmd:
        return f"ERROR: ssh_exec {err or '需要 host + cmd'}", None
    shell = args.get('shell', 'auto')
    if shell not in ('auto', 'powershell', 'wsl'):
        return 'ERROR: ssh_exec shell 只支持 auto / powershell / wsl', None
    extra = {}
    if shell == 'wsl':
        from lib.windows_ssh import wsl_command
        try:
            cmd, extra['stdin_data'] = wsl_command(cmd, args.get('wsl_distro'), args.get('wsl_user', ''),
                                                       timeout=timeout)
        except ValueError as ex:
            return 'ERROR: ssh_exec ' + str(ex), None
    if shell == 'powershell':
        # Windows OpenSSH 的默认 shell 可能是 cmd 或 PowerShell。直接把脚本拼进
        # -Command 会被外层再次展开 $, 引号及换行；在工具内编码，模型只需提供原始脚本。
        from lib.windows_ssh import POWERSHELL_COMMAND_LIMIT, powershell_command, powershell_script
        script = powershell_script(cmd)
        cmd = powershell_command(cmd)
        if len(cmd) > POWERSHELL_COMMAND_LIMIT:
            extra['powershell_script'] = script.encode('utf-8-sig')
            cmd = ''  # 连接后上传并生成短引导，不把长脚本送入默认 cmd.exe。
    if args.get('sudo'):
        # [2026-10-21] Linux 主机提权: 用主机库里已存的密码经标准输入交给 sudo, 不拼进命令
        # (原来模型写 `echo 密码 | sudo -S`, 密码进了命令行、日志和工具历史)。
        if shell != 'auto':
            return 'ERROR: ssh_exec sudo 只用于 Linux 主机 (shell=auto)', None
        if not conn["password"]:
            return 'ERROR: ssh_exec sudo 需要该主机已保存 sudo 密码 (ssh_save_host)', None
        import shlex
        cmd = "sudo -S -p '' bash -c " + shlex.quote(cmd)
        extra['stdin_data'] = (conn["password"] + "\n").encode()
    try:
        rc, out, e = await _ssh(conn["host"], conn["port"], conn["user"], conn["password"], cmd, timeout,
                                conn["key_file"], conn["jump"], **extra)
        if args.get('sudo') and conn["password"]:
            out, e = (out or "").replace(conn["password"], "***"), (e or "").replace(conn["password"], "***")
        head = f"[ssh {conn['user']}@{conn['host']}:{conn['port']}] rc={rc}"
        body = ""
        if out:
            body += f"\n--- stdout ---\n{_clip(out, 4000)}"
        if e:
            body += f"\n--- stderr ---\n{_clip(e, 2000)}"
        if rc != 0 and shell in ('powershell', 'wsl') and 'NativeCommandError' in (e or ''):
            # [2026-10-19] Windows PowerShell 5.1 + ErrorActionPreference=Stop 下 `git ... 2>&1` 写 stderr
            # 就被当成 NativeCommandError 终止, 模型以为 git 本身失败。只在出错路径给提示, 不改全局行为。
            body += ("\n[hint] 原生命令写 stderr 被 PowerShell 当成错误; "
                     "脚本里局部 $ErrorActionPreference='Continue' 再调 git 之类")
        return head + (body or "\n(no output)"), None
    except HostNotReady as ex:
        return f"ERROR: {ex}", None
    except Exception as ex:
        return f"ERROR: ssh_exec {type(ex).__name__}: {ex}", None


def _conn(args: dict) -> tuple[dict | None, str]:
    """解析连接参数 (主机别名 / 密钥 / 跳板别名), 所有 ssh_* / docker_remote_* 工具共用。
    返回 (conn, "") 或 (None, 错误说明)。"""
    args = resolve_ssh_args(args)
    host = (args.get("host") or "").strip()
    if not host:
        return None, "需要 host (或已保存的 alias)"
    conn = {"host": host, "port": int(args.get("port") or 22), "user": (args.get("user") or "root").strip(),
            "password": args.get("password") or "", "key_file": (args.get("key_file") or "").strip(), "jump": None}
    if not conn["password"] and not conn["key_file"]:
        return None, "需要 password 或 key_file"
    jump = args.get("jump") or None
    if isinstance(jump, str):                     # 跳板写别名: 从 ssh_save_host 存的主机里取
        resolved = resolve_ssh_args({"alias": jump})
        if not resolved.get("host"):
            return None, f"跳板别名 {jump} 没存过 (先 ssh_save_host)"
        jump = resolved
    conn["jump"] = jump
    return conn, ""


def _connect(conn: dict):
    """按 conn 连上目标 (可经跳板), 返回 (client, jump_client)。用完调 _close。
    握手/拒连类错误在 _CONNECT_RETRY_BUDGET 内退避重试, 用尽抛 HostNotReady。"""
    t0 = None   # 首次失败才计时, 成功路径不碰 monotonic
    attempt = 0
    while True:
        try:
            return _connect_once(conn)
        except Exception as ex:
            if not _is_not_ready(ex):
                raise
            now = time.monotonic()
            t0 = now if t0 is None else t0
            elapsed = now - t0
            wait = _CONNECT_BACKOFF[min(attempt, len(_CONNECT_BACKOFF) - 1)]
            attempt += 1
            if elapsed + wait > _CONNECT_RETRY_BUDGET:
                raise HostNotReady(
                    f"主机 {conn['host']}:{conn['port']} 还没就绪 (SSH 握手失败, 可能在重启中): "
                    f"{type(ex).__name__}: {ex}。已在 {int(elapsed)}s 内自动重试 {attempt} 次; "
                    "别盲目长时间 sleep, 稍后再试一次或先确认主机状态。") from ex
            time.sleep(wait)


def _connect_once(conn: dict):
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    jc = sock = None
    try:
        if conn["jump"]:
            jc, sock = _open_jump(conn["jump"], conn["host"], conn["port"])
        client.connect(hostname=conn["host"], port=conn["port"], username=conn["user"],
                       **_auth_kwargs(conn["password"], conn["key_file"]), sock=sock,
                       timeout=15, banner_timeout=15, auth_timeout=15, allow_agent=False, look_for_keys=False)
    except Exception:
        _close(client, jc)
        raise
    return client, jc


def _close(*clients) -> None:
    for c in clients:
        try:
            if c is not None:
                c.close()
        except Exception:
            pass


def _remote_run(client):
    """给 lib.sftp_transfer 用的远端命令执行器 (算 sha256), 拿不到结果返回 None 由其回退 SFTP 回读。"""
    def run(cmd: str):
        _i, so, _e = client.exec_command(cmd, timeout=900)
        out = so.read()
        rc = so.channel.recv_exit_status()
        if not isinstance(out, (bytes, str)) or not isinstance(rc, int):
            return None
        return rc, out.decode(errors="replace") if isinstance(out, bytes) else out, ""
    return run


def _sftp_replace(sftp, path: str, content, backup: bool, client=None) -> dict:
    """[large-file 2026-10-21] 写临时文件 → sha256 校验 → 备份 → 原子改名 → 恢复权限/属主
    (实现见 lib/sftp_transfer; 2026-10-19 的保留 x 位/属主语义不变)。"""
    from lib import sftp_transfer as st
    data = content.encode() if isinstance(content, str) else (content or b"")
    return st.write_bytes(sftp, path, data, backup, _remote_run(client) if client is not None else None)


_LOCAL_DENY = (os.path.expanduser("~/.ssh"), os.path.expanduser("~/.litecode/keys"))


def _check_local(local: str) -> str:
    """ssh 上传的本地源: 必须是普通文件; 不许取 .secrets/ 与 SSH/控制端私钥目录。返回错误串或空串。"""
    rp = os.path.realpath(local)
    if not os.path.isfile(rp):
        return f"本地文件不存在或不是普通文件: {local}"
    if "/.secrets/" in rp + "/" or any(rp == d or rp.startswith(d + os.sep) for d in _LOCAL_DENY):
        return f"不允许上传密钥目录里的文件: {local}"
    return ""


def _sftp_write(conn: dict, path: str, content: str, backup: bool) -> str:
    client, jc = _connect(conn)
    try:
        sftp = client.open_sftp()
        r = _sftp_replace(sftp, path, content, backup, client)
        sftp.close()
        return (f"OK: wrote {r['size']} bytes → {path} (sha256 {r['sha256'][:16]}… 已校验, {r['verify']})"
                + (f" (backup: {path}.bak)" if backup else ""))
    finally:
        _close(client, jc)


def _sftp_upload(conn: dict, local: str, path: str, backup: bool, resume: bool) -> str:
    from lib import sftp_transfer as st
    client, jc = _connect(conn)
    try:
        sftp = client.open_sftp()
        t0 = time.monotonic()
        r = st.upload_file(sftp, local, path, backup, resume, _remote_run(client))
        sftp.close()
        dt = max(time.monotonic() - t0, 1e-6)
        sent = r["size"] - r["resumed_from"]
        res = f" (断点续传: 从 {r['resumed_from']} 字节处接着传)" if r["resumed_from"] else ""
        return (f"OK: uploaded {local} → {path} {r['size']} bytes, {sent / 1048576 / dt:.1f}MB/s{res}; "
                f"sha256 {r['sha256']} 两端一致 ({r['verify']})" + (f" (backup: {path}.bak)" if backup else ""))
    finally:
        _close(client, jc)


def _tag(conn: dict) -> str:
    return f"{conn['user']}@{conn['host']}:{conn['port']}" + (f" via {conn['jump']['host']}" if conn["jump"] else "")


async def _in_thread(fn, timeout: int):
    # 外层 deadline 留出 _connect 握手重试预算, 否则重试没跑完就被 wait_for 截断
    return await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, fn),
                                  timeout=timeout + _CONNECT_RETRY_BUDGET + 60)


@register("ssh_write_file")
async def h_ssh_write_file(sid: str, args: dict) -> tuple[str, Any]:
    conn, err = _conn(args)
    path = (args.get("path") or "").strip()
    local = (args.get("local_path") or "").strip()
    if not conn or not path:
        return f"ERROR: ssh_write_file {err or '需要 host + path + content (或 local_path)'}", None
    backup = args.get("backup", True)
    try:
        if local:
            if args.get("content"):
                return "ERROR: ssh_write_file content 与 local_path 只能给一个", None
            bad = _check_local(local)
            if bad:
                return f"ERROR: ssh_write_file {bad}", None
            from lib.sftp_transfer import transfer_timeout
            msg = await _in_thread(lambda: _sftp_upload(conn, local, path, backup, args.get("resume", True)),
                                   transfer_timeout(os.path.getsize(local)))
        else:
            msg = await _in_thread(lambda: _sftp_write(conn, path, args.get("content") or "", backup), 30)
        return f"[sftp {_tag(conn)}] {msg}", None
    except Exception as e:
        hint = ("; 已传部分留在远端 <path>.lcpart, 原样重试会断点续传" if local and not isinstance(e, ValueError) else "")
        return f"ERROR: ssh_write_file {type(e).__name__}: {e}{hint}", None


@register("ssh_sync_files")
async def h_ssh_sync_files(sid: str, args: dict) -> tuple[str, Any]:
    conn, err = _conn(args)
    files: dict = args.get("files") or {}
    backup = args.get("backup", True)
    if not conn or not files:
        return f"ERROR: ssh_sync_files {err or '需要 host + files 字典'}", None
    from lib import sftp_transfer as st
    total = 0
    for v in files.values():
        if isinstance(v, dict) and v.get("local_path"):
            bad = _check_local(str(v["local_path"]))
            if bad:
                return f"ERROR: ssh_sync_files {bad}", None
            total += os.path.getsize(str(v["local_path"]))
        else:
            total += len(str(v or "").encode())

    def _do_sync():
        client, jc = _connect(conn)
        results = []
        try:
            sftp = client.open_sftp()
            run = _remote_run(client)
            for path, content in files.items():
                try:
                    if isinstance(content, dict) and content.get("local_path"):
                        r = st.upload_file(sftp, str(content["local_path"]), path, backup, True, run)
                    else:
                        r = _sftp_replace(sftp, path, content or "", backup, client)
                    results.append(f"  ✓ {path} ({r['size']} bytes, sha256 {r['sha256'][:12]}… 已校验)")
                except Exception as e:
                    results.append(f"  ✗ {path}: {e} (该文件未改动)")
            sftp.close()
        finally:
            _close(client, jc)
        return results

    try:
        results = await _in_thread(_do_sync, max(60, st.transfer_timeout(total)))
        return f"[sftp {_tag(conn)}] sync {len(files)} files:\n" + "\n".join(results), None
    except Exception as e:
        return f"ERROR: ssh_sync_files {type(e).__name__}: {e}", None


@register("ssh_read_file")
async def h_ssh_read_file(sid: str, args: dict) -> tuple[str, Any]:
    conn, err = _conn(args)
    path = (args.get("path") or "").strip()
    max_chars = int(args.get("max_chars") or 20000)
    if not conn or not path:
        return f"ERROR: ssh_read_file {err or '需要 host + path'}", None

    def _do_read():
        # [2026-10-19] 原来按解码后字符数判截断: 中文 3 字节/字, 读 max_chars*2 字节只解出 ~2/3 字符,
        # 40KB+ 中文文件被截了却不标 truncated。改按字节: 读到的字节 < 远端大小 (或读满上限) 即截断。
        limit = max_chars * 2  # bytes, UTF-8 may be multi-byte
        client, jc = _connect(conn)
        try:
            sftp = client.open_sftp()
            try:
                size = sftp.stat(path).st_size
            except Exception:
                size = None
            with sftp.open(path, "r") as f:
                raw = f.read(limit)
            sftp.close()
            text = raw.decode("utf-8", errors="replace")
            cut = len(text) > max_chars or (len(raw) < size if isinstance(size, int) else len(raw) >= limit)
            return text[:max_chars], cut
        finally:
            _close(client, jc)

    try:
        content, truncated = await _in_thread(_do_read, 30)
        return (f"[sftp {_tag(conn)}] {path} ({len(content)} chars):\n{content}"
                + ("\n...(truncated)" if truncated else "")), None
    except Exception as e:
        return f"ERROR: ssh_read_file {type(e).__name__}: {e}", None


@register("ssh_str_replace")
async def h_ssh_str_replace(sid: str, args: dict) -> tuple[str, Any]:
    """在远程文件上做 str_replace — 等价于 Claude Code 的 str_replace_based_edit_tool 但走 SSH.
    对大文件特别有用: 只传 old_str/new_str, 不需要传整个文件内容.
    """
    conn, err = _conn(args)
    path = (args.get("path") or "").strip()
    old_str = args.get("old_str") or args.get("old_string") or ""
    new_str = str(args.get("new_str") if args.get("new_str") is not None else (args.get("new_string") or ""))
    if not conn or not path or not old_str:
        return f"ERROR: ssh_str_replace {err or '需要 host + path + old_str'}", None

    def _do_replace():
        client, jc = _connect(conn)
        try:
            sftp = client.open_sftp()
            with sftp.open(path, "r") as f:
                content = f.read().decode("utf-8", errors="replace")
            count = content.count(old_str)
            if count == 0:
                return False, "NOT_FOUND: old_str not found in file"
            if count > 1:
                return False, f"AMBIGUOUS: old_str matches {count} times; make it more specific"
            new_content = content.replace(old_str, new_str, 1)
            _sftp_replace(sftp, path, new_content, True, client)     # backup + 保留权限 + 校验
            sftp.close()
            return True, f"OK: replaced 1 occurrence in {path} ({len(content)}→{len(new_content)} chars)"
        finally:
            _close(client, jc)

    try:
        ok, msg = await _in_thread(_do_replace, 30)
        return (f"[sftp {_tag(conn)}] {msg}" if ok else f"ERROR: ssh_str_replace {msg}"), None
    except Exception as e:
        return f"ERROR: ssh_str_replace {type(e).__name__}: {e}", None


async def _ssh_conn(conn: dict, cmd: str, timeout: int):
    return await _ssh(conn["host"], conn["port"], conn["user"], conn["password"], cmd, timeout,
                      conn["key_file"], conn["jump"])


@register("docker_remote_ps")
async def h_docker_remote_ps(sid: str, args: dict) -> tuple[str, Any]:
    conn, err = _conn(args)
    if not conn:
        return f"ERROR: docker_remote_ps {err}", None
    all_flag = "-a" if args.get("all") else ""
    cmd = f"docker ps {all_flag} --format 'table {{{{.Names}}}}\\t{{{{.Image}}}}\\t{{{{.Ports}}}}\\t{{{{.Status}}}}'"
    try:
        rc, out, e = await _ssh_conn(conn, cmd, 30)
        if rc != 0:
            return f"ERROR: docker ps rc={rc}\n{_clip(e, 500)}", None
        return f"[docker@{conn['host']}] ps:\n{out}", None
    except Exception as ex:
        return f"ERROR: docker_remote_ps {type(ex).__name__}: {ex}", None


@register("docker_remote_inspect")
async def h_docker_remote_inspect(sid: str, args: dict) -> tuple[str, Any]:
    conn, err = _conn(args)
    container = (args.get("container") or "").strip()
    fmt = args.get("format") or ""
    if not conn or not container:
        return f"ERROR: docker_remote_inspect {err or '需要 host + container'}", None
    cmd = f"docker inspect {shlex.quote(container)}" + (f" --format {shlex.quote(fmt)}" if fmt else "")
    try:
        rc, out, e = await _ssh_conn(conn, cmd, 30)
        if rc != 0:
            return f"ERROR: docker inspect rc={rc}\n{_clip(e, 500)}", None
        return f"[docker@{conn['host']}] inspect {container}:\n{_clip(out, 6000)}", None
    except Exception as ex:
        return f"ERROR: docker_remote_inspect {type(ex).__name__}: {ex}", None


def _has_detach(run_flags: str) -> bool:
    try:
        toks = shlex.split(run_flags)
    except ValueError:
        toks = run_flags.split()
    return any(t in ("-d", "--detach") or t.startswith("--detach=") or re.fullmatch(r"-[a-zA-Z]*d[a-zA-Z]*", t)
               for t in toks)


@register("docker_remote_recreate")
async def h_docker_remote_recreate(sid: str, args: dict) -> tuple[str, Any]:
    """rm + run 替换容器. 用于改启动参数(比如 vllm --max-model-len).

    args:
      host/port/user/password/key_file/jump 或 alias
      container: 容器名 (会被 stop+rm)
      image: 镜像 id 或 name:tag (必填)
      run_flags: 传给 docker run 的完整 flag 字符串 (例如 '-d --name X --restart unless-stopped --gpus all -p 8000:8000 -v /a:/b')
      entrypoint: 可选, 覆盖 --entrypoint
      cmd: image 后面的命令行 (args.Args)
      pull: 是否 pull (默认 false)
    """
    conn, err = _conn(args)
    container = (args.get("container") or "").strip()
    image = (args.get("image") or "").strip()
    run_flags = args.get("run_flags") or ""
    entrypoint = args.get("entrypoint") or ""
    cmd = args.get("cmd") or ""
    if not conn or not container or not image:
        return f"ERROR: docker_remote_recreate {err or '需要 host + container + image'}", None
    ep_flag = f"--entrypoint {shlex.quote(entrypoint)} " if entrypoint else ""
    # [2026-10-19] 不带 -d 时 docker run 前台跑服务, 卡满 120s 才超时; 缺就补上。
    if not _has_detach(run_flags):
        run_flags = ("-d " + run_flags).strip()
    # [2026-10-19] 原来每步 `| tail -N` 且无 pipefail, docker run 失败也报 rc=0。
    # 现在 bash -o pipefail 执行; stop/rm 失败 (容器本就不存在) 显式容忍, run 失败则整体 rc≠0。
    steps = [
        f"{{ echo '=== stop {container} ==='; docker stop {shlex.quote(container)} 2>&1 | tail -3 || echo '(stop skipped)'; }}",
        f"{{ echo '=== rm {container} ==='; docker rm {shlex.quote(container)} 2>&1 | tail -3 || echo '(rm skipped)'; }}",
        f"echo '=== run {container} ===' && docker run {run_flags} {ep_flag}{shlex.quote(image)} {cmd} 2>&1 | tail -6",
        f"echo '=== ps ===' && sleep 3 && docker ps --filter name=^{shlex.quote(container)}$ --format 'table {{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}'",
    ]
    script = "set -o pipefail\n" + " && ".join(steps)
    try:
        rc, out, e = await _ssh_conn(conn, "bash -c " + shlex.quote(script), 120)
        text = f"[docker@{conn['host']}] recreate {container} rc={rc}\n{_clip(out, 3000)}\n{_clip(e, 1000)}"
        return (text if rc == 0 else f"ERROR: docker_remote_recreate rc={rc}\n" + text), None
    except HostNotReady as ex:
        return f"ERROR: {ex}", None
    except Exception as ex:
        return f"ERROR: docker_remote_recreate {type(ex).__name__}: {ex}", None
