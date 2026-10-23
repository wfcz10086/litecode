"""lib/providers.py — 「🔌 能力接入」: 外部服务卡片 (生图/生视频/语音/搜索/音乐/向量/通知/存储/任意 HTTP)。

一个服务 = 一张卡片, 核心是一段明文用法说明 (地址、key、curl 示例), agent 读了就能调用;
常用协议 (openai_images / comfyui / searxng / webhook / smtp / s3 ...) 另有内置适配, 可一键测试、自动路由。

存储: config.json 顶层 `providers` 列表 (lib.config.load_cfg / save_cfg 读写, 认 LITECODE_CONFIG,
原子写; 其余字段原样保留)。密钥明文存 config.json 是用户接受的现状; 列表接口按 config_router 同一套规则打码,
单条详情 / agent 的 get_provider 给明文。

每项字段:
  id, name, kind, protocol, base_url, api_key, model, extra{...}, usage (明文, 可含 {prompt} {api_key} 等占位),
  default (同类默认), enabled, note (一句话用途)
大模型 (models) 不在这里, 前端 llm 类只放一张"去模型管理"的链接卡。
"""
from __future__ import annotations

import copy
import re
import shlex
import threading
import uuid
from contextvars import ContextVar

KINDS: dict[str, dict] = {   # desc 是给普通用户看的大白话 (面板分组说明), 不出现工具名/协议名
    "image":     {"label": "生图", "icon": "🎨", "desc": "让 LiteCode 画图: 海报、插画、短视频配图, 也能照着参考图改图"},
    "video":     {"label": "生视频", "icon": "🎬", "desc": "让 LiteCode 做短视频: 文字生成视频、让图片动起来"},
    "tts":       {"label": "语音合成", "icon": "🔊", "desc": "把文字读出来: 配音、旁白、有声稿"},
    "asr":       {"label": "语音识别", "icon": "🎙️", "desc": "把录音、视频里说的话转成文字"},
    "search":    {"label": "搜索", "icon": "🔎", "desc": "让 LiteCode 上网查资料"},
    "music":     {"label": "音乐", "icon": "🎵", "desc": "给视频配背景音乐、生成歌曲"},
    "embedding": {"label": "向量", "icon": "🧮", "desc": "知识库用: 把文字变成可检索的向量 (一般不用管)"},
    "rerank":    {"label": "重排", "icon": "📶", "desc": "知识库用: 把搜到的内容按相关度重新排序 (一般不用管)"},
    "notify":    {"label": "通知", "icon": "📣", "desc": "任务做完自动发到你的钉钉 / 飞书 / 企微群或邮箱"},
    "storage":   {"label": "对象存储", "icon": "🗄️", "desc": "把生成的文件存到云端存储 (OSS / COS / MinIO 等)"},
    "llm":       {"label": "大模型", "icon": "🧠", "desc": "对话模型在「模型管理」里配置"},
    "other":     {"label": "其他", "icon": "🧩", "desc": "其他任何服务: 写清楚怎么调用, LiteCode 照着用"},
}

PROTOCOLS: dict[str, str] = {
    "openai_images": "OpenAI 兼容生图 (/images/generations)",
    "comfyui": "ComfyUI (工作流, 委托 comfyui 技能脚本)",
    "searxng": "SearXNG (/search?format=json)",
    "openai_tts": "OpenAI 兼容语音合成 (/audio/speech)",
    "openai_asr": "OpenAI 兼容语音识别 (/audio/transcriptions)",
    "openai_embeddings": "OpenAI 兼容向量 (/embeddings)",
    "openai_rerank": "重排 (/rerank)",
    "wecom_webhook": "企业微信群机器人 webhook",
    "feishu_webhook": "飞书自定义机器人 webhook",
    "dingtalk_webhook": "钉钉自定义机器人 webhook",
    "smtp": "SMTP 邮件",
    "s3": "S3 兼容对象存储",
    "builtin": "内置 (本机程序, 只读)",
    "http": "通用 HTTP (按明文用法 / curl 调用)",
}

