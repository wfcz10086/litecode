#!/usr/bin/env python3
"""comfy.py — 通用 ComfyUI 客户端 (生图 / 生视频技能共用, 单文件, 只用标准库)。

需要一个 ComfyUI 地址 (host:port, 用户给的); 省略地址 = 用上次记住的 ($WORKSPACE/comfyui_endpoints.json)。

  comfy.py probe     [url] [--refresh] [--json]      探索: 版本/显卡/队列/可用模型/已保存工作流/模板能否在这台跑
  comfy.py templates [--kind image|video]            技能自带模板
  comfy.py workflows [url] [名字.json]               服务器已保存的工作流 (给名字就转成 API 格式并列节点)
  comfy.py run       [url] -w 模板名|api.json|saved:名字 [--prompt ..] [--seed/--steps/--width/--height/--frames ..]
                     [--input 槽位=文件或URL ...] [--set 节点.字段=值 ...] [--drop 节点] [-o 目录] [--dry-run]
  comfy.py wait      url prompt_id                   超时后接着等并取回产物
  comfy.py endpoints | remember url [--via 跳板] [--wait-idle yes|no] [--note ..] | forget url

线上机保护: 提交前默认等 /queue 为空; 永不调用 /free /interrupt; 超时只报告 prompt_id。
内网地址: --via-ssh <跳板别名> 自动起 ssh -L 端口转发 (成功后记住该地址的跳板), 也可设 COMFY_SSH_ROUTES="10.0.0.*=别名"。
"""
from __future__ import annotations

import argparse
import copy
import difflib
import fnmatch
import hashlib
import json
import mimetypes
import os
import random
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARAM_KEYS = ("prompt", "negative", "seed", "steps", "cfg", "width", "height", "frames", "fps", "sampler",
              "scheduler", "denoise")
MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft", ".onnx")
URL_LOADER = re.compile(r"FromURL$", re.I)
FILE_LOADERS = {"LoadImage": "image", "LoadImageMask": "image", "LoadVideo": "file", "LoadAudio": "audio"}
CONTROL_VALUES = {"fixed", "increment", "decrement", "randomize"}


class ComfyError(RuntimeError):
    pass


def log(*a):
    print(*a, flush=True)


def workspace() -> Path:
    return Path(os.environ.get("WORKSPACE") or "/tmp/litecode_workspace")


def norm_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return url if "://" in url else "http://" + url


def host_port(url: str) -> tuple[str, int]:
    u = urllib.parse.urlparse(norm_url(url))
    return u.hostname or "127.0.0.1", u.port or (443 if u.scheme == "https" else 80)


def cache_dir(url: str) -> Path:
    h, p = host_port(url)
    d = workspace() / "comfy_cache" / f"{h}_{p}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tcp_ok(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ================= 记住的地址 =================

def store_path() -> Path:
    return Path(os.environ.get("COMFY_ENDPOINTS_FILE") or workspace() / "comfyui_endpoints.json")


