"""handlers/remote.py — ssh_exec + docker_remote_* (基于 paramiko)."""
from __future__ import annotations

import asyncio
import os
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
             key_file: str = "", jump: dict | None = None) -> tuple[int, str, str]:
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    jc = None
    try:
        sock = None
        if jump:
            jc, sock = _open_jump(jump, host, port)
        client.connect(hostname=host, port=port, username=user, **_auth_kwargs(password, key_file),
                       sock=sock, timeout=15, banner_timeout=15, auth_timeout=15,
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
        for c in (client, jc):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass


async def _ssh(host: str, port: int, user: str, password: str, cmd: str, timeout: int,
               key_file: str = "", jump: dict | None = None) -> tuple[int, str, str]:
    loop = asyncio.get_event_loop()
    # asyncio 层硬性 deadline, 防止 executor 线程永久阻塞
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _run_ssh, host, port, user, password, cmd, timeout, key_file, jump),
            timeout=timeout + 10,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(f"ssh_exec asyncio deadline: {timeout + 10}s exceeded")


@register("ssh_exec")
async def h_ssh_exec(sid: str, args: dict) -> tuple[str, Any]:
    conn, err = _conn(args)
    cmd = args.get("cmd") or args.get("command") or ""
    timeout = int(args.get("timeout") or _DEFAULT_TIMEOUT)
    if not conn or not cmd:
        return f"ERROR: ssh_exec {err or '需要 host + cmd'}", None
    try:
        rc, out, e = await _ssh(conn["host"], conn["port"], conn["user"], conn["password"], cmd, timeout,
                                conn["key_file"], conn["jump"])
        head = f"[ssh {conn['user']}@{conn['host']}:{conn['port']}] rc={rc}"
        body = ""
        if out:
            body += f"\n--- stdout ---\n{out[:4000]}" + ("\n...(truncated)" if len(out) > 4000 else "")
        if e:
            body += f"\n--- stderr ---\n{e[:2000]}" + ("\n...(truncated)" if len(e) > 2000 else "")
        return head + (body or "\n(no output)"), None
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
    """按 conn 连上目标 (可经跳板), 返回 (client, jump_client)。用完调 _close。"""
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


def _sftp_write(conn: dict, path: str, content: str, backup: bool) -> str:
    client, jc = _connect(conn)
    try:
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
        _close(client, jc)


def _tag(conn: dict) -> str:
    return f"{conn['user']}@{conn['host']}:{conn['port']}" + (f" via {conn['jump']['host']}" if conn["jump"] else "")


async def _in_thread(fn, timeout: int):
    return await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, fn), timeout=timeout)


@register("ssh_write_file")
async def h_ssh_write_file(sid: str, args: dict) -> tuple[str, Any]:
    conn, err = _conn(args)
    path = (args.get("path") or "").strip()
    if not conn or not path:
        return f"ERROR: ssh_write_file {err or '需要 host + path + content'}", None
    try:
        msg = await _in_thread(lambda: _sftp_write(conn, path, args.get("content") or "", args.get("backup", True)), 30)
        return f"[sftp {_tag(conn)}] {msg}", None
    except Exception as e:
        return f"ERROR: ssh_write_file {type(e).__name__}: {e}", None


@register("ssh_sync_files")
async def h_ssh_sync_files(sid: str, args: dict) -> tuple[str, Any]:
    conn, err = _conn(args)
    files: dict = args.get("files") or {}
    backup = args.get("backup", True)
    if not conn or not files:
        return f"ERROR: ssh_sync_files {err or '需要 host + files 字典'}", None

    def _do_sync():
        client, jc = _connect(conn)
        results = []
        try:
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
            _close(client, jc)
        return results

    try:
        results = await _in_thread(_do_sync, 60)
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
        client, jc = _connect(conn)
        try:
            sftp = client.open_sftp()
            with sftp.open(path, "r") as f:
                content = f.read(max_chars * 2)  # bytes, UTF-8 may be multi-byte
            sftp.close()
            return content.decode("utf-8", errors="replace")[:max_chars]
        finally:
            _close(client, jc)

    try:
        content = await _in_thread(_do_read, 30)
        truncated = len(content) >= max_chars
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
            try:
                sftp.rename(path, path + ".bak")        # backup
            except Exception:
                pass
            with sftp.open(path, "w") as f:
                f.write(new_content)
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
            return f"ERROR: docker ps rc={rc}\n{e[:500]}", None
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
            return f"ERROR: docker inspect rc={rc}\n{e[:500]}", None
        return f"[docker@{conn['host']}] inspect {container}:\n{out[:6000]}" + ("\n...(truncated)" if len(out) > 6000 else ""), None
    except Exception as ex:
        return f"ERROR: docker_remote_inspect {type(ex).__name__}: {ex}", None


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
    steps = [
        f"echo '=== stop {container} ==='; docker stop {shlex.quote(container)} 2>&1 | tail -3",
        f"echo '=== rm {container} ==='; docker rm {shlex.quote(container)} 2>&1 | tail -3",
        f"echo '=== run {container} ==='; docker run {run_flags} {ep_flag}{shlex.quote(image)} {cmd} 2>&1 | tail -6",
        f"echo '=== ps ==='; sleep 3; docker ps --filter name=^{shlex.quote(container)}$ --format 'table {{{{.Names}}}}\\t{{{{.Status}}}}\\t{{{{.Ports}}}}'",
    ]
    try:
        rc, out, e = await _ssh_conn(conn, " && ".join(steps), 120)
        return f"[docker@{conn['host']}] recreate {container} rc={rc}\n{out[:3000]}\n{e[:1000]}", None
    except Exception as ex:
        return f"ERROR: docker_remote_recreate {type(ex).__name__}: {ex}", None
