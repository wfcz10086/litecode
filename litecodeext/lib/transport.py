"""
lib/transport.py — vLLM/OpenAI 后端流式调用（带重试 + 指数退避 + 优雅降级）

[#68] 重试策略统一走 lib/retry.py (LLM_POLICY: base=1s, factor=2, jitter=±0.3, cap=20s)
"""
import json, re
from typing import AsyncGenerator, Optional
import httpx
from .config import BACKEND_URL, API_KEY, MODEL_ID, MAX_TOKENS, ENABLE_THINKING, THINKING_BUDGET, log, _LIVE, find_vision_fallback, wire_model_name
from .thinking_adapter import inject_params as _thinking_inject
from .retry import sleep_backoff, LLM_POLICY

# ── 重试配置 ──────────────────────────────────────────────────
MAX_RETRIES       = 3
RETRYABLE_CODES   = {429, 500, 502, 503, 504}   # 可重试的 HTTP 状态码
CONNECT_TIMEOUT   = 15
STREAM_TIMEOUT    = 600


# [vision-strip 2026-05-25] 非视觉模型 (deepseek-v4-pro / glm-4 等 supports_vision=false)
# 不接受 OpenAI 多模态 content (list of {type:image_url,...}), 会 400:
#   "Failed to deserialize: messages[N]: unknown variant `image_url`, expected `text`"
# 触发场景: wechat bot 历史里有用户上传图片, session 切到 text-only 模型后第一轮就爆.
# 修法: 检测 content 是 list 时, 剥掉 image_url 部分, 保留 text 拼成 string;
#       全是图就替换占位符 [图片附件: 当前模型不支持视觉, 无法查看].
def _strip_vision_from_messages(messages: list) -> list:
    """对 non-vision 模型, 把 multi-modal content 降级为 string."""
    if not messages:
        return messages
    cleaned = []
    for m in messages:
        c = m.get("content")
        if not isinstance(c, list):
            cleaned.append(m)
            continue
        # 多模态 content (list of parts) → 抽 text 拼回字符串
        texts = []
        img_count = 0
        for part in c:
            if not isinstance(part, dict):
                texts.append(str(part))
                continue
            ptype = part.get("type", "")
            if ptype == "text":
                t = part.get("text") or ""
                if t:
                    texts.append(t)
            elif ptype in ("image_url", "image", "input_image"):
                img_count += 1
            # 其他 type (audio/video/file/...) 忽略
        if img_count and not texts:
            new_content = f"[{img_count} 个图片附件 — 当前模型不支持视觉, 无法查看]"
        elif img_count:
            new_content = "\n".join(texts) + f"\n\n[附 {img_count} 个图片 — 当前模型不支持视觉, 已剥离]"
        else:
            new_content = "\n".join(texts)
        new_m = dict(m)
        new_m["content"] = new_content
        cleaned.append(new_m)
    return cleaned


def _model_supports_vision(model_id: str = "") -> bool:
    """判 model_id 是否支持 vision.

    [fix 2026-05-30] 旧版无脑读 _LIVE.supports_vision, 不看入参 model_id.
    当 vision-route 路由后 _model 已切到 fallback (如 qwen3.6), _LIVE 仍是
    active 主模型 (如 deepseek) supports_vision=False → 返 False → 后续
    vision-strip 把图剥了. 修法: 先按 model_id 在 config.models[] 里查;
    没传 model_id 或找不到才退回 _LIVE.
    """
    if model_id:
        ml = model_id.lower()
        # 启发式: deepseek 系列一律视为不支持 vision (避免找 config 找错条)
        if any(k in ml for k in ("deepseek-v4", "deepseek-chat", "deepseek-flash",
                                  "deepseek-r1", "deepseek-reasoner")):
            return False
        # 查 config.models[] 同名条 (任意一条 supports_vision=True 即算支持)
        try:
            from .config import CFG as _cfg
            for m in (_cfg.get("models") or []):
                if m.get("id") == model_id and bool(m.get("supports_vision", False)):
                    return True
        except Exception:
            pass
    # 没传 / 没匹配 → 退回 _LIVE active model
    try:
        if _LIVE.get("supports_vision") is not None:
            return bool(_LIVE.get("supports_vision"))
    except Exception:
        pass
    return True   # 其它默认认为支持 (Qwen/Anthropic/Gemini 等)