def store_all() -> dict:
    try:
        d = json.loads(store_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def store_save(d: dict):
    store_path().parent.mkdir(parents=True, exist_ok=True)
    store_path().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def store_get(url: str) -> dict:
    return store_all().get(norm_url(url), {})


def sibling_via(host: str) -> str | None:
    """同一 /24 网段里已记住的跳板 (2026-10-22: 166 记住了跳板, 165 还得先失败一次、模型再去翻主机库)。"""
    net = host.rsplit(".", 1)[0] + "." if host.count(".") == 3 else None
    if not net:
        return None
    for rec in sorted(store_all().values(), key=lambda r: r.get("last_used", ""), reverse=True):
        h = host_port(rec.get("url", ""))[0] if rec.get("url") else ""
        if rec.get("via") and h.startswith(net) and h != host:
            return rec["via"]
    return None


def store_update(url: str, touch: bool = True, **fields) -> dict:
    d = store_all()
    key = norm_url(url)
    rec = d.setdefault(key, {"url": key, "wait_idle": True})
    rec.update({k: v for k, v in fields.items() if v is not None})
    if touch:
        rec["last_used"] = time.strftime("%Y-%m-%d %H:%M:%S")
    store_save(d)
    return rec


def resolve_url(arg: str | None) -> str:
    if arg and arg != "last":
        return norm_url(arg)
    recs = sorted(store_all().values(), key=lambda r: r.get("last_used", ""), reverse=True)
    if not recs:
        raise ComfyError("需要一个 ComfyUI 地址 (host:port), 之前也没记住过任何地址 —— 请先问用户")
    log(f"[mem] 没给地址, 用上次的 {recs[0]['url']}")
    return recs[0]["url"]


def describe(rec: dict) -> str:
    s = rec["url"] + (f"  via ssh {rec['via']}" if rec.get("via") else "")
    s += "  [线上机: 等队列空再提交]" if rec.get("wait_idle", True) else "  [独占: 不等队列]"
    s += f"\n  最近使用 {rec.get('last_used', '-')}" + (f" · 备注 {rec['note']}" if rec.get("note") else "")
    p = rec.get("probe") or {}
    if p:
        s += (f"\n  ComfyUI {p.get('version')} · {', '.join(p.get('gpus') or [])}"
              f"\n  能跑的模板: {', '.join(p.get('templates_ok') or []) or '无'}")
    if rec.get("last_ok"):
        lo = rec["last_ok"]
        s += f"\n  上次成功: {lo['workflow']} {lo['at']} 用时 {lo['wall_s']}s " + json.dumps(lo.get("params"), ensure_ascii=False)[:150]
    return s


# ================= SSH 跳板 =================

def ssh_config_entry(alias: str, cfg_path: Path) -> dict:
    """从 ssh config 取 alias 的 HostName/Port/User/IdentityFile/ProxyJump (简单解析, 不支持 Match/Include)。"""
    out, active = {}, False
    try:
        lines = cfg_path.read_text(errors="replace").splitlines()
    except OSError:
        return out
    for raw in lines:
        parts = raw.strip().split(None, 1)
        if not parts or parts[0].startswith("#"):
            continue
        key, val = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
        if key == "host":
            active = any(fnmatch.fnmatch(alias, pat) for pat in val.split())
        elif active and key in ("hostname", "port", "user", "identityfile", "proxyjump"):
            out.setdefault(key, val)
    return out


def default_ssh_config() -> Path:
    """OpenSSH 读的是 passwd 里的家目录 (不看 $HOME); COMFY_SSH_CONFIG 可指定别的 config。"""
    if os.environ.get("COMFY_SSH_CONFIG"):
        return Path(os.environ["COMFY_SSH_CONFIG"])
    try:
        import pwd
        return Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".ssh" / "config"
    except (ImportError, KeyError):
        return Path.home() / ".ssh" / "config"


def ssh_cmd(alias: str, cfg_path: Path | None = None) -> list[str]:
    """跳板的 ssh 参数。alias 可以是 ssh config 里的 Host 别名, 也可以直接写 user@host:port (密钥登录)。
    config 属主不是当前用户时 (容器里 root 跑 + bind-mount 的宿主目录) ssh 会报 Bad owner,
    这时自己解析 config, 用 -F /dev/null + 显式参数。"""
    cfg = cfg_path or default_ssh_config()
    opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30"]
    m = re.fullmatch(r"([^@\s]+)@([^:@\s]+)(?::(\d+))?|([^:@\s]+):(\d+)", alias)
    if m:                                    # 直接给了 user@host[:port] / host:port
        user, host, port = (m.group(1), m.group(2), m.group(3)) if m.group(2) else (None, m.group(4), m.group(5))
        return ["ssh", "-F", "/dev/null", *opts, *(["-p", port] if port else []), f"{user}@{host}" if user else host]
    try:
        st = cfg.stat()
        unusable = st.st_uid != os.geteuid() or bool(st.st_mode & 0o022)
    except OSError:
        unusable = False
    if not unusable:
        return ["ssh", *opts, alias] if cfg == default_ssh_config() and not os.environ.get("COMFY_SSH_CONFIG") \
            else ["ssh", "-F", str(cfg), *opts, alias]
    e = ssh_config_entry(alias, cfg)
    cmd = ["ssh", "-F", "/dev/null", *opts]
    cmd += ["-p", e["port"]] if e.get("port") else []
    cmd += ["-i", os.path.expanduser(e["identityfile"]), "-o", "IdentitiesOnly=yes"] if e.get("identityfile") else []
    cmd += ["-J", e["proxyjump"]] if e.get("proxyjump") else []
    host = e.get("hostname", alias)
    return cmd + [f"{e['user']}@{host}" if e.get("user") else host]


def env_route(url: str) -> str | None:
    h, p = host_port(url)
    for item in os.environ.get("COMFY_SSH_ROUTES", "").replace(";", ",").split(","):
        if "=" in item:
            pat, alias = (x.strip() for x in item.split("=", 1))
            if fnmatch.fnmatch(f"{h}:{p}", pat) or fnmatch.fnmatch(h, pat):
                return alias or None
    return None


def open_tunnel(url: str, alias: str) -> int:
    """ssh -L 127.0.0.1:<端口> → url。端口由 (跳板, 目标) 算出, 已经开着就直接复用 (ssh 进程常驻后台)。"""
    h, p = host_port(url)
    port = 20000 + int(hashlib.md5(f"{alias}>{h}:{p}".encode()).hexdigest(), 16) % 20000
    if tcp_ok("127.0.0.1", port, 1):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=10).read()
            return port
        except Exception:
            with socket.socket() as s:            # 端口被别的程序占了: 换个随机端口
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
    base = ssh_cmd(alias)
    cmd = base[:-1] + ["-N", "-o", "ExitOnForwardFailure=yes", "-L", f"127.0.0.1:{port}:{h}:{p}", base[-1]]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True)
    for _ in range(60):
        if proc.poll() is not None:
            err = proc.stderr.read().decode(errors="replace")[-300:]
            hint = (f" —— {alias!r} 不是 ssh config 里的 Host 别名; LiteCode 主机库 (ssh_list_hosts) 里的别名这里用不了, "
                    f"请改写成 --via-ssh user@host:port (需密钥登录)") if "resolve hostname" in err else ""
            raise ComfyError(f"SSH 端口转发失败 ({' '.join(cmd)}): {err}{hint}")
        if tcp_ok("127.0.0.1", port, 1):
            return port
        time.sleep(0.3)
    proc.kill()
    raise ComfyError(f"SSH 端口转发 20 秒没起来: {' '.join(cmd)}")


# ================= HTTP =================

