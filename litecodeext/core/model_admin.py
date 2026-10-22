"""core/model_admin.py — manage_models 工具: 对话里新增 / 测试 / 切换默认 / 删除大模型。

读写配置、连通测试、热切换一律复用 gateway/model_mgmt.py 里 HTTP 接口用的同一套函数
(upsert_model / probe_model / apply_switch / remove_model), 不另写一份。
只接 OpenAI 兼容 (/chat/completions) 接口: lib/transport 只说这一种协议。
任何返回给模型的文字都不含完整 key (只留前 4 后 4)。
"""
from __future__ import annotations

import re

import httpx

# 预设: base_url 都是 2026-10 实测可达的 OpenAI 兼容地址 (GET /models 有 200/401 响应);
# 默认模型名: deepseek/kimi/glm/dashscope/openrouter 按各家 /models 或官方文档核对过,
# siliconflow/ark/minimax/openai 没有 key 无法列模型, 按官方文档写, 连通测试失败时会列出该 key 实际可用的模型。
PRESETS: dict[str, dict] = {
    "deepseek": {"name": "DeepSeek 官方", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash",
                 "provider": "deepseek", "context_window": 128000,
                 "aliases": ("deepseek", "深度求索")},
    "kimi": {"name": "Kimi (月之暗面)", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2.7-code",
             "provider": "kimi", "context_window": 262144,
             "aliases": ("kimi", "moonshot", "月之暗面")},
    "glm": {"name": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-5.3-flash",
            "provider": "glm", "context_window": 200000,
            "aliases": ("glm", "zhipu", "智谱", "bigmodel", "清言")},
    "dashscope": {"name": "阿里百炼 (通义千问)", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "model": "qwen3.8-max", "provider": "qwen", "context_window": 131072,
                  "aliases": ("dashscope", "百炼", "阿里", "阿里云", "aliyun", "通义", "千问", "qwen", "bailian")},
    "siliconflow": {"name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
                    "model": "deepseek-ai/DeepSeek-V4-Flash", "provider": "siliconflow", "context_window": 128000,
                    "aliases": ("siliconflow", "硅基", "硅基流动", "silicon")},
    "ark": {"name": "火山方舟 (豆包)", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "doubao-seed-2-1-pro", "provider": "doubao", "context_window": 256000,
            "aliases": ("ark", "火山", "火山方舟", "火山引擎", "豆包", "doubao", "volcengine", "volces"),
            "hint": "方舟的 model 也可以填控制台里的推理接入点 ID (ep-…)"},
    "minimax": {"name": "MiniMax", "base_url": "https://api.minimaxi.com/v1", "model": "MiniMax-M3",
                "provider": "minimax", "context_window": 200000, "aliases": ("minimax", "海螺")},
    "openrouter": {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "model": "deepseek/deepseek-v4.1-flash", "provider": "openrouter", "context_window": 128000,
                   "aliases": ("openrouter",)},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-6.1-sol",
               "provider": "openai", "context_window": 200000, "aliases": ("openai", "chatgpt", "gpt")},
    "vllm": {"name": "自建 vLLM / SGLang", "base_url": "", "model": "", "provider": "custom", "deployment": "vllm",
             "context_window": 131072, "aliases": ("vllm", "sglang", "自建")},
    "ollama": {"name": "Ollama", "base_url": "http://127.0.0.1:11434/v1", "model": "", "provider": "custom",
               "deployment": "ollama", "context_window": 64000, "aliases": ("ollama",)},
}
# 只给了 "sk-…" 这种通用格式的 key 时, 按顺序用 GET /models 试出是哪家
_SK_CANDIDATES = ("deepseek", "kimi", "dashscope", "siliconflow")


def mask_key(key) -> str:
    k = str(key or "")
    if not k:
        return "(无)"
    if k.startswith("••••"):
        return k
    return f"{k[:4]}…{k[-4:]}" if len(k) > 12 else "****"


def redact(text, key) -> str:
    """上游报错原文里可能回显 key (如 DeepSeek 'Your api key: ****50c8'), 完整 key 一律换成脱敏值。"""
    s = str(text or "")
    k = str(key or "")
    if len(k) >= 6:
        s = s.replace(k, mask_key(k))
    return re.sub(r"\b(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]{8,}", r"\1…", s)


def preset_by_name(name) -> str:
    n = str(name or "").strip().lower()
    if not n:
        return ""
    if n in PRESETS:
        return n
    for pid, p in PRESETS.items():
        if any(a in n for a in p["aliases"]):
            return pid
    return ""


def preset_by_url(url) -> str:
    u = str(url or "").lower()
    for pid, p in PRESETS.items():
        host = re.sub(r"^https?://", "", p["base_url"]).split("/")[0]
        if host and host in u and "127.0.0.1" not in host:
            return pid
    return ""