def _count_images_in_messages(messages: list) -> int:
    """统计 messages 里所有 image_url / image / input_image part 数量."""
    if not messages:
        return 0
    n = 0
    for m in messages:
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for part in c:
            if isinstance(part, dict) and part.get("type") in ("image_url", "image", "input_image"):
                n += 1
    return n


# [vision-route 2026-05-26] 主模型不支持 vision + messages 含图 时,
# 临时路由到 config.vision_fallback_id 指定的次选模型 (本次请求一次性).
# 返回 (model, url, key, routed_msg). routed_msg 非空 = 已路由, 调用方可 emit 给前端.
def _route_to_vision_fallback_if_needed(model: str, url: str, key: str,
                                        messages: list) -> tuple:
    if _model_supports_vision(model):
        return (model, url, key, "")
    img_n = _count_images_in_messages(messages)
    if img_n == 0:
        return (model, url, key, "")
    fb = find_vision_fallback()
    if not fb:
        # 没配 fallback 或找不到 → 回退到剥图逻辑 (由调用方处理)
        return (model, url, key, "")
    fb_id  = fb.get("id", "")
    fb_url = (fb.get("backend_url", "") or "").rstrip("/")
    fb_key = fb.get("api_key", "")
    if not (fb_id and fb_url):
        return (model, url, key, "")
    msg = (
        f"[VISION-ROUTE] {img_n} 个图片 → 临时路由到 vision fallback: "
        f"{model} → {fb_id} @ {fb_url}"
    )
    log.info(f"\033[36m{msg}\033[0m")
    return (fb_id, fb_url, fb_key, msg)


# [tool-role 兜底 2026-05-25] vLLM/OpenAI/DeepSeek 共通契约: role=tool 的消息必须
# 紧跟在 role=assistant + tool_calls 后面, 否则一律 400:
#   "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'"
# 真因: 子代理 (DAG 同层并行 + auto-rotate fork + memory 压缩裁剪) 序列化 assistant
# 时偶尔丢 tool_calls 字段, 导致后面 tool msg 变孤儿. 本函数在 transport 入口扫一遍,
# 丢掉所有孤儿 tool, 同时把"assistant 有 tool_calls 但实际无 tool 响应"的 tool_calls
# 也剥掉 (有些 backend 会因 partial tool 响应 400).
def _sanitize_tool_message_chain(messages: list) -> list:
    """返回清洗后的 messages 副本; 不改原对象."""
    if not messages:
        return messages
    cleaned = []
    open_tc_ids: set = set()   # 当前 assistant 还没拿到 tool 响应的 tool_call id
    last_assistant_idx = -1    # cleaned 里最近 assistant 的下标 (用于回填 tool_calls 裁剪)
    for m in messages:
        role = m.get("role")
        if role == "assistant":
            # 进入新 assistant turn — 关掉上一轮残留的 open ids
            open_tc_ids = set()
            tcs = m.get("tool_calls") or []
            if tcs:
                open_tc_ids = {tc.get("id") for tc in tcs if tc.get("id")}
            cleaned.append(dict(m))   # shallow copy, 后面可能裁 tool_calls
            last_assistant_idx = len(cleaned) - 1
        elif role == "tool":
            tcid = m.get("tool_call_id")
            if tcid and tcid in open_tc_ids:
                cleaned.append(m)
                open_tc_ids.discard(tcid)
            # 否则 orphan tool — 直接丢
        else:
            # user / system / 其他 — 进入新段, 关掉残留 open ids 并裁掉对应 tool_calls
            if open_tc_ids and last_assistant_idx >= 0:
                am = cleaned[last_assistant_idx]
                am_tcs = am.get("tool_calls") or []
                kept = [tc for tc in am_tcs if tc.get("id") not in open_tc_ids]
                if kept:
                    am["tool_calls"] = kept
                else:
                    am.pop("tool_calls", None)
                    # tool_calls 全裁光 → assistant 必须有 content (DeepSeek/vLLM 都要求)
                    if not am.get("content"):
                        am["content"] = ""
                open_tc_ids = set()
            cleaned.append(m)
            last_assistant_idx = -1
    # 收尾: 末尾若还有 open tc 但没 user/system 跟着, 也要裁 (避免 assistant 末尾发了 tool_calls 却没拿到响应)
    if open_tc_ids and last_assistant_idx >= 0:
        am = cleaned[last_assistant_idx]
        am_tcs = am.get("tool_calls") or []
        kept = [tc for tc in am_tcs if tc.get("id") not in open_tc_ids]
        if kept:
            am["tool_calls"] = kept
        else:
            am.pop("tool_calls", None)
            if not am.get("content"):
                am["content"] = ""
    return cleaned