# 内置兜底卡片: 不存 config, 列表里只读展示, 路由时排在同类最后
BUILTINS: list[dict] = [{
    "id": "builtin_bgm", "name": "本地 BGM 配乐 (内置)", "kind": "music", "protocol": "builtin",
    "base_url": "", "api_key": "", "model": "", "default": False, "enabled": True, "readonly": True,
    "extra": {"script": "skills/bgm-music/scripts/bgm_gen.py"},
    "note": "本机直接生成无版权背景音乐 (不联网、免费、几秒出), 没有其他音乐接入时自动用它",
    "usage": ("python3 skills/bgm-music/scripts/bgm_gen.py --mood upbeat --duration 30 -o /tmp/litecode_workspace/music/bgm.mp3\n"
              "mood: calm 舒缓 / upbeat 轻快 / epic 史诗 / sad 伤感 / tech 科技 / warm 温暖 / funny 搞笑 / suspense 悬疑\n"
              "--loop 生成无缝循环段; --bpm / --key 调速度与调性。也可直接调 generate_music 工具。"),
}]

FIELDS = ("id", "name", "kind", "protocol", "base_url", "api_key", "model", "extra", "usage", "note",
          "default", "enabled")
MASK = "••••"
_LOCK = threading.Lock()


# ── 读写 ──────────────────────────────────────────────────────

def _cfg_io():
    from .config import load_cfg, save_cfg
    return load_cfg, save_cfg


def normalize(p: dict) -> dict:
    """补齐字段、规整类型; 未知 kind/protocol 归到 other/http。"""
    p = dict(p or {})
    out = {
        "id": str(p.get("id") or "").strip() or new_id(),
        "name": str(p.get("name") or "").strip()[:80],
        "kind": str(p.get("kind") or "other").strip().lower(),
        "protocol": str(p.get("protocol") or "http").strip().lower(),
        "base_url": str(p.get("base_url") or "").strip(),
        "api_key": str(p.get("api_key") or "").strip(),
        "model": str(p.get("model") or "").strip(),
        "extra": p.get("extra") if isinstance(p.get("extra"), dict) else {},
        "usage": str(p.get("usage") or ""),
        "note": str(p.get("note") or "").strip()[:200],
        "default": bool(p.get("default", False)),
        "enabled": bool(p.get("enabled", True)),
    }
    if out["kind"] not in KINDS:
        out["kind"] = "other"
    if out["protocol"] not in PROTOCOLS:
        out["protocol"] = "http"
    if not out["name"]:
        out["name"] = f"{KINDS[out['kind']]['label']}-{out['id'][-4:]}"
    for k, v in p.items():   # 未知字段原样保留 (以后加字段不丢)
        if k not in out and not k.startswith("_"):
            out[k] = v
    return out


def new_id() -> str:
    return "p_" + uuid.uuid4().hex[:8]