def guess_by_key(key) -> tuple[str, ...]:
    """按 key 格式猜提供商 → 候选 (只有一个就是确定的)。"""
    k = str(key or "").strip()
    if k.startswith("sk-or-"):
        return ("openrouter",)
    if k.startswith(("sk-proj-", "sk-svcacct-")):
        return ("openai",)
    if k.startswith("sk-api-") or k.startswith("eyJ"):
        return ("minimax",)
    if re.fullmatch(r"[0-9a-f]{32}\.[A-Za-z0-9]{16}", k):
        return ("glm",)
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", k):
        return ("ark",)
    if k.startswith("sk-"):
        return _SK_CANDIDATES
    return ()


async def list_remote_models(base_url: str, key: str, timeout: float = 12.0) -> tuple[int, list]:
    """GET {base_url}/models → (HTTP 状态, 模型 id 列表)。网络错误返回 (0, [])。"""
    hdrs = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0)) as c:
            r = await c.get(base_url.rstrip("/") + "/models", headers=hdrs)
    except Exception:
        return 0, []
    ids: list = []
    if r.status_code == 200:
        try:
            ids = [str(x.get("id")) for x in (r.json().get("data") or []) if isinstance(x, dict) and x.get("id")]
        except Exception:
            ids = []
    return r.status_code, ids


async def _detect_provider(key: str, candidates) -> tuple[str, list]:
    for pid in candidates:
        st, ids = await list_remote_models(PRESETS[pid]["base_url"], key)
        if st == 200:
            return pid, ids
    return "", []


def _mm():
    from gateway import model_mgmt
    return model_mgmt


def _cfg() -> dict:
    from lib.config import load_cfg
    return load_cfg()


def _find(model_id: str) -> dict:
    for m in _cfg().get("models") or []:
        if m.get("id") == model_id:
            return m
    return {}


# ── actions ──────────────────────────────────────────────────

def do_presets() -> str:
    lines = ["内置提供商预设 (OpenAI 兼容接口; add 时 provider 填左列名字或中文名):"]
    for pid, p in PRESETS.items():
        url = p["base_url"] or "(需自己给 base_url)"
        mdl = p["model"] or "(需自己给 model)"
        lines.append(f"  {pid:<12}{p['name']:<16}{url}  默认模型 {mdl}" + (f"  ({p['hint']})" if p.get("hint") else ""))
    return "\n".join(lines)


def do_list() -> str:
    cfg = _cfg()
    default = str(cfg.get("default_model_id") or "")
    active = str((cfg.get("model") or {}).get("id") or "")
    try:
        active = str(_mm()._srv().MODEL_ID or active)
    except Exception:
        pass
    models = cfg.get("models") or []
    lines = [f"共 {len(models)} 个模型; 默认 {default or '(未设)'}" + (f", 当前激活 {active}" if active != default else "") + ":"]
    for m in models:
        mark = "★" if m.get("id") in (default, active) else " "
        wire = m.get("served_model_name") or m.get("id")
        caps = [x for x, f in (("看图", m.get("supports_vision")), ("思考", m.get("enable_thinking"))) if f]
        lines.append(f"{mark} {m.get('id')}  [{m.get('label') or ''}]  {m.get('backend_url')}  model={wire}  "
                     f"key={mask_key(m.get('api_key'))}" + (f"  {'/'.join(caps)}" if caps else ""))
    return "\n".join(lines)


async def do_test(model_id: str) -> str:
    entry = _find(model_id)
    if not entry:
        return f"ERROR: 没有 id 为 {model_id} 的模型 (先 action=list 看看)"
    r = await _mm().probe_model(entry)
    if r.get("ok"):
        return f"✓ {model_id} 可用, 延迟 {r.get('latency_ms')}ms, 回复: {r.get('reply_preview')!r}"
    return f"✗ {model_id} 不可用: {redact(r.get('error'), entry.get('api_key'))}"