async def vllm_stream(
    messages: list,
    tools: list,
    *,
    model: Optional[str] = None,
    backend_url: Optional[str] = None,
    api_key: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
    max_tokens_override: Optional[int] = None,
) -> AsyncGenerator[dict, None]:
    """
    流式调用 vLLM /v1/chat/completions。
    
    特性:
    - 指数退避重试（429/5xx）
    - 连接超时 15s，流超时 600s
    - JSON 解析错误跳过（不中断流）
    - 所有异常转为 RuntimeError 向上层传播
    """
    _model   = model or _LIVE["model_id"]
    _url     = (backend_url or _LIVE["backend_url"]).rstrip("/")
    _key     = api_key or _LIVE["api_key"]

    # [vision-route 2026-05-26] 优先检测 — 当前模型不支持 vision + 含图 → 临时切 fallback
    _model, _url, _key, _route_msg = _route_to_vision_fallback_if_needed(_model, _url, _key, messages)

    # [DeepSeek-compat 2026-05-23 / 2026-05-25 分版本分流]
    # DeepSeek 两套模型规则**完全相反** (官方文档自己矛盾):
    #   - deepseek-reasoner / R1 系列: reasoning_content 不能入 input → 传就 400
    #     (api-docs.deepseek.com/guides/reasoning_model: "CoT from previous rounds
    #     is not concatenated into the context")
    #   - deepseek-v4 / deepseek-chat 系列: reasoning_content 必须入 input → 缺就 400
    #     (api-docs.deepseek.com/guides/thinking_mode: "must be passed back to the API")
    # 这里按 model id 分流:
    _model_low = (_model or "").lower()
    if "deepseek-v4" in _model_low or "deepseek-chat" in _model_low or "deepseek-flash" in _model_low:
        # V4 系列: 缺 reasoning_content 的 assistant msg 补空字符串占位 (字段必须存在)
        for _m in messages:
            if _m.get("role") == "assistant" and "reasoning_content" not in _m:
                _m["reasoning_content"] = ""
    elif "deepseek-reasoner" in _model_low or "deepseek-r1" in _model_low:
        # R1 系列: 剥掉 input 里所有 reasoning_content (官方明令禁止)
        for _m in messages:
            if _m.get("role") == "assistant" and "reasoning_content" in _m:
                _m.pop("reasoning_content", None)

    # [vision-strip 2026-05-25] 非视觉模型 (路由失败也兜底) 剥多模态 content
    if not _model_supports_vision(_model):
        messages = _strip_vision_from_messages(messages)

    # [tool-role 兜底 2026-05-25] 全后端通用 — 清掉孤儿 tool / 裁齐 partial tool_calls
    messages = _sanitize_tool_message_chain(messages)

    # [vision-route 2026-05-26] log-only 标记 (前端不污染) — _route_to_vision_fallback_if_needed
    # 内部已经写 log.info 了, 这里不再 yield SSE marker (避免 agent multi-iter 重复污染 content).
    # 想看路由信号: docker logs litecode | grep VISION-ROUTE

    body = {
        # [wire-name 2026-08-26] _model 是 config 里的显示 id, 上游认的可能是别的名字
        # (自建 vLLM 的 --served-model-name)。wire_model_name 没配 served_model_name
        # 时原样返回, 老配置零行为变化。
        "model": wire_model_name(_model),
        "messages": messages,
        "stream": True,
        # [USAGE-ANCHOR 2026-09-03] 请求上游在流末回报真实 usage, 作为 token 预算的锚点。
        # 不带这个, vLLM/多数 OpenAI 兼容端默认流式**不回** usage, 框架只能纯估算。
        # 不认这个字段的上游会忽略它, 无副作用。
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens_override if max_tokens_override else _LIVE["max_tokens"],
        "temperature": 0.6,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    #  由 thinking_adapter 按 backend_type 注入正确参数
    _thinking_inject(body)

    headers = {"Authorization": f"Bearer {_key}", "Content-Type": "application/json"}
    last_err = None

    for attempt in range(max_retries + 1):
        try:
            timeout = httpx.Timeout(STREAM_TIMEOUT, connect=CONNECT_TIMEOUT)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{_url}/chat/completions",
                                         headers=headers, json=body) as resp:
                    if resp.status_code in RETRYABLE_CODES and attempt < max_retries:
                        body_text = await resp.aread()
                        # [#68] 统一走 lib.retry: exp backoff + jitter, cap 20s
                        log.warning(f"[transport] {resp.status_code} on attempt {attempt+1}, "
                                    f"retrying (exp backoff): {body_text[:200]}")
                        await sleep_backoff(attempt, LLM_POLICY)
                        continue

                    if resp.status_code != 200:
                        text = await resp.aread()
                        log.error(f"[transport] vLLM {resp.status_code}: {text[:300]}")
                        raise RuntimeError(f"vLLM returned {resp.status_code}: {text[:200]}")

                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line or not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            return
                        try:
                            yield json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                    return  # 正常结束

        except httpx.ConnectError as e:
            last_err = e
            if attempt < max_retries:
                log.warning(f"[transport] connect failed (attempt {attempt+1}), retrying (exp backoff): {e}")
                await sleep_backoff(attempt, LLM_POLICY)
            else:
                raise RuntimeError(f"vLLM unreachable after {max_retries+1} attempts: {e}")

        except httpx.ReadTimeout as e:
            last_err = e
            if attempt < max_retries:
                log.warning(f"[transport] read timeout (attempt {attempt+1}), retrying (exp backoff): {e}")
                await sleep_backoff(attempt, LLM_POLICY)
            else:
                raise RuntimeError(f"vLLM read timeout after {max_retries+1} attempts")

        except RuntimeError:
            raise  # 4xx 等不可重试错误直接抛出

        except Exception as e:
            last_err = e
            if attempt < max_retries:
                log.warning(f"[transport] unexpected error (attempt {attempt+1}), retrying (exp backoff): {e}")
                await sleep_backoff(attempt, LLM_POLICY)
            else:
                raise RuntimeError(f"vLLM call failed: {e}")

    raise RuntimeError(f"vLLM exhausted retries: {last_err}")


