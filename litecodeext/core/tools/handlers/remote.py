"""handlers/remote.py — ssh_exec + docker_remote_* (基于 paramiko)."""
from __future__ import annotations

import asyncio
import re
import shlex
import time
from typing import Any

from . import register
from .ssh_hosts import resolve_ssh_args

_DEFAULT_TIMEOUT = 60

# 检测命令里是否含后台进程 (nohup ... 或 ... &)
_BG_RE = re.compile(r'(?:(?:^|[;|&{\n]\s*)nohup\s|&\s*(?:#[^\n]*)?\s*$)')


def _wrap_bg(cmd: str) -> str:
    """后台进程命令需关闭 stdin, 否则 SSH channel 永远不会自动关闭导致 recv_exit_status 挂死."""
    if _BG_RE.search(cmd.strip()):
        # 把整条命令包进 { ... } 并重定向 stdin 到 /dev/null
        # 这样 nohup/& 子进程不会继承 SSH channel 的 stdin fd
        return f'{{ {cmd}\n}} </dev/null'
    return cmd


def _run_ssh(host: str, port: int, user: str, password: str, cmd: str, timeout: int) -> tuple[int, str, str]:
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, port=port, username=user, password=password,
                       timeout=15, banner_timeout=15, auth_timeout=15,
                       allow_agent=False, look_for_keys=False)

        actual_cmd = _wrap_bg(cmd)
        stdin, stdout, stderr = client.exec_command(actual_cmd, timeout=timeout, get_pty=False)

        # channel 级别设超时, 防止 recv_exit_status 无限阻塞
        chan = stdout.channel
        chan.settimeout(timeout)

        # 轮询读取输出 + 等待 exit status, 避免先 read() 再 recv_exit_status() 的死锁
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if chan.exit_status_ready():
                break
            if chan.recv_ready():
                data = chan.recv(8192)
                if data:
                    out_chunks.append(data)
            if chan.recv_stderr_ready():
                data = chan.recv_stderr(8192)
                if data:
                    err_chunks.append(data)
            if not chan.recv_ready() and not chan.recv_stderr_ready():
                time.sleep(0.05)

        # 排干剩余输出
        while chan.recv_ready():
            out_chunks.append(chan.recv(8192))
        while chan.recv_stderr_ready():
            err_chunks.append(chan.recv_stderr(8192))

        rc = chan.recv_exit_status() if chan.exit_status_ready() else 0
        out = b"".join(out_chunks).decode("utf-8", errors="replace")
        err = b"".join(err_chunks).decode("utf-8", errors="replace")
        return rc, out, err
    finally:
        try:
            client.close()
        except Exception:
            pass


async def _ssh(host: str, port: int, user: str, password: str, cmd: str, timeout: int) -> tuple[int, str, str]:
    loop = asyncio.get_event_loop()
    # asyncio 层硬性 deadline, 防止 executor 线程永久阻塞
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _run_ssh, host, port, user, password, cmd, timeout),
            timeout=timeout + 10,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"ssh_exec asyncio deadline: {timeout + 10}s exceeded")


@register("ssh_exec")
async def h_ssh_exec(sid: str, args: dict) -> tuple[str, Any]:
    args = resolve_ssh_args(args)
    host = (args.get("host") or "").strip()
    port = int(args.get("port") or 22)
    user = (args.get("user") or "root").strip()
    password = args.get("password") or ""
    cmd = args.get("cmd") or args.get("command") or ""
    timeout = int(args.get("timeout") or _DEFAULT_TIMEOUT)
    if not host or not cmd:
        return "ERROR: ssh_exec 需要 host + cmd", None
    try:
        rc, out, err = await _ssh(host, port, user, password, cmd, timeout)
        head = f"[ssh {user}@{host}:{port}] rc={rc}"
        body = ""
        if out:
            body += f"\n--- stdout ---\n{out[:4000]}" + ("\n...(truncated)" if len(out) > 4000 else "")
        if err:
            body += f"\n--- stderr ---\n{err[:2000]}" + ("\n...(truncated)" if len(err) > 2000 else "")
        if not body:
            body = "\n(no output)"
        return head + body, None
    except Exception as e:
        return f"ERROR: ssh_exec {type(e).__name__}: {e}", None


def _sftp_write(host: str, port: int, user: str, password: str,
                path: str, content: str, backup: bool) -> str:
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, port=port, username=user, password=password,
                       timeout=15, allow_agent=False, look_for_keys=False)
        sftp = client.open_sftp()
        if backup:
            try:
                sftp.rename(path, path + ".bak")
            except Exception:
                pass
        with sftp.open(path, "w") as f:
            f.write(content)
        size = len(content.encode())
        sftp.close()
        return f"OK: wrote {size} bytes → {path}" + (f" (backup: {path}.bak)" if backup else "")
    finally:
        try:
            client.close()
        except Exception:
            pass


