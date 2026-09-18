# plugins/tools/docker_ops.py — Docker 管理 plugin (#44)
#
# 直接讲 HTTP-over-Unix-socket 到 host 的 /var/run/docker.sock,
# 靠 docker-compose 的 volume mount 暴露 (见 docker-compose.yml).
#
# 覆盖: ps / logs / stats / restart / stop / start / inspect.
#
# 契约: 单模块暴露 TOOLS = [...] (给 LLM 用),
# 同时导出 async helper (docker_ps / docker_logs / ...) 给 router + CLI 复用.
# 不需要 core.tool_dispatch 代理, 全部自成一体.

from __future__ import annotations
import logging
import os

import httpx

log = logging.getLogger("plugins.docker_ops")

DOCKER_SOCK = "/var/run/docker.sock"
DEFAULT_TIMEOUT = 10.0

# 破坏性操作白名单前缀: 只允许操作以这些前缀开头的容器,
# 防止 LLM/UI 一脚踢到宿主机 db/网关等无关容器.
# 环境变量 LITECODE_DOCKER_SCOPE="litecode,searxng" 可覆盖.
_SCOPE_RAW = os.environ.get("LITECODE_DOCKER_SCOPE", "litecode,searxng,pptx,cad")
ALLOWED_PREFIXES = tuple(x.strip() for x in _SCOPE_RAW.split(",") if x.strip())


def _client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCK)
    return httpx.AsyncClient(transport=transport, base_url="http://d", timeout=timeout)


def _in_scope(name_or_id: str, container_names: list[str]) -> bool:
    """判断是否允许操作该容器 (按名字前缀白名单)."""
    if not ALLOWED_PREFIXES:
        return True
    for n in container_names + [name_or_id]:
        n = n.lstrip("/")
        if any(n == p or n.startswith(p) for p in ALLOWED_PREFIXES):
            return True
    return False


async def docker_ps(all: bool = False) -> dict:
    """列容器. all=True 含 stopped."""
    try:
        async with _client() as c:
            r = await c.get("/containers/json", params={"all": "true" if all else "false"})
            r.raise_for_status()
            raw = r.json()
        items = []
        for it in raw:
            names = it.get("Names") or []
            ports = it.get("Ports") or []
            port_str = ", ".join(
                f"{p.get('IP','')}:{p['PublicPort']}->{p['PrivatePort']}/{p['Type']}"
                if p.get("PublicPort") else f"{p['PrivatePort']}/{p['Type']}"
                for p in ports if p.get("PrivatePort")
            )
            items.append({
                "id": (it.get("Id") or "")[:12],
                "name": (names[0] if names else "").lstrip("/"),
                "image": it.get("Image", ""),
                "state": it.get("State", ""),
                "status": it.get("Status", ""),
                "ports": port_str,
                "in_scope": _in_scope((names[0] if names else "").lstrip("/"), names),
            })
        return {"ok": True, "count": len(items), "containers": items}
    except Exception as e:
        log.exception("docker_ps failed")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def docker_logs(name: str, tail: int = 100) -> dict:
    """取容器日志尾部 (stdout+stderr 合并). tail 上限 1000."""
    tail = max(1, min(int(tail or 100), 1000))
    try:
        async with _client(timeout=15.0) as c:
            r = await c.get(f"/containers/{name}/logs", params={
                "stdout": "true", "stderr": "true", "tail": str(tail), "timestamps": "false",
            })
            r.raise_for_status()
            # Docker 日志流带 8-byte header per frame (stream_type + 4 zeros + size);
            # 简化: 剥掉这些 header, 剩下就是文本.
            raw = r.content
        out = []
        i = 0
        while i < len(raw):
            if len(raw) - i < 8:
                out.append(raw[i:])
                break
            hdr = raw[i:i+8]
            if hdr[0] in (0, 1, 2) and hdr[1:4] == b"\x00\x00\x00":
                size = int.from_bytes(hdr[4:8], "big")
                out.append(raw[i+8:i+8+size])
                i += 8 + size
            else:
                out.append(raw[i:])
                break
        text = b"".join(out).decode("utf-8", errors="replace")
        return {"ok": True, "name": name, "tail": tail, "text": text, "bytes": len(text)}
    except Exception as e:
        log.exception("docker_logs %s failed", name)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def docker_stats() -> dict:
    """单次抓所有容器的 CPU/内存快照 (走 stream=false)."""
    try:
        ps = await docker_ps(all=False)
        if not ps.get("ok"):
            return ps
        rows = []
        async with _client(timeout=8.0) as c:
            for it in ps["containers"]:
                cid = it["id"]
                try:
                    r = await c.get(f"/containers/{cid}/stats", params={"stream": "false", "one-shot": "true"})
                    r.raise_for_status()
                    s = r.json()
                    cpu = _calc_cpu_pct(s)
                    mem_used = (s.get("memory_stats") or {}).get("usage") or 0
                    mem_limit = (s.get("memory_stats") or {}).get("limit") or 0
                    rows.append({
                        "name": it["name"],
                        "id": cid,
                        "cpu_pct": round(cpu, 2),
                        "mem_mb": round(mem_used / 1024 / 1024, 1),
                        "mem_limit_mb": round(mem_limit / 1024 / 1024, 1),
                        "mem_pct": round(mem_used / mem_limit * 100, 1) if mem_limit else 0.0,
                    })
                except Exception as e:
                    rows.append({"name": it["name"], "id": cid, "error": str(e)})
        return {"ok": True, "count": len(rows), "stats": rows}
    except Exception as e:
        log.exception("docker_stats failed")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _calc_cpu_pct(s: dict) -> float:
    """Docker stats CPU 换算 (跟 docker stats 一致的公式)."""
    try:
        cpu = s.get("cpu_stats") or {}
        pre = s.get("precpu_stats") or {}
        cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (pre.get("cpu_usage") or {}).get("total_usage", 0)
        sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        online = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
        if cpu_delta > 0 and sys_delta > 0:
            return (cpu_delta / sys_delta) * online * 100.0
    except Exception:
        pass
    return 0.0