# ── 非流式调用（子代理、skills 等场景用）────────────────────────
async def vllm_call(
    messages: list,
    tools: Optional[list] = None,
    *,
    model: Optional[str] = None,
    backend_url: Optional[str] = None,
    api_key: Optional[str] = None,
    max_retries: int = MAX_RETRIES,
    timeout: float = 120,
) -> dict:
    """
    非流式调用，返回完整的 response dict。
    带同样的重试 + 退避逻辑。
    """
    _model = model or _LIVE["model_id"]
    _url   = (backend_url or _LIVE["backend_url"]).rstrip("/")
    _key   = api_key or _LIVE["api_key"]

    # [vision-route 2026-05-26] 与 vllm_stream 对齐 — 非视觉 + 含图 时路由 fallback
    _model, _url, _key, _ = _route_to_vision_fallback_if_needed(_model, _url, _key, messages)

    # [DeepSeek-compat 兜底 + tool-role 兜底 2026-05-25] 与 vllm_stream 对齐 (含分版本分流)
    _model_low = (_model or "").lower()
    if "deepseek-v4" in _model_low or "deepseek-chat" in _model_low or "deepseek-flash" in _model_low:
        for _m in messages:
            if _m.get("role") == "assistant" and "reasoning_content" not in _m:
                _m["reasoning_content"] = ""
    elif "deepseek-reasoner" in _model_low or "deepseek-r1" in _model_low:
        for _m in messages:
            if _m.get("role") == "assistant" and "reasoning_content" in _m:
                _m.pop("reasoning_content", None)
    # [vision-strip 同步覆盖 vllm_call 路径]
    if not _model_supports_vision(_model):
        messages = _strip_vision_from_messages(messages)
    messages = _sanitize_tool_message_chain(messages)

    body = {"model": wire_model_name(_model), "messages": messages, "max_tokens": _LIVE["max_tokens"], "temperature": 0.6}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {_key}", "Content-Type": "application/json"}

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(f"{_url}/chat/completions", headers=headers, json=body)
                if r.status_code in RETRYABLE_CODES and attempt < max_retries:
                    log.warning(f"[transport] {r.status_code} on non-stream attempt {attempt+1}, retrying (exp backoff)")
                    await sleep_backoff(attempt, LLM_POLICY)
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"vLLM {r.status_code}: {r.text[:200]}")
                return r.json()
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < max_retries:
                await sleep_backoff(attempt, LLM_POLICY)
            else:
                raise RuntimeError(f"vLLM unreachable: {e}")
    raise RuntimeError("vLLM exhausted retries")