class Comfy:
    """base = 服务器自己认识的地址 (喂给 *FromURL 节点、给人看的链接); api = 本机实际访问的地址 (可能是隧道)。"""

    def __init__(self, url: str, via: str | None = None, self_url: str | None = None):
        self.base = self.api = norm_url(url)
        self.route = None
        # 服务器自己访问自己的地址: 经端口转发 / 反向代理访问时, base 是本机这一侧的地址, 服务器上的 *FromURL
        # 节点访问不到 (2026-10-23 H3 参考音频报 Connection refused)。--self-url / remember --self-url / COMFY_SELF_URL
        self.self_url = norm_url(self_url or store_get(self.base).get("self_url") or os.environ.get("COMFY_SELF_URL") or self.base)
        h, p = host_port(self.base)
        if via in ("none", "no"):
            return
        if not via:
            if tcp_ok(h, p, 3):
                return
            via = store_get(self.base).get("via") or env_route(self.base) or sibling_via(h)
            if via and not store_get(self.base).get("via"):
                log(f"[net] {self.base} 直连不通, 试用同网段已记住的跳板 {via}")
            if not via:
                raise ComfyError(f"本机连不上 {self.base} (3 秒超时)。若它在内网, 需要能访问它的 SSH 跳板: "
                                 f"加 --via-ssh <跳板别名> (成功后会记住); 否则请用户确认地址/端口")
        self.api = f"http://127.0.0.1:{open_tunnel(self.base, via)}"
        self.route = via
        log(f"[net] {self.base} 经 ssh {via} 转发 → {self.api}")

    def req(self, method: str, path: str, body=None, headers=None, timeout: float = 60) -> bytes:
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        hdrs = {"User-Agent": "litecode-comfy/1.0", **(headers or {})}
        if data is not None and not isinstance(body, bytes):
            hdrs["Content-Type"] = "application/json"
        try:
            with urllib.request.urlopen(urllib.request.Request(self.api + path, data, hdrs, method=method),
                                        timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise ComfyError(f"{method} {path} → HTTP {e.code}: {e.read().decode(errors='replace')[:3000]}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ComfyError(f"{method} {path} 失败: {e}") from None

    def get(self, path: str, timeout: float = 60):
        return json.loads(self.req("GET", path, timeout=timeout) or b"null")

    def queue(self) -> dict:
        q = self.get("/queue")
        return {"running": q.get("queue_running") or [], "pending": q.get("queue_pending") or []}

    def submit(self, graph: dict) -> str:
        r = json.loads(self.req("POST", "/prompt", {"prompt": graph, "client_id": f"litecode-{uuid.uuid4().hex[:10]}"}))
        if r.get("node_errors"):
            raise ComfyError("工作流校验失败: " + json.dumps(r["node_errors"], ensure_ascii=False)[:3000])
        return r["prompt_id"]

    def upload(self, path: Path, subfolder: str) -> dict:
        """POST /upload/image (图片/视频/音频都走它, 原样存进 input/<subfolder>/), 文件名带内容哈希防撞名。"""
        data = Path(path).read_bytes()
        name = f"lc_{hashlib.md5(data).hexdigest()[:10]}_{Path(path).name}"
        b = uuid.uuid4().hex
        parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in (("type", "input"), ("subfolder", subfolder), ("overwrite", "true"))]
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: '
                     f'{mimetypes.guess_type(name)[0] or "application/octet-stream"}\r\n\r\n'.encode() + data + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        return json.loads(self.req("POST", "/upload/image", b"".join(parts), timeout=600,
                                   headers={"Content-Type": f"multipart/form-data; boundary={b}"}))

    def view(self, filename: str, subfolder: str = "", type_: str = "output", public: bool = True) -> str:
        q = "/view?" + urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": type_})
        return (self.self_url if public else "") + q


def object_info(c: Comfy, refresh: bool = False) -> dict:
    p = cache_dir(c.base) / "object_info.json"
    if p.exists() and not refresh and time.time() - p.stat().st_mtime < 6 * 3600:
        return json.loads(p.read_text())
    oi = c.get("/object_info", timeout=180)
    p.write_text(json.dumps(oi))
    return oi


def saved_workflows(c: Comfy) -> list[str]:
    try:
        r = c.get("/api/userdata?dir=workflows", timeout=20)
        return [x if isinstance(x, str) else x.get("path", "") for x in r] if isinstance(r, list) else []
    except (ComfyError, ValueError):
        return []


# ================= 工作流: 读取 / 改参 =================

def is_api(d) -> bool:
    return isinstance(d, dict) and bool(d) and all(
        isinstance(v, dict) and "class_type" in v for k, v in d.items() if not str(k).startswith("_"))


def split_template(d: dict) -> tuple[dict | None, dict]:
    """返回 (API 工作流, 模板元信息); 网页格式返回 (None, {})。
    模板文件: {"_litecode_template": {kind, desc, params, inputs, required_inputs, optional_inputs, defaults, model_any},
               "prompt": {API 工作流}}"""
    if "_litecode_template" in d:
        return copy.deepcopy(d["prompt"]), dict(d["_litecode_template"])
    if is_api(d.get("prompt")):
        return copy.deepcopy(d["prompt"]), {}
    if is_api(d):
        return {k: v for k, v in copy.deepcopy(d).items() if not str(k).startswith("_")}, {}
    if isinstance(d.get("nodes"), list):
        return None, {}
    raise ComfyError("不认识的工作流格式 (既不是 API 格式也不是 ComfyUI 网页保存的格式)")


def template_dirs() -> list[Path]:
    return [d for d in [HERE.parent / "templates", *sorted(HERE.parent.parent.glob("comfyui-*/templates"))]
            if d.is_dir()]


def list_templates(kind: str | None = None) -> list[dict]:
    out, seen = [], set()
    for d in template_dirs():
        for f in sorted(d.glob("*.json")):
            if f.stem in seen:
                continue
            seen.add(f.stem)
            g, meta = split_template(json.loads(f.read_text(encoding="utf-8")))
            if meta and (not kind or meta.get("kind") == kind):
                out.append({**meta, "name": f.stem, "path": str(f), "graph": g})
    return out


def resolve_node(graph: dict, ref: str) -> str:
    if ref in graph:
        return ref
    by_title = [k for k, v in graph.items() if (v.get("_meta") or {}).get("title") == ref]
    by_cls = [k for k, v in graph.items() if v.get("class_type") == ref]
    for hits in (by_title, by_cls):
        if len(hits) == 1:
            return hits[0]
    if by_cls:
        raise ComfyError(f"类型 {ref} 有多个节点 ({', '.join(by_cls)}), 请写节点 id")
    raise ComfyError(f"工作流里没有节点 {ref!r} (有: {', '.join(list(graph)[:30])})")


def set_field(graph: dict, target: str, value, raw: bool = False) -> str:
    """target = 节点.字段。raw=True 时按原字段类型解析字符串 (原来是字符串就保持字符串, 否则试 JSON)。"""
    if "." not in target:
        raise ComfyError(f"要写成 节点.字段=值, 收到 {target!r}")
    ref, field = target.split(".", 1)
    nid = resolve_node(graph, ref)
    ins = graph[nid].setdefault("inputs", {})
    if raw and not isinstance(ins.get(field), str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    ins[field] = value
    return f"{nid}.{field}"


def drop_node(graph: dict, nid: str) -> None:
    """删节点, 并删掉其它节点里连到它的输入 (可选素材没给时用)。"""
    graph.pop(nid, None)
    for v in graph.values():
        for f, val in list((v.get("inputs") or {}).items()):
            if isinstance(val, list) and len(val) == 2 and str(val[0]) == nid:
                v["inputs"].pop(f)


def find_targets(graph: dict, meta: dict, key: str) -> list[str]:
    """快捷参数 → 字段。模板 params 优先; 普通工作流按启发式找。"""
    if (meta.get("params") or {}).get(key):
        return [t for t in meta["params"][key] if t.split(".", 1)[0] in graph]
    if key in ("prompt", "negative"):        # 顺着采样器的 positive/negative 连线找文本节点
        for v in graph.values():
            link = (v.get("inputs") or {}).get("positive" if key == "prompt" else "negative")
            if isinstance(link, list) and str(link[0]) in graph:
                ins = graph[str(link[0])].get("inputs") or {}
                for f in ("prompt", "text"):
                    if isinstance(ins.get(f), str):
                        return [f"{link[0]}.{f}"]
        if key == "negative":
            return []
        return [f"{k}.prompt" for k, v in graph.items() if isinstance((v.get("inputs") or {}).get("prompt"), str)][:1]
    names = {"seed": ("seed", "noise_seed"), "steps": ("steps",), "cfg": ("cfg",), "denoise": ("denoise",),
             "width": ("width",), "height": ("height",), "fps": ("fps", "frame_rate"),
             "frames": ("length", "num_frames", "frames", "video_length"),
             "sampler": ("sampler_name",), "scheduler": ("scheduler",)}[key]
    want = str if key in ("sampler", "scheduler") else (int, float)
    out = []
    for nid, n in graph.items():
        ins = n.get("inputs") or {}
        if key in ("width", "height") and not {"width", "height"} <= set(ins):
            continue                         # 只改同时有宽高的节点 (潜空间/视频条件), 不碰缩放节点
        out += [f"{nid}.{f}" for f in names if isinstance(ins.get(f), want) and not isinstance(ins.get(f), bool)]
    return out


def apply_params(graph: dict, meta: dict, params: dict) -> list[str]:
    done = []
    for key, val in params.items():
        targets = find_targets(graph, meta, key)
        if not targets:
            raise ComfyError(f"工作流里找不到 --{key} 对应的字段, 请改用 --set 节点.字段=值")
        for t in targets:
            nid, f = t.split(".", 1)
            graph[nid]["inputs"][f] = val
            done.append(f"{t}={json.dumps(val, ensure_ascii=False)[:60]}")
    return done


def input_slots(graph: dict, meta: dict) -> dict:
    """素材槽位: 模板 inputs; 普通工作流按加载节点顺序自动叫 image / image2 / ..."""
    if meta.get("inputs"):
        return dict(meta["inputs"])
    slots, n = {}, 0
    for nid, node in graph.items():
        cls, ins = node.get("class_type", ""), node.get("inputs") or {}
        field = "url" if URL_LOADER.search(cls) else FILE_LOADERS.get(cls)
        if field and field in ins:
            n += 1
            slots["image" if n == 1 else f"image{n}"] = f"{nid}.{field}"
    return slots


# ================= 结合 object_info =================

def combo_options(spec):
    if isinstance(spec, list) and spec:
        if isinstance(spec[0], list):
            return spec[0]
        if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
            return spec[1].get("options") or []
    return None


def input_specs(oi: dict, cls: str) -> dict:
    s = (oi.get(cls) or {}).get("input") or {}
    return {**(s.get("required") or {}), **(s.get("optional") or {})}


def model_catalog(oi: dict) -> dict:
    """各加载节点下拉里的模型文件 {字段名: [文件]} (只收模型扩展名, 排除云 API 节点的型号名)。"""
    cat = {}
    for cls in oi:
        for name, s in input_specs(oi, cls).items():
            opts = combo_options(s)
            if opts and name.endswith("_name"):
                files = [o for o in opts if isinstance(o, str) and o.lower().endswith(MODEL_EXTS)]
                if files:
                    cat.setdefault(name, set()).update(files)
    return {k: sorted(v) for k, v in sorted(cat.items())}


def fix_models(graph: dict, oi: dict, any_fields=()) -> tuple[list[str], list[str]]:
    """模板里的模型这台没有时, 只换成名字足够像的同系列文件 (如 bf16↔int8); 不像就如实报缺什么, 不硬套。
    any_fields (模板 model_any) 里的字段可换成任意可用模型。返回 (改动, 问题)。"""
    changes, problems = [], []
    for nid, node in graph.items():
        cls = node.get("class_type")
        specs = input_specs(oi, cls)
        for f, val in (node.get("inputs") or {}).items():
            spec = specs.get(f)
            extra = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
            if not isinstance(val, str) or cls in FILE_LOADERS or extra.get("image_upload"):
                continue                     # 素材槽: 上传的新文件不在缓存的下拉里
            opts = combo_options(spec)
            if opts is None or val in opts:
                continue
            if not val.lower().endswith(MODEL_EXTS):
                problems.append(f"{nid}.{f}={val!r} 不在可选值里 (可选: {', '.join(map(str, opts[:8]))})")
                continue
            files = [o for o in opts if isinstance(o, str) and o.lower().endswith(MODEL_EXTS)]
            best = difflib.get_close_matches(val, files, n=1, cutoff=0.0 if f"{nid}.{f}" in any_fields else 0.6)
            if not best:
                problems.append(f"{nid}.{f}: 缺模型 {val} (这台有: {', '.join(files[:6]) or '无'})")
                continue
            node["inputs"][f] = best[0]
            changes.append(f"{nid}.{f}: {val} → {best[0]}")
    return changes, problems


def template_fit(graph: dict, meta: dict, oi: dict) -> tuple[bool, list[str]]:
    miss = sorted({n["class_type"] for n in graph.values() if n["class_type"] not in oi})
    if miss:
        return False, ["缺节点 " + ", ".join(miss)]
    changes, problems = fix_models(copy.deepcopy(graph), oi, meta.get("model_any") or ())
    return not problems, problems + [f"模型替换 {c}" for c in changes]


def follow_server_ray(c: Comfy, graph: dict) -> str | None:
    """Raylight 多卡工作流: ray_init 参数跟随服务器最近一次任务 (同命名空间/注意力/卡数), 复用已加载的 Ray worker,
    不在线上机上另起一套占显存。服务器没有这类历史就用模板原值。"""
    rays = [k for k, v in graph.items() if v.get("class_type") == "RayInitializerAdvanced"]
    if not rays:
        return None
    try:
        hist = c.get("/history?max_items=5", timeout=30) or {}
    except ComfyError:
        return None
    for item in sorted(hist.values(), key=lambda h: (h.get("prompt") or [0])[0], reverse=True):
        g = (item.get("prompt") or [None, None, {}])[2] or {}
        for v in g.values():
            if isinstance(v, dict) and v.get("class_type") == "RayInitializerAdvanced":
                for k in rays:
                    graph[k]["inputs"].update({f: x for f, x in v["inputs"].items() if not isinstance(x, list)})
                return v["inputs"].get("ray_cluster_namespace")
    return None


# ================= 网页格式 → API 格式 =================

def ui_to_api(ui: dict, oi: dict) -> dict:
    """ComfyUI 网页保存的工作流 → API 格式 (尽力而为: 支持旁路/静音/Reroute/Primitive, 不支持子图)。"""
    nodes = {n["id"]: n for n in ui.get("nodes") or []}
    subs = {s.get("id") for s in ((ui.get("definitions") or {}).get("subgraphs") or [])}
    if any(n.get("type") in subs for n in nodes.values()):
        raise ComfyError("工作流含子图 (subgraph), 转不了: 请在 ComfyUI 网页里 '导出 (API)' 另存, 或改用技能模板")
    links = {}
    for lk in ui.get("links") or []:
        if isinstance(lk, dict):
            lk = [lk["id"], lk["origin_id"], lk["origin_slot"], lk["target_id"], lk["target_slot"], lk.get("type")]
        links[lk[0]] = (lk[1], lk[2], lk[5] if len(lk) > 5 else None)

    def origin(link_id, want=None, depth=0):
        if link_id not in links or depth > 50:
            return None
        src_id, slot, ltype = links[link_id]
        src = nodes.get(src_id) or {}
        if src.get("type") == "Reroute" or src.get("mode") == 4:          # 透传 / 旁路
            for i in src.get("inputs") or []:
                if i.get("link") is not None and (src.get("type") == "Reroute" or i.get("type") in (want or ltype, "*")):
                    return origin(i["link"], want or ltype, depth + 1)
            return None
        if not src or src.get("mode") == 2:
            return None
        if src.get("type", "").startswith("Primitive"):
            wv = src.get("widgets_values")
            return ("value", wv[0] if isinstance(wv, list) and wv else wv)
        return ("link", [str(src_id), slot])

    api = {}
    for nid, n in nodes.items():
        cls = n.get("type") or ""
        if n.get("mode", 0) in (2, 4) or cls in ("Note", "MarkdownNote", "Reroute") or cls.startswith("Primitive"):
            continue
        if cls not in oi:
            raise ComfyError(f"节点 {nid} 的类型 {cls} 在这台 ComfyUI 上不存在")
        specs = input_specs(oi, cls)
        node_inputs = n.get("inputs") or []
        widgets = [i["name"] for i in node_inputs if i.get("widget")]
        if not widgets:                      # 老格式: 控件不在 inputs 里, 按 object_info 顺序取非连线输入
            linked = {i["name"] for i in node_inputs}
            widgets = [k for k, s in specs.items() if k not in linked and (combo_options(s) is not None or (
                isinstance(s, list) and s and s[0] in ("INT", "FLOAT", "STRING", "BOOLEAN")))]
        inputs, wv, i = {}, n.get("widgets_values"), 0
        if isinstance(wv, dict):
            inputs = {k: v for k, v in wv.items() if k in specs}
        for name in widgets if isinstance(wv, list) else []:
            if i >= len(wv):
                break
            inputs[name], i = wv[i], i + 1
            if name in ("seed", "noise_seed") and i < len(wv) and wv[i] in CONTROL_VALUES:
                i += 1                       # 跳过 "control_after_generate" 的 randomize/fixed
        for inp in node_inputs:
            if inp.get("link") is not None:
                got = origin(inp["link"], inp.get("type"))
                if got:
                    inputs[inp["name"]] = got[1]
        api[str(nid)] = {"class_type": cls, "inputs": inputs}
    for v in api.values():                   # 指向被跳过节点的连线清掉
        for f, val in list(v["inputs"].items()):
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str) and val[0] not in api:
                v["inputs"].pop(f)
    return api


# ================= 产物 =================

def collect_outputs(hist: dict) -> list[dict]:
    """history 里所有带 filename 的产物 (images/gifs/videos/audio...); 有 output 类型时忽略 temp 预览。"""
    out = [{"node": nid, "kind": kind, "filename": it["filename"], "subfolder": it.get("subfolder", ""),
            "type": it.get("type", "output")}
           for nid, o in (hist.get("outputs") or {}).items() for kind, items in (o or {}).items()
           if isinstance(items, list) for it in items if isinstance(it, dict) and it.get("filename")]
    return [o for o in out if o["type"] == "output"] or out


def exec_timing(hist: dict) -> dict:
    msgs = {m[0]: m[1] or {} for m in (hist.get("status") or {}).get("messages") or [] if len(m) == 2}
    t0 = msgs.get("execution_start", {}).get("timestamp")
    t1 = (msgs.get("execution_success") or msgs.get("execution_error") or {}).get("timestamp")
    return {"render_s": round((t1 - t0) / 1000, 1) if t0 and t1 else None,
            "cached": msgs.get("execution_cached", {}).get("nodes", []), "error": msgs.get("execution_error")}


# ================= 子命令 =================

def gpu_name(raw: str) -> str:
    """'cuda:0 NVIDIA RTX 4500 Ada Generation : cudaMallocAsync' → 'NVIDIA RTX 4500 Ada Generation'"""
    return re.sub(r"\s*:\s*\w+$", "", re.sub(r"^\w+:\d+\s+", "", raw or "")).strip()


def do_probe(c: Comfy, refresh: bool) -> dict:
    stats = c.get("/system_stats", timeout=30)
    oi = object_info(c, refresh)
    q = c.queue()
    fam = {"URL 加载": ("FromURL",), "Qwen-Image": ("QwenImage",), "MiniMax H3": ("MiniMaxH3",), "Wan": ("Wan",),
           "LTX": ("LTXV",), "Flux": ("Flux",), "Raylight 多卡": ("RayInitializer", "XFuser"),
           "视频输出": ("SaveVideo", "CreateVideo", "VideoCombine")}
    nodes = {k: sorted(x for x in oi if any(s in x for s in subs) and not x.endswith(("Api", "ApiNode"))
                       and not x.startswith("ComfyCloud"))[:6] for k, subs in fam.items()}
    info = {
        "url": c.base, "via_ssh": c.route, "comfyui_version": (stats.get("system") or {}).get("comfyui_version"),
        "gpus": [f"{gpu_name(d.get('name'))} 空闲 {(d.get('vram_free') or 0) / 2**30:.1f}/{(d.get('vram_total') or 0) / 2**30:.1f}GB"
                 for d in stats.get("devices") or []],
        "queue": f"运行 {len(q['running'])} · 等待 {len(q['pending'])}", "models": model_catalog(oi),
        "nodes": {k: v for k, v in nodes.items() if v}, "saved_workflows": saved_workflows(c), "templates": [],
    }
    for t in list_templates():
        ok, why = template_fit(t["graph"], t, oi)
        info["templates"].append({"name": t["name"], "kind": t.get("kind"), "ok": ok, "desc": t.get("desc", ""),
                                  "notes": why[:3]})
    (cache_dir(c.base) / "probe.json").write_text(json.dumps(info, ensure_ascii=False, indent=1))
    store_update(c.base, probe={"version": info["comfyui_version"], "gpus": info["gpus"],
                                "templates_ok": [t["name"] for t in info["templates"] if t["ok"]],
                                "saved_workflows": info["saved_workflows"][:20]})
    return info


def probe_text(i: dict) -> str:
    L = [f"ComfyUI {i['url']}" + (f" (经 ssh {i['via_ssh']})" if i["via_ssh"] else "") + f" · 版本 {i['comfyui_version']}",
         *[f"  GPU{n} {g}" for n, g in enumerate(i["gpus"])], f"  队列: {i['queue']}", "  可用模型:"]
    L += [f"    {k} ({len(v)}): {', '.join(v[:8])}{' …' if len(v) > 8 else ''}" for k, v in i["models"].items()]
    L += ["  特色节点: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in i["nodes"].items()),
          "  已保存工作流: " + (", ".join(i["saved_workflows"]) or "(无)"), "  技能模板能否在这台跑:"]
    L += [f"    {'✓' if t['ok'] else '✗'} {t['name']} [{t['kind']}] {t['desc'][:36]}"
          + (f"  ({'; '.join(t['notes'])})" if t["notes"] else "") for t in i["templates"]]
    return "\n".join(L)


def load_graph(c: Comfy, spec: str, oi_get) -> tuple[dict, dict]:
    if spec.startswith("saved:"):
        raw = c.get("/api/userdata/" + urllib.parse.quote("workflows/" + spec[6:], safe=""), timeout=30)
    else:
        p = Path(spec).expanduser()
        name = spec[:-5] if spec.endswith(".json") else spec
        if not p.exists():
            p = next((d / f"{name}.json" for d in template_dirs() if (d / f"{name}.json").exists()), p)
        if not p.exists():
            raise ComfyError(f"找不到工作流 {spec!r} (不是文件也不是模板名; 模板见 comfy.py templates)")
        raw = json.loads(p.read_text(encoding="utf-8"))
    g, meta = split_template(raw)
    return (g if g is not None else ui_to_api(raw, oi_get())), meta


def feed_input(c: Comfy, graph: dict, target: str, value: str, subfolder: str, dry: bool) -> str:
    """URL 槽 (*FromURL) 填服务器能访问的链接 (本地文件先上传, 填服务器自己的 /view 链接);
    文件槽 (LoadImage 等) 填上传后的文件名。"""
    nid, field = target.split(".", 1)
    url_slot = field == "url" or bool(URL_LOADER.search(graph[nid].get("class_type", "")))
    if value.startswith(("http://", "https://")) and url_slot:
        val = value
    else:
        path = Path(value).expanduser()
        if value.startswith(("http://", "https://")):          # 文件槽给的是链接: 先下到本地
            path = workspace() / "comfy_inputs" / Path(urllib.parse.urlparse(value).path).name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not dry:
                path.write_bytes(urllib.request.urlopen(value, timeout=120).read())
        elif not path.exists():
            raise ComfyError(f"素材文件不存在: {value}")
        up = {"name": path.name, "subfolder": subfolder} if dry else c.upload(path, subfolder)
        if not dry:
            log(f"[up] {path.name} → input/{up.get('subfolder')}/{up['name']}")
        sub = up.get("subfolder") or ""
        val = c.view(up["name"], sub, "input") if url_slot else (f"{sub}/{up['name']}" if sub else up["name"])
    set_field(graph, target, val)
    return f"{target}={val}"


def prepare(c: Comfy, args, oi_get) -> tuple[dict, dict, list]:
    graph, meta = load_graph(c, args.workflow, oi_get)
    params = dict(meta.get("defaults") or {})
    params.update({k: getattr(args, k) for k in PARAM_KEYS if getattr(args, k, None) is not None})
    if params.get("seed") in (None, -1) and find_targets(graph, meta, "seed"):
        params["seed"] = random.randint(1, 2**31 - 1)          # 默认随机, 免得命中服务器缓存
    notes = apply_params(graph, meta, params)
    slots, given = input_slots(graph, meta), set()
    for item in args.input or []:
        k, _, v = item.partition("=")
        target = slots.get(k) or (k if "." in k else None)
        if not v or not target:
            raise ComfyError(f"--input 要写成 槽位=文件或URL, 收到 {item!r} (这个工作流的槽位: {', '.join(slots) or '无'})")
        given.add(k)
        nid, f = target.split(".", 1)
        notes.append(feed_input(c, graph, f"{resolve_node(graph, nid)}.{f}", v, args.subfolder, args.dry_run))
    missing = [k for k in meta.get("required_inputs") or [] if k not in given]
    if missing:
        raise ComfyError(f"模板 {args.workflow} 需要素材: " + " ".join(f"--input {k}=<文件或URL>" for k in missing))
    for k, nodes in (meta.get("optional_inputs") or {}).items():
        if k not in given:
            for n in nodes:
                drop_node(graph, n)
            notes.append(f"没给 {k}, 移除节点 {', '.join(nodes)}")
    for n in args.drop or []:
        drop_node(graph, resolve_node(graph, n))
        notes.append(f"删除节点 {n}")
    for item in args.set or []:
        k, _, v = item.partition("=")
        notes.append("设置 " + set_field(graph, k, v, raw=True) + f"={v[:60]}")
    for n in graph.values():
        if args.prefix and "filename_prefix" in (n.get("inputs") or {}):
            n["inputs"]["filename_prefix"] = args.prefix
    return graph, meta, notes


def wait_idle(c: Comfy, limit: float) -> bool:
    t0, last = time.time(), -1e9
    while True:
        q = c.queue()
        n = len(q["running"]) + len(q["pending"])
        if not n:
            return True
        if time.time() - t0 > limit:
            return False
        if time.time() - last >= 30:
            log(f"[queue] 服务器有 {n} 个任务在跑/排队 (可能是线上用户), 等空了再提交… 已等 {time.time() - t0:.0f}s")
            last = time.time()
        time.sleep(5)


def wait_done(c: Comfy, pid: str, timeout: float, t0: float) -> dict | None:
    started, last = None, -1e9
    while time.time() - t0 < timeout:
        h = (c.get(f"/history/{pid}") or {}).get(pid)
        st = (h or {}).get("status") or {}
        if h and (st.get("completed") or st.get("status_str") in ("success", "error")):
            return h
        now = time.time()
        if started is None:
            q = c.queue()
            if any(len(x) > 1 and x[1] == pid for x in q["running"]):
                started = now
                log(f"[run] 开始执行 (排队 {now - t0:.0f}s)")
            elif now - last >= 30:
                log(f"[queue] 排队中, 前面运行 {len(q['running'])} 个、等待 {len(q['pending'])} 个 (已等 {now - t0:.0f}s)")
                last = now
        elif now - last >= 30:
            log(f"[run] 执行中… {now - started:.0f}s")
            last = now
        time.sleep(2)
    return None


def finish(c: Comfy, graph: dict | None, hist: dict, pid: str, out_dir: Path, t0: float) -> dict:
    tm = exec_timing(hist)
    res = {"prompt_id": pid, "status": (hist.get("status") or {}).get("status_str"), "url": c.base,
           "wall_s": round(time.time() - t0, 1), "render_s": tm["render_s"], "files": []}
    if tm["render_s"] is not None:
        res["queue_s"] = round(max(0.0, res["wall_s"] - tm["render_s"]), 1)
    if res["status"] == "error":
        err = tm["error"] or {}
        res["error"] = {k: err.get(k) for k in ("node_id", "node_type", "exception_message")}
        log(f"[err] 执行失败: {json.dumps(res['error'], ensure_ascii=False)[:1500]}")
        return res
    samplers = [k for k, v in (graph or {}).items() if "Sampler" in v.get("class_type", "")]
    if any(s in tm["cached"] for s in samplers):
        res["cached"] = True
        log("[warn] 采样节点命中服务器缓存 (同参数同种子跑过), 没有真正生成; 要新结果请换 --seed")
    out_dir.mkdir(parents=True, exist_ok=True)
    host = host_port(c.base)[0]
    for o in collect_outputs(hist):
        dest = out_dir / f"{host}_{pid[:8]}_{Path(o['filename']).name}"
        dest.write_bytes(c.req("GET", c.view(o["filename"], o["subfolder"], o["type"], public=False), timeout=1200))
        res["files"].append({"path": str(dest), "bytes": dest.stat().st_size,
                             "url": c.view(o["filename"], o["subfolder"], o["type"])})
        log(f"[out] {dest}  ({dest.stat().st_size // 1024} KB)  {res['files'][-1]['url']}")
    log(f"[done] 总耗时 {res['wall_s']}s = 排队/加载 {res.get('queue_s', '?')}s + 服务器执行 {res['render_s']}s")
    return res


def out_dir_of(args) -> Path:
    return Path(args.out).expanduser() if args.out else workspace() / "comfy_outputs" / time.strftime("%Y%m%d")


def cmd_run(c: Comfy, args) -> int:
    oi_box = {}

    def oi_get():
        if "oi" not in oi_box:
            oi_box["oi"] = object_info(c)
        return oi_box["oi"]

    graph, meta, notes = prepare(c, args, oi_get)
    oi = oi_get()
    miss = sorted({n["class_type"] for n in graph.values() if n["class_type"] not in oi})
    if miss:
        raise ComfyError(f"这台 ComfyUI 缺节点 {', '.join(miss)} —— probe 看它能跑什么, 换模板或工作流")
    changes, problems = fix_models(graph, oi, meta.get("model_any") or ())
    if problems:
        raise ComfyError("这台机器跑不了这个工作流, 如实告诉用户缺什么:\n  " + "\n  ".join(problems))
    notes += [f"模型适配 {x}" for x in changes]
    if not args.dry_run:
        ns = follow_server_ray(c, graph)
        notes += [f"Ray 参数跟随服务器最近任务 (namespace={ns})"] if ns else []
    for n in notes:
        log(f"[wf] {n}")
    if args.dry_run:
        p = out_dir_of(args) / "dry_run_api.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(graph, ensure_ascii=False, indent=1))
        log(f"[dry-run] 最终 API 工作流: {p} (没提交)")
        return 0
    if not args.no_wait_idle and store_get(c.base).get("wait_idle", True) and not wait_idle(c, args.idle_timeout):
        log(f"[queue] 等了 {args.idle_timeout:.0f}s 队列仍不空, 没提交 (线上机不插队), 稍后再试")
        return 3
    t0 = time.time()
    pid = c.submit(graph)
    log(f"[run] 已提交 prompt_id={pid} → {c.base}")
    hist = wait_done(c, pid, args.timeout, t0)
    if hist is None:
        log(f"[timeout] {args.timeout:.0f}s 没完成 (任务仍在服务器上, 不取消)。稍后取: "
            f"python3 {Path(__file__).resolve()} wait {c.base} {pid}")
        return 4
    res = finish(c, graph, hist, pid, out_dir_of(args), t0)
    res["workflow"] = args.workflow
    log("RESULT " + json.dumps(res, ensure_ascii=False))
    if res["files"]:
        store_update(c.base, last_ok={"workflow": args.workflow, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                      "wall_s": res["wall_s"],
                                      "params": {k: getattr(args, k) for k in PARAM_KEYS if getattr(args, k, None) is not None}})
    return 0 if res["files"] else 2


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="comfy.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--via-ssh", help="SSH 跳板: ssh config 别名或 user@host:port (内网地址用; 成功后记住), none=强制直连")
    ap.add_argument("--self-url", help="服务器访问它自己的地址 (经端口转发/反向代理访问时, 给 *FromURL 节点用; 如容器内 http://127.0.0.1:8188)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("url", nargs="?")
    p.add_argument("--refresh", action="store_true", help="重拉节点定义 (默认缓存 6 小时)")
    p.add_argument("--json", action="store_true")
    sub.add_parser("templates").add_argument("--kind", choices=["image", "video"])
    p = sub.add_parser("workflows")
    p.add_argument("url", nargs="?")
    p.add_argument("name", nargs="?")
    p = sub.add_parser("run")
    p.add_argument("url", nargs="?")
    p.add_argument("-w", "--workflow", required=True, help="模板名 / API json 路径 / saved:服务器工作流名")
    for k in ("prompt", "negative", "sampler", "scheduler"):
        p.add_argument(f"--{k}")
    for k in ("seed", "steps", "width", "height", "frames"):
        p.add_argument(f"--{k}", type=int)
    for k in ("cfg", "fps", "denoise"):
        p.add_argument(f"--{k}", type=float)
    p.add_argument("--input", action="append", metavar="槽位=文件或URL")
    p.add_argument("--set", action="append", metavar="节点.字段=值")
    p.add_argument("--drop", action="append", metavar="节点")
    p.add_argument("--prefix", help="输出文件名前缀")
    p.add_argument("--subfolder", default="litecode", help="素材上传到 input/<subfolder>/")
    p.add_argument("-o", "--out", help="产物目录 (默认 $WORKSPACE/comfy_outputs/<日期>)")
    p.add_argument("--timeout", type=float, default=1800)
    p.add_argument("--idle-timeout", type=float, default=1800, help="最多等队列空多久")
    p.add_argument("--no-wait-idle", action="store_true", help="不等队列空 (只对独占机用)")
    p.add_argument("--dry-run", action="store_true", help="只生成最终 API 工作流, 不上传不提交")
    p = sub.add_parser("wait")
    p.add_argument("url")
    p.add_argument("prompt_id")
    p.add_argument("-o", "--out")
    p.add_argument("--timeout", type=float, default=1800)
    sub.add_parser("endpoints")
    p = sub.add_parser("remember")
    p.add_argument("url")
    p.add_argument("--via", help="SSH 跳板别名 (none=清除)")
    p.add_argument("--wait-idle", choices=["yes", "no"], help="yes=线上机等队列空 / no=独占机")
    p.add_argument("--note")
    p.add_argument("--self-url", dest="remember_self_url", help="记住服务器自访问地址")
    sub.add_parser("forget").add_argument("url")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "templates":
            for t in list_templates(args.kind):
                ins = " ".join(f"{k}{'' if k in (t.get('required_inputs') or []) else '?'}" for k in t.get("inputs") or {})
                log(f"{t['name']:<18} [{t['kind']}] {t.get('desc', '')}" + (f"  素材: {ins}" if ins else ""))
            return 0
        if args.cmd == "endpoints":
            log("\n".join(describe(r) for r in store_all().values()) or "(还没记住任何 ComfyUI 地址)")
            return 0
        if args.cmd == "forget":
            d = store_all()
            log("已删除" if d.pop(norm_url(args.url), None) else "本来就没记")
            store_save(d)
            return 0
        if args.cmd == "remember":
            log(describe(store_update(args.url, touch=False, via="" if args.via == "none" else args.via, note=args.note,
                                      wait_idle=None if args.wait_idle is None else args.wait_idle == "yes",
                                      self_url=getattr(args, "remember_self_url", None))))
            return 0
        url = resolve_url(args.url)
        c = Comfy(url, args.via_ssh, args.self_url)
        store_update(url, via=c.route)
        if args.cmd == "probe":
            info = do_probe(c, args.refresh)
            log(json.dumps(info, ensure_ascii=False, indent=1) if args.json else probe_text(info))
            return 0
        if args.cmd == "workflows":
            if not args.name:
                log("\n".join(saved_workflows(c)) or "(服务器没有已保存的工作流)")
                return 0
            g, _ = load_graph(c, "saved:" + args.name, lambda: object_info(c))
            p = cache_dir(c.base) / f"{Path(args.name).stem}_api.json"
            p.write_text(json.dumps(g, ensure_ascii=False, indent=1))
            log(f"已转成 API 格式: {p}  (用 -w saved:{args.name} 或 -w {p})")
            for nid, n in g.items():
                plain = {k: v for k, v in n["inputs"].items() if not isinstance(v, list)}
                log(f"  {nid:>12} {n['class_type']:<26} {json.dumps(plain, ensure_ascii=False)[:150]}")
            return 0
        if args.cmd == "run":
            return cmd_run(c, args)
        if args.cmd == "wait":
            t0 = time.time()
            hist = wait_done(c, args.prompt_id, args.timeout, t0)
            if hist is None:
                log(f"[timeout] 仍未完成: {args.prompt_id}")
                return 4
            res = finish(c, None, hist, args.prompt_id, out_dir_of(args), t0)
            log("RESULT " + json.dumps(res, ensure_ascii=False))
            return 0 if res["files"] else 2
    except (ComfyError, OSError, ValueError, KeyError) as e:
        log(f"[err] {e}")
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