async def do_add(args: dict) -> str:
    key = str(args.get("api_key") or "").strip()
    if key.startswith("••••") or "…" in key:
        return "ERROR: api_key 是脱敏值, 请让用户提供完整 key"
    pid = preset_by_name(args.get("provider")) or preset_by_url(args.get("base_url"))
    base_url = str(args.get("base_url") or "").strip().rstrip("/")
    remote_ids: list = []
    detected = ""
    if not pid and not base_url:
        cands = guess_by_key(key)
        if len(cands) == 1:
            pid = cands[0]
        elif cands:
            pid, remote_ids = await _detect_provider(key, cands)
            detected = pid
        if not pid:
            return ("ERROR: 认不出这个 key 是哪家的。请问用户用的是哪个平台 (provider), 或者直接给 base_url。\n" + do_presets())
    preset = PRESETS.get(pid, {})
    base_url = base_url or preset.get("base_url", "")
    if not base_url:
        return f"ERROR: {preset.get('name', '自定义接口')} 需要提供 base_url (形如 http://host:8000/v1)"
    if not re.match(r"^https?://", base_url):
        return f"ERROR: base_url 要以 http:// 或 https:// 开头: {base_url}"
    model = str(args.get("model") or "").strip() or preset.get("model", "")
    if not model:
        st, remote_ids = await list_remote_models(base_url, key)
        if st == 200 and len(remote_ids) == 1:
            model = remote_ids[0]
        else:
            tail = f" 该接口可用模型: {', '.join(remote_ids[:30])}" if remote_ids else ""
            return f"ERROR: 需要提供 model (发给接口的模型名)。{tail}"
    mid = str(args.get("id") or "").strip() or model
    old = _find(mid)
    if old and not args.get("overwrite"):
        # 同 id 直接覆盖会把用户原有的条目 (key/备注/探测结果) 冲掉: 必须明确要求才覆盖
        return (f"ERROR: 已有 id 为 {mid} 的模型 ({old.get('label') or ''} {old.get('backend_url')}), 没有改动。"
                f"用户起的名字请放在 id 参数里 (换个不重复的); 确实要更新这一条再加 overwrite=true。")
    if old and not key and old.get("backend_url", "").rstrip("/") == base_url:
        key = str(old.get("api_key") or "")   # 更新已有条目且没给 key: 沿用原 key
    entry = {
        "id": mid,
        "label": str(args.get("label") or "").strip() or f"{mid} · {preset.get('name') or re.sub(r'^https?://', '', base_url).split('/')[0]}",
        "provider": preset.get("provider", "custom"),
        "deployment": preset.get("deployment") or ("official" if pid and pid not in ("openrouter", "siliconflow") else "gateway"),
        "backend_type": "openai",
        "backend_url": base_url,
        "api_key": key,
        "api_format": "openai-chat",
        "context_window": int(args.get("context_window") or preset.get("context_window") or 128000),
        "max_tokens": 8192,
        "enable_thinking": False,
        "thinking_budget": 8192,
        "supports_vision": False,
    }
    if model != mid:
        entry["served_model_name"] = model
    for k in ("context_window",):
        if old.get(k) and not args.get(k):
            entry[k] = old[k]

    r = await _mm().probe_model(entry, test_vision=True, test_thinking=True)
    if not r.get("ok"):
        err = redact(r.get("error") or "未知错误", key)
        msg = [f"✗ 连通测试失败, 没有写入配置。上游返回: {err}",
               f"  地址 {base_url}  model={model}  key={mask_key(key)}"]
        if re.search(r"\b(400|404)\b", err) or "model" in err.lower():
            if not remote_ids:
                _, remote_ids = await list_remote_models(base_url, key)
            if remote_ids:
                msg.append(f"  该 key 可用的模型 (前 40 个): {', '.join(remote_ids[:40])}")
        if re.search(r"\b401\b|auth|invalid.*key|key.*invalid", err, re.I):
            msg.append("  看起来是 key 无效/过期或不属于这个平台。")
        if preset.get("hint"):
            msg.append("  " + preset["hint"])
        return "\n".join(msg)

    vt, tt = r.get("vision_test", ""), r.get("thinking_test", "")
    entry["supports_vision"] = vt == "pass"
    entry["enable_thinking"] = tt == "pass"
    entry["_note"] = f"对话里用 manage_models 添加; 探测: 看图={vt or '未测'} 思考={tt or '未测'}"
    count = _mm().upsert_model(entry)
    lines = [f"✓ 已{'更新' if old else '加入'}模型 {mid} (共 {count} 个), 连通测试通过, 延迟 {r.get('latency_ms')}ms",
             f"  平台 {preset.get('name') or '自定义'}{'(按 key 自动识别)' if detected else ''}  地址 {base_url}  model={model}  key={mask_key(key)}",
             f"  看图: {'支持' if entry['supports_vision'] else '不支持'} ({vt or '未测'})  思考: {'支持' if entry['enable_thinking'] else '未检测到'} ({tt or '未测'})",
             "  没有切换默认模型; 用户要用它可在输入框旁的模型按钮里选, 或明确说要设为默认再调 set_default。"]
    return "\n".join(lines)


def do_set_default(model_id: str) -> str:
    if not _find(model_id):
        return f"ERROR: 没有 id 为 {model_id} 的模型"
    try:
        d = _mm().apply_switch({"model_id": model_id}, set_default=True)
    except LookupError as e:
        return f"ERROR: {e}"
    return f"✓ 默认模型已切换为 {d.get('model')} ({d.get('backend_url')}), 跟随默认的会话下一条消息起生效"


def do_remove(model_id: str) -> str:
    try:
        n = _mm().remove_model(model_id)
    except PermissionError as e:
        return f"ERROR: {e}"
    return f"✓ 已删除模型 {model_id}" if n else f"没有 id 为 {model_id} 的模型, 未改动"


async def run(args: dict) -> str:
    action = str(args.get("action") or "list").strip().lower()
    mid = str(args.get("id") or "").strip()
    if action == "list":
        return do_list()
    if action == "presets":
        return do_presets()
    if action == "add":
        return await do_add(args)
    if action in ("test", "set_default", "remove") and not mid:
        return f"ERROR: action={action} 需要 id (模型条目 id, 见 action=list)"
    if action == "test":
        return await do_test(mid)
    if action == "set_default":
        return do_set_default(mid)
    if action == "remove":
        return do_remove(mid)
    return "ERROR: action 只能是 list / presets / add / test / set_default / remove"