# ── text-format tool call fallback (Qwen / Anthropic / OpenAI markdown) ──
_TOOL_CALL_RE  = re.compile(r'<tool_call>\s*\{(.+?)\}\s*</tool_call>', re.S)
_TOOL_USE_RE   = re.compile(r'<tool_use>\s*(\{.+?\})\s*</tool_use>', re.S)
_JSON_BLOCK_RE = re.compile(r'```(?:json)?\s*(\{[^`]*?"name"\s*:\s*"[^"]+"[^`]*?\})\s*```', re.S)

def parse_text_tool_calls(text: str) -> list:
    """解析 LLM 文本里的 tool_call.
    支持 3 种格式 (优先级: 原生 > Qwen > 通用 markdown):
    1. <tool_call>{...}</tool_call>  — Qwen
    2. <tool_use>{"name":"...","input":...}</tool_use>  — Anthropic 风格
    3. ```json\n{"name":"...","arguments":...}\n```  — OpenAI 通用
    """
    calls = []
    seen = set()

    def _add(name: str, args: dict):
        if not name:
            return
        key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False, default=str))
        if key in seen:
            return
        seen.add(key)
        calls.append({"name": name, "arguments": args})

    for m in _TOOL_CALL_RE.finditer(text):
        try:
            obj = json.loads("{" + m.group(1) + "}")
            _add(obj.get("name", ""), obj.get("arguments", {}))
        except Exception:
            pass

    for m in _TOOL_USE_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            _add(obj.get("name", ""), obj.get("input") or obj.get("arguments") or {})
        except Exception:
            pass

    for m in _JSON_BLOCK_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            _add(obj.get("name", ""), obj.get("arguments") or obj.get("input") or {})
        except Exception:
            pass

    return calls