@register("ssh_write_file")
async def h_ssh_write_file(sid: str, args: dict) -> tuple[str, Any]:
    args = resolve_ssh_args(args)
    host = (args.get("host") or "").strip()
    port = int(args.get("port") or 22)
    user = (args.get("user") or "root").strip()
    password = args.get("password") or ""
    path = (args.get("path") or "").strip()
    content = args.get("content") or ""
    backup = args.get("backup", True)
    if not host or not path:
        return "ERROR: ssh_write_file 需要 host + path + content", None
    try:
        loop = asyncio.get_event_loop()
        msg = await asyncio.wait_for(
            loop.run_in_executor(None, _sftp_write, host, port, user, password, path, content, backup),
            timeout=30,
        )
        return f"[sftp {user}@{host}:{port}] {msg}", None
    except Exception as e:
        return f"ERROR: ssh_write_file {type(e).__name__}: {e}", None


@register("ssh_sync_files")
async def h_ssh_sync_files(sid: str, args: dict) -> tuple[str, Any]:
    args = resolve_ssh_args(args)
    host = (args.get("host") or "").strip()
    port = int(args.get("port") or 22)
    user = (args.get("user") or "root").strip()
    password = args.get("password") or ""
    files: dict = args.get("files") or {}
    backup = args.get("backup", True)
    if not host or not files:
        return "ERROR: ssh_sync_files 需要 host + files 字典", None
    import paramiko

    def _do_sync():
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        results = []
        try:
            client.connect(hostname=host, port=port, username=user, password=password,
                           timeout=15, allow_agent=False, look_for_keys=False)
            sftp = client.open_sftp()
            for path, content in files.items():
                try:
                    if backup:
                        try:
                            sftp.rename(path, path + ".bak")
                        except Exception:
                            pass
                    with sftp.open(path, "w") as f:
                        f.write(content)
                    results.append(f"  ✓ {path} ({len((content or '').encode())} bytes)")
                except Exception as e:
                    results.append(f"  ✗ {path}: {e}")
            sftp.close()
        finally:
            try:
                client.close()
            except Exception:
                pass
        return results

    try:
        loop = asyncio.get_event_loop()
        results = await asyncio.wait_for(
            loop.run_in_executor(None, _do_sync),
            timeout=60,
        )
        summary = f"[sftp {user}@{host}:{port}] sync {len(files)} files:\n" + "\n".join(results)
        return summary, None
    except Exception as e:
        return f"ERROR: ssh_sync_files {type(e).__name__}: {e}", None


@register("ssh_read_file")
async def h_ssh_read_file(sid: str, args: dict) -> tuple[str, Any]:
    args = resolve_ssh_args(args)
    host = (args.get("host") or "").strip()
    port = int(args.get("port") or 22)
    user = (args.get("user") or "root").strip()
    password = args.get("password") or ""
    path = (args.get("path") or "").strip()
    max_chars = int(args.get("max_chars") or 20000)
    if not host or not path:
        return "ERROR: ssh_read_file 需要 host + path", None
    import paramiko

    def _do_read():
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(hostname=host, port=port, username=user, password=password,
                           timeout=15, allow_agent=False, look_for_keys=False)
            sftp = client.open_sftp()
            with sftp.open(path, "r") as f:
                content = f.read(max_chars * 2)  # bytes, UTF-8 may be multi-byte
            sftp.close()
            return content.decode("utf-8", errors="replace")[:max_chars]
        finally:
            try:
                client.close()
            except Exception:
                pass

    try:
        loop = asyncio.get_event_loop()
        content = await asyncio.wait_for(
            loop.run_in_executor(None, _do_read),
            timeout=30,
        )
        truncated = len(content) >= max_chars
        return (f"[sftp {user}@{host}:{port}] {path} ({len(content)} chars):\n{content}"
                + ("\n...(truncated)" if truncated else "")), None
    except Exception as e:
        return f"ERROR: ssh_read_file {type(e).__name__}: {e}", None


@register("ssh_str_replace")
async def h_ssh_str_replace(sid: str, args: dict) -> tuple[str, Any]:
    args = resolve_ssh_args(args)
    """在远程文件上做 str_replace — 等价于 Claude Code 的 str_replace_based_edit_tool 但走 SSH.
    对大文件特别有用: 只传 old_str/new_str, 不需要传整个文件内容.
    """
    host = (args.get("host") or "").strip()
    port = int(args.get("port") or 22)
    user = (args.get("user") or "root").strip()
    password = args.get("password") or ""
    path = (args.get("path") or "").strip()
    old_str = args.get("old_str") or args.get("old_string") or ""
    new_str = str(args.get("new_str") if args.get("new_str") is not None else (args.get("new_string") or ""))
    if not host or not path or not old_str:
        return "ERROR: ssh_str_replace 需要 host + path + old_str", None

    import json as _json

    def _do_replace():
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(hostname=host, port=port, username=user, password=password,
                           timeout=15, allow_agent=False, look_for_keys=False)
            sftp = client.open_sftp()
            with sftp.open(path, "r") as f:
                content = f.read().decode("utf-8", errors="replace")
            count = content.count(old_str)
            if count == 0:
                return False, "NOT_FOUND: old_str not found in file"
            if count > 1:
                return False, f"AMBIGUOUS: old_str matches {count} times; make it more specific"
            new_content = content.replace(old_str, new_str, 1)
            # backup
            try:
                sftp.rename(path, path + ".bak")
            except Exception:
                pass
            with sftp.open(path, "w") as f:
                f.write(new_content)
            sftp.close()
            return True, f"OK: replaced 1 occurrence in {path} ({len(content)}→{len(new_content)} chars)"
        finally:
            try:
                client.close()
            except Exception:
                pass

    try:
        loop = asyncio.get_event_loop()
        ok, msg = await asyncio.wait_for(
            loop.run_in_executor(None, _do_replace),
            timeout=30,
        )
        prefix = f"[sftp {user}@{host}:{port}]"
        if ok:
            return f"{prefix} {msg}", None
        else:
            return f"ERROR: ssh_str_replace {msg}", None
    except Exception as e:
        return f"ERROR: ssh_str_replace {type(e).__name__}: {e}", None