def load_all(cfg: dict | None = None) -> list[dict]:
    if cfg is None:
        cfg = _cfg_io()[0]()
    raw = cfg.get("providers")
    return [normalize(x) for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


def _write(items: list[dict]) -> None:
    load_cfg, save_cfg = _cfg_io()
    cfg = load_cfg()
    cfg["providers"] = items
    save_cfg(cfg)


def find(ref: str, items: list[dict] | None = None) -> dict | None:
    """按 id 或名字 (不分大小写) 找。"""
    items = load_all() if items is None else items
    ref = str(ref or "").strip()
    if not ref:
        return None
    items = list(items) + [normalize(b) for b in BUILTINS] + _discovered()
    for p in items:
        if p["id"] == ref:
            return p
    low = ref.lower()
    for p in items:
        if p["name"].lower() == low:
            return p
    # 模型常少写括号后缀, 如 "AniSora 动漫视频" → "AniSora 动漫视频 (gpu-a)": 唯一的包含匹配也算
    hits = [p for p in items if low in p["name"].lower() or p["name"].lower().split(" (")[0] == low]
    return hits[0] if len(hits) == 1 else None


def _discovered() -> list[dict]:
    """自动发现的已有能力 (SearXNG / ComfyUI / 内置配音), 只读; 失败就当没有。"""
    try:
        from .provider_discover import discover
        return discover()
    except Exception:
        return []


def _fix_defaults(items: list[dict], keep: dict | None = None) -> None:
    """每个 kind 至多一个 default; keep 指定的那张优先保留。"""
    seen = set()
    if keep and keep.get("default"):
        seen.add(keep["kind"])
    for p in items:
        if not p.get("default") or p is keep:
            continue
        if p["kind"] in seen:
            p["default"] = False
        else:
            seen.add(p["kind"])


def _merge_secret(new: str, old: str) -> str:
    """前端回传的打码占位 (••••xxxx) 视为"没改", 保留原值。"""
    return old if isinstance(new, str) and new.startswith(MASK) else new


def _unmask_extra(new: dict, old: dict) -> dict:
    out = {}
    for k, v in (new or {}).items():
        if isinstance(v, str) and v.startswith(MASK):
            if k in (old or {}):
                out[k] = old[k]
            continue
        out[k] = v
    return out


# ── 从明文自动识别 (lib/provider_infer.py) ─────────────────────

_EMPTY = {"protocol": "http", "kind": "other"}


def _explicit(data: dict) -> set:
    """调用方明确给了值的字段 (空串 / 默认值不算); extra 里的键记成 extra.xxx。"""
    out = set()
    for k, v in (data or {}).items():
        if k == "extra" and isinstance(v, dict):
            out |= {f"extra.{x}" for x, y in v.items() if y not in ("", None)}
        elif v not in ("", None) and _EMPTY.get(k) != v:
            out.add(k)
    return out


def auto_fill(p: dict, explicit: set, infer_protocol: bool = True) -> dict:
    """把明文里识别到的字段填进空着的位置; 记下哪些是自动填的 (p["inferred"]), 改明文时重新识别。"""
    from .provider_infer import infer
    got = infer(p)
    filled = list(p.get("inferred") or [])
    for f, v in got.items():
        if f in explicit:
            continue
        if f.startswith("extra."):
            k = f[6:]
            if not (p.get("extra") or {}).get(k):
                p.setdefault("extra", {})[k] = v
            else:
                continue
        elif f == "protocol":
            if not infer_protocol or p.get("protocol") not in ("", "http") or v not in PROTOCOLS:
                continue
            p["protocol"] = v
        elif f == "kind":
            if p.get("kind") not in ("", "other") or v not in KINDS:
                continue
            p["kind"] = v
        elif not p.get(f):
            p[f] = v
        else:
            continue
        if f not in filled:
            filled.append(f)
    p["inferred"] = filled
    return p


def _clear_inferred(p: dict, keep: set) -> None:
    for f in p.get("inferred") or []:
        if f in keep:
            continue
        if f.startswith("extra."):
            (p.get("extra") or {}).pop(f[6:], None)
        else:
            p[f] = _EMPTY.get(f, "")
    p["inferred"] = []


def prepare_draft(data: dict, base: dict | None = None) -> dict:
    """测试未保存的草稿: 与 create/update 同一套识别, 但不落盘。"""
    d = dict(base or {})
    d.update({k: v for k, v in (data or {}).items() if k != "id"})
    p = normalize(d)
    if base and ("usage" in data or "name" in data):
        _clear_inferred(p, _explicit(data))
    return auto_fill(p, _explicit(data), infer_protocol=not base or "inferred" in base)


def create(data: dict) -> dict:
    with _LOCK:
        items = load_all()
        d = dict(data or {})
        d.pop("id", None)
        p = auto_fill(normalize(d), _explicit(d))
        if not str((data or {}).get("name") or "").strip():
            raise ValueError("名称不能为空")
        if any(x["name"].lower() == p["name"].lower() for x in items):
            raise ValueError(f"已有同名接入「{p['name']}」, 换个名字")
        if p["kind"] != "llm" and not any(x["kind"] == p["kind"] and x.get("default") for x in items):
            p["default"] = True   # 该类第一张自动设为默认
        items.append(p)
        _fix_defaults(items, keep=p)
        _write(items)
        return p


def update(pid: str, data: dict) -> dict:
    with _LOCK:
        items = load_all()
        old = next((x for x in items if x["id"] == pid), None)
        if old is None:
            raise KeyError(pid)
        merged = copy.deepcopy(old)
        given = {}
        for k, v in (data or {}).items():
            if k == "id":
                continue
            if k == "api_key":
                v = _merge_secret(v, old.get("api_key", ""))
            elif k == "extra" and isinstance(v, dict):
                v = _unmask_extra(v, old.get("extra") or {})
            merged[k] = given[k] = v
        p = normalize(merged)
        p["id"] = pid
        if set(given) & {"usage", "base_url", "api_key", "protocol", "extra"}:
            p.pop("last_test", None)   # 内容变了, 之前的测试结果作废 (保存时前端会带上新的)
        if "last_test" in given:
            p["last_test"] = given["last_test"]
        if "usage" in given or "name" in given:   # 明文改了 → 之前自动识别的字段重新识别
            exp = _explicit(given)
            _clear_inferred(p, exp)
            auto_fill(p, exp, infer_protocol="inferred" in old)
        if any(x["id"] != pid and x["name"].lower() == p["name"].lower() for x in items):
            raise ValueError(f"已有同名接入「{p['name']}」, 换个名字")
        items[items.index(old)] = p
        _fix_defaults(items, keep=p)
        _write(items)
        return p


def delete(pid: str) -> bool:
    with _LOCK:
        items = load_all()
        left = [x for x in items if x["id"] != pid]
        if len(left) == len(items):
            return False
        gone = next(x for x in items if x["id"] == pid)
        if gone.get("default"):   # 默认被删 → 同类第一张启用的接任
            nxt = next((x for x in left if x["kind"] == gone["kind"] and x.get("enabled")), None)
            if nxt:
                nxt["default"] = True
        _write(left)
        return True


def set_default(pid: str) -> dict:
    with _LOCK:
        items = load_all()
        p = next((x for x in items if x["id"] == pid), None)
        if p is None:
            raise KeyError(pid)
        for x in items:
            if x["kind"] == p["kind"]:
                x["default"] = x is p
        p["enabled"] = True
        _write(items)
        return p


def record_test(pid: str, r: dict) -> None:
    """记下最近一次测试结果 (左侧列表的 ✅ / ⚪); 卡片不存在 (内置/自动发现) 时忽略。"""
    import time as _t
    with _LOCK:
        items = load_all()
        p = next((x for x in items if x["id"] == pid), None)
        if p is None:
            return
        p["last_test"] = {"ok": bool(r.get("ok")) and not r.get("untested"), "ts": int(_t.time()),
                          "msg": str(r.get("hint") or r.get("detail") or r.get("error") or "")[:200]}
        _write(items)


# ── 脱敏 / 展示 ───────────────────────────────────────────────

def _mask_value(v):
    try:   # 与 /api/config 同一套规则: 短密钥全遮, 长密钥露后 4 位
        from routers.config_router import _mask_value as mv
        return mv(v)
    except Exception:
        s = str(v)
        if s in ("", "EMPTY"):
            return v
        return MASK + s[-4:] if len(s) >= 16 else MASK


def _is_secret_key(k: str) -> bool:
    try:
        from routers.config_router import _is_secret_cfg_key
        return _is_secret_cfg_key(k)
    except Exception:
        k = str(k).lower()
        return k in {"password", "secret", "token", "api_key", "key"} or k.endswith(("_key", "_secret", "_token",
                                                                                         "_password"))


def masked(p: dict) -> dict:
    """列表接口用: api_key / extra 里像密钥的字段打码; usage 里出现的明文 key 也替换成打码值。"""
    q = copy.deepcopy(p)
    key = q.get("api_key") or ""
    if key:
        q["api_key"] = _mask_value(key)
    secrets = [key] if len(key) >= 6 else []
    ex = {}
    for k, v in (q.get("extra") or {}).items():
        if _is_secret_key(k) and isinstance(v, str) and v:
            ex[k] = _mask_value(v)
            if len(v) >= 6:
                secrets.append(v)
        else:
            ex[k] = v
    q["extra"] = ex
    u = q.get("usage") or ""
    for s in secrets:
        u = u.replace(s, _mask_value(s))
    q["usage"] = u
    q["has_key"] = bool(key)
    return q


_PH = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render(text: str, p: dict, /, **vars) -> str:
    """填占位: {api_key} {base_url} {model} + extra 里的键 + 调用方给的 {prompt} {width} 等。
    不认识的占位原样保留 (JSON 的花括号带引号, 不会被误伤)。"""
    if not text:
        return ""
    table = {"api_key": p.get("api_key", ""), "base_url": (p.get("base_url") or "").rstrip("/"),
             "model": p.get("model", ""), "name": p.get("name", "")}
    for k, v in (p.get("extra") or {}).items():
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            table.setdefault(str(k), str(v))
    for k, v in vars.items():
        if v is not None:
            table[k] = str(v)
    return _PH.sub(lambda m: table.get(m.group(1), m.group(0)), text)


def brief(p: dict) -> str:
    k = KINDS.get(p["kind"], KINDS["other"])
    flags = (" · 默认" if p.get("default") else "") + ("" if p.get("enabled") else " · 已停用")
    what = p.get("note") or k["desc"]
    return f"- {p['name']} [{k['label']}/{p['protocol']}{flags}] {what}"


def card_text(p: dict) -> str:
    """完整明文卡片 (agent 的 get_provider / 对话引用): 名称、类型、地址、key、模型、extra、用法。"""
    k = KINDS.get(p["kind"], KINDS["other"])
    lines = [f"### 接入: {p['name']}",
             f"类型: {k['label']} ({p['kind']}) · 协议: {p['protocol']} ({PROTOCOLS.get(p['protocol'], '')})"
             + (" · 默认" if p.get("default") else "") + ("" if p.get("enabled") else " · 已停用")]
    if p.get("note"):
        lines.append(f"用途: {p['note']}")
    if p.get("base_url"):
        lines.append(f"地址 base_url: {p['base_url']}")
    if p.get("api_key"):
        lines.append(f"密钥 api_key: {p['api_key']}")
    if p.get("model"):
        lines.append(f"模型 model: {p['model']}")
    for kk, v in (p.get("extra") or {}).items():
        lines.append(f"{kk}: {v}")
    usage = render(p.get("usage") or "", p)
    if usage.strip():
        lines += ["用法 (明文, 占位 {prompt} 等换成实际值后照着调用; 用 execute_shell 跑 curl):", usage.strip()]
    return "\n".join(lines)


# ── 路由 ──────────────────────────────────────────────────────

def candidates(kind: str, ref: str | None = None, items: list[dict] | None = None) -> list[dict]:
    """调用顺序: 指定的那张 (不管是否默认) → 同类默认 → 同类其余启用的 (按列表顺序)。"""
    items = load_all() if items is None else items
    same = [p for p in items if p["kind"] == kind and p.get("enabled")]
    first: list[dict] = []
    if ref:
        hit = find(ref, items)
        if hit is None:
            raise LookupError(f"没有名为 {ref} 的接入")
        first = [hit]
    rest = sorted(same, key=lambda p: not p.get("default"))
    rest += [normalize(b) for b in BUILTINS if b["kind"] == kind]
    return first + [p for p in rest if not first or p["id"] != first[0]["id"]]


# ── curl 解析 (测试 http 类用; 不走 shell) ─────────────────────

_CURL_FLAGS_NOARG = {"-s", "--silent", "-S", "--show-error", "-L", "--location", "-k", "--insecure", "-i",
                     "--include", "-v", "--verbose", "--compressed", "-f", "--fail", "-sS", "-Ss", "-sL", "-sSL",
                     "-fsSL", "-N", "--no-buffer", "-#", "--progress-bar", "-g", "--globoff"}
_CURL_FLAGS_SKIPARG = {"-o", "--output", "-m", "--max-time", "--connect-timeout", "-w", "--write-out",
                       "--retry", "-c", "--cookie-jar", "-D", "--dump-header", "--limit-rate", "--proto"}
_STOP = {"|", "||", "&&", ";", ">", ">>", "2>", "2>&1", "&"}


def first_curl(text: str) -> str | None:
    """取用法说明里第一条 curl 命令 (续行 / 引号跨行的规则见 provider_infer.curls)。"""
    from .provider_infer import curls
    allc = curls(text)
    return allc[0] if allc else None


def parse_curl(cmd: str) -> dict:
    """curl 命令 → {method, url, headers, params, content|json|data|files, auth, timeout}。
    只认常见选项; 含 shell 变量 / 命令替换 / @文件 时报错, 不执行 shell。"""
    try:
        toks = shlex.split(cmd)
    except ValueError as e:
        raise ValueError(f"curl 解析失败: {e}")
    if not toks or toks[0] != "curl":
        raise ValueError("不是 curl 命令")
    cut = next((i for i, t in enumerate(toks) if t in _STOP), len(toks))
    toks = toks[1:cut]
    method, url = None, None
    headers: dict = {}
    data_parts: list[str] = []
    form: list[tuple] = []
    auth = None
    get_mode = False
    is_json = False
    timeout = None
    i = 0

    def nxt():
        nonlocal i
        i += 1
        if i >= len(toks):
            raise ValueError(f"curl 选项 {toks[i - 1]} 缺参数")
        return toks[i]

    while i < len(toks):
        t = toks[i]
        if t in ("-X", "--request"):
            method = nxt().upper()
        elif t in ("-H", "--header"):
            h = nxt()
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()
        elif t in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"):
            v = nxt()
            if v.startswith("@") and t != "--data-raw":
                raise ValueError("curl 的 -d @文件 不支持测试, 请把内容直接写进用法")
            data_parts.append(v)
        elif t == "--json":
            data_parts.append(nxt())
            is_json = True
        elif t in ("-F", "--form"):
            v = nxt()
            k, _, val = v.partition("=")
            if val.startswith("@"):
                raise ValueError("curl -F 上传本地文件不支持测试")
            form.append((k, val))
        elif t in ("-u", "--user"):
            u = nxt()
            a, _, b = u.partition(":")
            auth = (a, b)
        elif t in ("-A", "--user-agent"):
            headers["User-Agent"] = nxt()
        elif t in ("-e", "--referer"):
            headers["Referer"] = nxt()
        elif t in ("-b", "--cookie"):
            headers["Cookie"] = nxt()
        elif t in ("-G", "--get"):
            get_mode = True
        elif t in ("-m", "--max-time"):
            try:
                timeout = float(nxt())
            except ValueError:
                pass
        elif t in _CURL_FLAGS_SKIPARG:
            nxt()
        elif t in _CURL_FLAGS_NOARG or (t.startswith("-") and not t.startswith("--") and len(t) > 1
                                         and set(t[1:]) <= set("sSLkivfN#g")):
            pass
        elif t == "--url":
            url = nxt()
        elif t.startswith("-"):
            raise ValueError(f"不支持的 curl 选项 {t}")
        else:
            url = url or t
        i += 1
    if not url:
        raise ValueError("curl 里没有 URL")
    if "$" in url or any("$(" in v or "`" in v for v in [url, *headers.values(), *data_parts]):
        raise ValueError("curl 里有 shell 变量或命令替换 ($X / $(...)), 测试不展开; 请写成实际值或 {占位}")
    if not re.match(r"https?://", url):
        url = "http://" + url
    out: dict = {"url": url, "headers": headers, "params": None, "auth": auth, "timeout": timeout}
    if is_json:
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json")
    body = "&".join(data_parts) if data_parts else None
    if get_mode and body:
        sep = "&" if "?" in url else "?"
        out["url"] = url + sep + body
        body = None
    if form:
        out["files"] = {k: (None, v) for k, v in form}
        method = method or "POST"
    elif body is not None:
        out["content"] = body.encode()
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        method = method or "POST"
    out["method"] = method or "GET"
    return out


# ── 对话里 `@` 引用 (完整明文挂进本轮上下文, lib/turn_sources.providers_ref) ──

MAX_REFS = 5
REF_LABEL = "## 用户在本条消息里引用的能力接入 (完整明文, 按用法直接调用; 回复里不要复述完整 key)\n"
_TURN: ContextVar = ContextVar("lc_turn_providers", default=())


def turn_begin(ids) -> None:
    out = []
    for x in ids or ():
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    _TURN.set(tuple(out[:MAX_REFS]))


def turn_refs() -> tuple:
    return _TURN.get()


def refs_block(ids, items: list[dict] | None = None) -> str:
    """引用的接入 → 上下文文本; 已删除的提示一句。"""
    if not ids:
        return ""
    items = load_all() if items is None else items
    parts = []
    for i in ids:
        p = find(i, items)
        parts.append(card_text(p) if p else f"### 接入 {i}: 已被删除, 告诉用户这张卡片不存在了")
    return REF_LABEL + "\n\n".join(parts)


def ref_meta(ids) -> list[dict]:
    """会话里存的引用摘要 (气泡只显示标题 chip; 明文点开时实时取)。"""
    items = load_all()
    out = []
    for i in list(ids or ())[:MAX_REFS]:
        p = find(str(i), items)
        if p and not any(o["id"] == p["id"] for o in out):
            out.append({"id": p["id"], "name": p["name"], "kind": p["kind"]})
    return out


def prompt_brief(items: list[dict] | None = None) -> str:
    """系统提示里的一小段「已接入能力」清单 (每类默认在前)。
    2026-10-23 实跑发现: 用户说"失恋 DJ 要有歌词", 模型按 bgm-music 技能的老说明走本地合成器, 根本不知道
    能力接入里已经有 ACE-Step。卡片没变时内容不变, 不破坏前缀缓存。"""
    try:
        items = load_all() if items is None else items
    except Exception:
        return ""
    on = [p for p in items if p.get("enabled", True) and p.get("kind") not in ("llm",)]
    if not on:
        return ""
    lines = ["\n## 已接入的外部能力 (左上角「能力接入」; 优先于技能说明里写的默认做法)"]
    for kind, meta in KINDS.items():
        ps = sorted((p for p in on if p.get("kind") == kind), key=lambda p: not p.get("default"))
        if ps:
            names = "、".join(f"{p['name']}{' [默认]' if p.get('default') else ''}" for p in ps)
            lines.append(f"- {meta['label']}: {names}")
    lines.append("用的时候先 enable_tools(['providers']), 再 get_provider(名称) 取完整明文 (地址 / key / curl), 照着调用; "
                 "生图 / 视频 / 音乐也可直接用 generate_image / generate_video / generate_music。")
    return "\n".join(lines)