async def _lifecycle(name: str, action: str) -> dict:
    """restart / start / stop 统一入口, 走前缀白名单."""
    if action not in ("restart", "start", "stop"):
        return {"ok": False, "error": f"unknown action: {action}"}
    ps = await docker_ps(all=True)
    if not ps.get("ok"):
        return ps
    match = next((c for c in ps["containers"] if c["name"] == name or c["id"] == name[:12]), None)
    if not match:
        return {"ok": False, "error": f"container not found: {name}"}
    if not match.get("in_scope"):
        return {"ok": False, "error": f"container '{name}' 不在 LITECODE_DOCKER_SCOPE 白名单 ({','.join(ALLOWED_PREFIXES)}) 内, 拒绝操作"}
    try:
        async with _client(timeout=30.0) as c:
            r = await c.post(f"/containers/{name}/{action}", params={"t": "10"})
            if r.status_code not in (204, 304):
                return {"ok": False, "error": f"docker {action} {name} → {r.status_code} {r.text[:200]}"}
        return {"ok": True, "action": action, "name": name}
    except Exception as e:
        log.exception("docker_%s %s failed", action, name)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def docker_restart(name: str) -> dict:
    return await _lifecycle(name, "restart")


async def docker_start(name: str) -> dict:
    return await _lifecycle(name, "start")


async def docker_stop(name: str) -> dict:
    return await _lifecycle(name, "stop")


