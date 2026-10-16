#!/usr/bin/env python3
"""在 SWE-bench 题目容器里执行命令 (给 LiteCode agent 用, 运行在 litecode 容器内)。

litecode 容器挂了 /var/run/docker.sock 但没装 docker CLI, 这里直接走 Docker Engine API。
命令在题目容器的 /testbed 下、激活 testbed conda 环境后执行, 输出原样打印, 退出码透传。

用法: python3 swe_dexec.py <容器名> "<shell 命令>" [超时秒数, 默认 600]
"""
import http.client
import json
import socket
import sys

SOCK = "/var/run/docker.sock"
PREAMBLE = "source /opt/miniconda3/bin/activate testbed 2>/dev/null || true; cd /testbed; "
MAX_OUT = 20000


class _Conn(http.client.HTTPConnection):
    def __init__(self, timeout):
        super().__init__("localhost", timeout=timeout)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCK)


def _req(method, path, body=None, timeout=30):
    c = _Conn(timeout)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Content-Type": "application/json"})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def run(container: str, cmd: str, timeout: int = 600) -> int:
    st, data = _req("POST", f"/containers/{container}/exec", {
        "AttachStdout": True, "AttachStderr": True, "Tty": True,
        "Cmd": ["bash", "-c", PREAMBLE + f"timeout {timeout} bash -c {json.dumps(cmd)}"],
    })
    if st != 201:
        print(f"[dexec] 创建 exec 失败 HTTP {st}: {data[:300]!r}")
        return 125
    eid = json.loads(data)["Id"]
    try:
        st, out = _req("POST", f"/exec/{eid}/start", {"Detach": False, "Tty": True}, timeout=timeout + 30)
    except socket.timeout:
        print(f"[dexec] 超过 {timeout}s 未结束")
        return 124
    text = out.decode("utf-8", "replace")
    if len(text) > MAX_OUT:
        text = text[:MAX_OUT // 2] + f"\n...[省略 {len(text) - MAX_OUT} 字符]...\n" + text[-MAX_OUT // 2:]
    print(text, end="" if text.endswith("\n") else "\n")
    _, info = _req("GET", f"/exec/{eid}/json")
    return int(json.loads(info).get("ExitCode") or 0)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(run(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 600))