@register("docker_remote_ps")
async def h_docker_remote_ps(sid: str, args: dict) -> tuple[str, Any]:
    host = (args.get("host") or "").strip()
    port = int(args.get("port") or 22)
    user = (args.get("user") or "root").strip()
    password = args.get("password") or ""
    all_flag = "-a" if args.get("all") else ""
    if not host:
        return "ERROR: docker_remote_ps 需要 host", None
    cmd = f"docker ps {all_flag} --format 'table {{{{.Names}}}}\\t{{{{.Image}}}}\\t{{{{.Ports}}}}\\t{{{{.Status}}}}'"
    try:
        rc, out, err = await _ssh(host, port, user, password, cmd, 30)
        if rc != 0:
            return f"ERROR: docker ps rc={rc}\n{err[:500]}", None
        return f"[docker@{host}] ps:\n{out}", None
    except Exception as e:
        return f"ERROR: docker_remote_ps {type(e).__name__}: {e}", None


@register("docker_remote_inspect")
async def h_docker_remote_inspect(sid: str, args: dict) -> tuple[str, Any]:
    host = (args.get("host") or "").strip()
    port = int(args.get("port") or 22)
    user = (args.get("user") or "root").strip()
    password = args.get("password") or ""
    container = (args.get("container") or "").strip()
    fmt = args.get("format") or ""
    if not host or not container:
        return "ERROR: docker_remote_inspect 需要 host + container", None
    if fmt:
        cmd = f"docker inspect {shlex.quote(container)} --format {shlex.quote(fmt)}"
    else:
        cmd = f"docker inspect {shlex.quote(container)}"
    try:
        rc, out, err = await _ssh(host, port, user, password, cmd, 30)
        if rc != 0:
            return f"ERROR: docker inspect rc={rc}\n{err[:500]}", None
        return f"[docker@{host}] inspect {container}:\n{out[:6000]}" + ("\n...(truncated)" if len(out) > 6000 else ""), None
    except Exception as e:
        return f"ERROR: docker_remote_inspect {type(e).__name__}: {e}", None


@register("docker_remote_recreate")
async def h_docker_remote_recreate(sid: str, args: dict) -> tuple[str, Any]:
    """rm + run 替换容器. 用于改启动参数(比如 vllm --max-model-len).

    args:
      host, port, user, password
      container: 容器名 (会被 stop+rm)
      image: 镜像 id 或 name:tag (必填)
      run_flags: 传给 docker run 的完整 flag 字符串 (例如 '-d --name X --restart unless-stopped --gpus all -p 8000:8000 -v /a:/b')
      entrypoint: 可选, 覆盖 --entrypoint
      cmd: image 后面的命令行 (args.Args)
      pull: 是否 pull (默认 false)
    """
    host = (args.get("host") or "").strip()
    port = int(args.get("port") or 22)
    user = (args.get("user") or "root").strip()
    password = args.get("password") or ""
    container = (args.get("container") or "").strip()
    image = (args.get("image") or "").strip()
    run_flags = args.get("run_flags") or ""
    entrypoint = args.get("entrypoint") or ""
    cmd = args.get("cmd") or ""
    if not host or not container or not image:
        return "ERROR: docker_remote_recreate 需要 host + container + image", None
    ep_flag = f"--entrypoint {shlex.quote(entrypoint)} " if entrypoint else ""
    steps = [
        f"echo '=== stop {container} ==='; docker stop {shlex.quote(container)} 2>&1 | tail -3",
        f"echo '=== rm {container} ==='; docker rm {shlex.quote(container)} 2>&1 | tail -3",
        f"echo '=== run {container} ==='; docker run {run_flags} {ep_flag}{shlex.quote(image)} {cmd} 2>&1 | tail -6",
        f"echo '=== ps ==='; sleep 3; docker ps --filter name=^{shlex.quote(container)}$ --format 'table {{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}'",
    ]
    full = " && ".join(steps)
    try:
        rc, out, err = await _ssh(host, port, user, password, full, 120)
        return f"[docker@{host}] recreate {container} rc={rc}\n{out[:3000]}\n{err[:1000]}", None
    except Exception as e:
        return f"ERROR: docker_remote_recreate {type(e).__name__}: {e}", None