async def docker_inspect(name: str) -> dict:
    """摘取常用字段 (state / created / mounts / env keys / ports)."""
    try:
        async with _client() as c:
            r = await c.get(f"/containers/{name}/json")
            if r.status_code == 404:
                return {"ok": False, "error": f"container not found: {name}"}
            r.raise_for_status()
            j = r.json()
        state = j.get("State") or {}
        cfg = j.get("Config") or {}
        env = cfg.get("Env") or []
        env_keys = sorted({e.split("=", 1)[0] for e in env if isinstance(e, str) and "=" in e})
        mounts = [{"src": m.get("Source"), "dst": m.get("Destination"), "mode": m.get("Mode")}
                  for m in (j.get("Mounts") or [])]
        nsettings = j.get("NetworkSettings") or {}
        ports = nsettings.get("Ports") or {}
        return {"ok": True, "inspect": {
            "id": (j.get("Id") or "")[:12],
            "name": (j.get("Name") or "").lstrip("/"),
            "image": cfg.get("Image", ""),
            "state": state.get("Status"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "restart_count": j.get("RestartCount", 0),
            "env_keys": env_keys,
            "mounts": mounts,
            "ports": ports,
            "cmd": cfg.get("Cmd") or [],
            "healthcheck": (cfg.get("Healthcheck") or {}).get("Test"),
        }}
    except Exception as e:
        log.exception("docker_inspect %s failed", name)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ─── plugin ABI (给 registry + LLM 用) ──────────────────────
# 破坏性 ops (restart/stop/start) 走 capabilities.safe=False, 前端会二次确认.

async def _plugin_run(name: str, args: dict, ctx: dict) -> dict:
    if name == "docker_ps":
        return await docker_ps(bool(args.get("all", False)))
    if name == "docker_logs":
        return await docker_logs(str(args.get("name", "")), int(args.get("tail", 100)))
    if name == "docker_stats":
        return await docker_stats()
    if name == "docker_inspect":
        return await docker_inspect(str(args.get("name", "")))
    if name == "docker_restart":
        return await docker_restart(str(args.get("name", "")))
    if name == "docker_stop":
        return await docker_stop(str(args.get("name", "")))
    if name == "docker_start":
        return await docker_start(str(args.get("name", "")))
    return {"ok": False, "error": f"unknown docker op: {name}"}


TOOLS = [
    {
        "name": "docker_ps",
        "description": "列出宿主机 Docker 容器 (name/image/state/status/ports). all=true 含已停止.",
        "schema": {"type": "object", "properties": {"all": {"type": "boolean"}}},
        "capabilities": {"safe": True},
        "run": lambda a, c: _plugin_run("docker_ps", a, c),
    },
    {
        "name": "docker_logs",
        "description": "取容器日志尾部. name=容器名, tail=行数 (默认 100, 上限 1000).",
        "schema": {"type": "object", "properties": {
            "name": {"type": "string"}, "tail": {"type": "integer"}}, "required": ["name"]},
        "capabilities": {"safe": True},
        "run": lambda a, c: _plugin_run("docker_logs", a, c),
    },
    {
        "name": "docker_stats",
        "description": "一次性抓所有容器 CPU/内存 (等价 `docker stats --no-stream`).",
        "schema": {"type": "object", "properties": {}},
        "capabilities": {"safe": True},
        "run": lambda a, c: _plugin_run("docker_stats", a, c),
    },
    {
        "name": "docker_inspect",
        "description": "查看容器详细状态 (state/env keys/mounts/ports/cmd/healthcheck).",
        "schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "capabilities": {"safe": True},
        "run": lambda a, c: _plugin_run("docker_inspect", a, c),
    },
    {
        "name": "docker_restart",
        "description": "重启容器 (受 LITECODE_DOCKER_SCOPE 前缀白名单限制, 默认 litecode/searxng/pptx/cad).",
        "schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "capabilities": {"safe": False},
        "run": lambda a, c: _plugin_run("docker_restart", a, c),
    },
    {
        "name": "docker_stop",
        "description": "停止容器 (受白名单限制).",
        "schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "capabilities": {"safe": False},
        "run": lambda a, c: _plugin_run("docker_stop", a, c),
    },
    {
        "name": "docker_start",
        "description": "启动已停止的容器 (受白名单限制).",
        "schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "capabilities": {"safe": False},
        "run": lambda a, c: _plugin_run("docker_start", a, c),
    },
]

VERSION = "0.1.0"
