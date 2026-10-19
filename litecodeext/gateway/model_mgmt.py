"""gateway/model_mgmt.py — 多模型管理 (/v1/model/*).

model_switch 会 mutate litecode_server 模块的全局变量 (MODEL_ID / BACKEND_URL / ...)
且重建 DeepSearchTool + 触发 thinking_adapter update + clear read_cache.
"""
from __future__ import annotations

import json
import time

import httpx
from fastapi import APIRouter, HTTPException, Request

from lib.config import (
    HAS_DEEP_SEARCH, deep_search_tool, load_cfg, log,
    SEARCH_CFG as _SEARCH_CFG,
)

from ._deps import _srv, require_auth

router = APIRouter()


@router.get("/v1/model/list")
async def model_list(request: Request):
    require_auth(request)
    srv = _srv()
    cfg = load_cfg()
    models = cfg.get("models", [])
    # [sec 2026-10-19] 列表里的 api_key 等一律脱敏: 界面只按 id 操作 (切换/测试都在服务端
    # 现查配置取 key), 永远不需要明文 key。
    return {
        "active": srv.MODEL_ID,
        "active_backend": srv.BACKEND_URL,
        "active_backend_type": srv.BACKEND_TYPE,
        "models": mask_secrets(models),
    }


MASK_PREFIX = "••••"
_SECRET_KEYS = {"api_key", "apikey", "key", "secret", "token", "password", "access_token", "authorization"}


def _mask_value(v):
    if not isinstance(v, str) or not v:
        return v
    if v.startswith(MASK_PREFIX):
        return v
    return MASK_PREFIX + (v[-4:] if len(v) > 8 else "")


def mask_secrets(obj):
    """递归复制 obj, 把 api_key/secret/token/password 类字段换成 "••••" + 末 4 位 (短值整段遮掉)。"""
    if isinstance(obj, dict):
        return {k: (_mask_value(v) if str(k).lower() in _SECRET_KEYS and isinstance(v, str)
                    else mask_secrets(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_secrets(x) for x in obj]
    return obj


def is_masked(v) -> bool:
    return isinstance(v, str) and v.startswith(MASK_PREFIX)


@router.post("/v1/model/switch")
async def model_switch(request: Request):
    """热切换模型：更新 config.json 并重载全局变量"""
    require_auth(request)
    srv = _srv()
    body = await request.json()
    model_id = body.get("model_id", "")
    if not model_id:
        raise HTTPException(400, "model_id required")

    cfg = load_cfg()
    models = cfg.get("models", [])
    body_url = body.get("backend_url")
    target = None
    # 同名模型 (如 deepseek-v4-pro 在 deepseek.com 直连 + gether 网关下双 entry)
    # 必须按 (id, backend_url) 双键匹配, 否则 list 里第一条永远赢, 切不到另一条
    if body_url:
        for m in models:
            if m["id"] == model_id and m.get("backend_url") == body_url:
                target = m
                break
    if target is None:
        for m in models:
            if m["id"] == model_id:
                target = m
                break

    if target is None:
        # 如果不是预设模型，检查是否传了完整配置
        if body_url:
            target = body
        else:
            raise HTTPException(404, f"model {model_id} not found in models list")

    # 更新 config.json 中的 model 段
    cfg["model"] = {k: v for k, v in target.items() if k != "label"}
    _p = srv.BASE / "config.json"
    _p.write_text(json.dumps(cfg, ensure_ascii=False, indent=4))

    # [v1.0] 通过 config.reload_model 原子更新所有共享变量
    # 这样 transport.py / subagent.py 的 _LIVE dict 也会同步
    from lib.config import reload_model as _reload_model
    _reload_model(target)

    # 热更新 litecode_server 模块的全局变量（向后兼容直接引用）
    srv.MODEL_ID        = target["id"]
    srv.BACKEND_URL     = target.get("backend_url", srv.BACKEND_URL).rstrip("/")
    srv.API_KEY         = target.get("api_key", srv.API_KEY)
    srv.MAX_TOKENS      = target.get("max_tokens", srv.MAX_TOKENS)
    srv.ENABLE_THINKING = target.get("enable_thinking", srv.ENABLE_THINKING)
    srv.THINKING_BUDGET = target.get("thinking_budget", srv.THINKING_BUDGET)
    srv.CONTEXT_WINDOW  = int(target.get("context_window", srv.CONTEXT_WINDOW))
    srv.BACKEND_TYPE    = target.get("backend_type", srv.BACKEND_TYPE)

    # [v1.0] 切换模型时同步更新 thinking_adapter
    try:
        from lib.thinking_adapter import update as _ta_update
        _ta_update(target)
    except Exception:
        pass

    # [v1.1] 切换模型时重建 DeepSearchTool 实例 (它在 __init__ 时把 backend_url/api_key/
    # model_id 固化了, 不重建的话 web_search 会继续用旧 backend, 导致 401/404/超时)
    if HAS_DEEP_SEARCH and deep_search_tool:
        try:
            _ds_new = deep_search_tool(
                workspace=srv.WORKSPACE,
                backend_url=srv.BACKEND_URL,
                api_key=srv.API_KEY,
                model_id=srv.MODEL_ID,
                search_sources=_SEARCH_CFG.get("domestic", []),
            )
            srv._ds = _ds_new
            # 重新注入 tool_dispatch 的 _ds 引用
            try:
                import tool_dispatch as _td
                _td._ds = _ds_new
                _td.HAS_SEARCH = True
            except Exception as _re:
                log.warning(f"[MODEL-SWITCH] tool_dispatch 未能刷新 _ds: {_re}")
        except Exception as _dse:
            log.warning(f"[MODEL-SWITCH] rebuild DeepSearchTool failed: {_dse}")

    # [v1.1] 清理 tool_dispatch 的 read_cache, 避免不同模型/workspace 的缓存污染
    try:
        import tool_dispatch as _td
        _td._read_cache.clear()
    except Exception:
        pass

    log.info(f"\033[32m[MODEL-SWITCH] → {srv.MODEL_ID}  backend={srv.BACKEND_URL}  (DeepSearch rebuilt)\033[0m")

    # [FIX] 明确告知用户: 切换模型不会打断正在跑的 session。
    # 正在 agent_stream 里的迭代会在下一轮 LLM 调用时自然读到新的 MODEL_ID/BACKEND_URL。
    # body["abort_active"]=true 时 → 把所有活跃 session 标为中断, 彻底停下。
    _active_sids: list = []
    try:
        from lib.session import _session_locks, _slock_dict
        with _slock_dict:
            for _sid, _lck in list(_session_locks.items()):
                if _lck.locked():
                    _active_sids.append(_sid)
    except Exception:
        pass
    if _active_sids:
        _abort = bool(body.get("abort_active"))
        if _abort:
            from lib.session import _interrupt_flags, _interrupt_lock
            with _interrupt_lock:
                for _sid in _active_sids:
                    _interrupt_flags.add(_sid)
            log.warning(
                f"\033[33m[MODEL-SWITCH] 发送中断到 {len(_active_sids)} 个活跃 session: "
                f"{[s[:8] for s in _active_sids]}\033[0m"
            )
        else:
            log.info(
                f"\033[33m[MODEL-SWITCH] {len(_active_sids)} 个活跃 session 将在下一轮用新模型继续: "
                f"{[s[:8] for s in _active_sids]} (传 abort_active=true 可强制中断)\033[0m"
            )
    else:
        log.info(f"[MODEL-SWITCH] 当前无活跃 session")

    return {
        "ok": True,
        "model": srv.MODEL_ID,
        "backend_url": srv.BACKEND_URL,
        "backend_type": srv.BACKEND_TYPE,
        "context_window": srv.CONTEXT_WINDOW,
        "max_tokens": srv.MAX_TOKENS,
    }


@router.post("/v1/model/add")
async def model_add(request: Request):
    """添加新模型到预设列表"""
    require_auth(request)
    srv = _srv()
    body = await request.json()
    if not body.get("id") or not body.get("backend_url"):
        raise HTTPException(400, "id and backend_url required")

    cfg = load_cfg()
    models = cfg.setdefault("models", [])
    # 检查是否已存在
    for i, m in enumerate(models):
        if m["id"] == body["id"]:
            if is_masked(body.get("api_key")):   # 前端拿到的是脱敏值, 回传时保留原 key
                body["api_key"] = m.get("api_key", "")
            models[i] = body
            break
    else:
        models.append(body)

    _p = srv.BASE / "config.json"
    _p.write_text(json.dumps(cfg, ensure_ascii=False, indent=4))
    return {"ok": True, "count": len(models)}


# 1x1 白色 PNG, 视觉探测用
_TINY_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
MODEL_TEST_TIMEOUT = 30.0


def _test_target(body: dict) -> dict:
    """要测的模型: 按 model_id / id (+ backend_url) 在 config.models[] 里找;
    找不到但请求里带了 backend_url (设置页里还没保存的新模型) → 直接用请求里的字段。
    [fix 2026-10-19] 旧版只认 body.id 且缺省回落"当前激活模型", 传 model_id 时测的其实是激活模型。"""
    from lib.config import find_model_entry
    mid = str(body.get("model_id") or body.get("id") or "").strip()
    url = str(body.get("backend_url") or "").strip()
    entry = find_model_entry(mid, url) if mid else {}
    if entry:
        # [sec 2026-10-19] 配置里的模型: backend_url / api_key 只取配置值, 请求里传的一律不用
        # (否则可借已知 id 把服务端 key 发到任意地址)。
        return dict(entry)
    if mid and url:
        # 未保存的新模型: 只用请求里自己带的 key, 绝不回落服务端的 API_KEY (防 key 外泄 / SSRF 带凭据)
        key = body.get("api_key")
        key = "" if (not isinstance(key, str) or is_masked(key)) else key
        return dict(body, id=mid, api_key=key)
    return {}


@router.post("/v1/model/test")
async def model_test(request: Request):
    """测试指定模型是否可用: 直连它自己的 backend + api_key + 线路名发一条极短对话 (关思考),
    返回 {ok, model_id, latency_ms, reply_preview, error}。只读, 不切换激活模型。"""
    require_auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    target = _test_target(body)
    mid = str(body.get("model_id") or body.get("id") or "")
    if not target:
        return {"ok": False, "model_id": mid, "latency_ms": 0, "reply_preview": "",
                "error": f"模型 {mid or '(未指定)'} 不在配置里" if mid else "model_id required"}

    from lib.config import model_live_view, reset_turn_model, set_turn_model
    from lib.thinking_adapter import inject_params
    view = model_live_view(target)
    url = view["backend_url"].rstrip("/")
    hdrs = {"Content-Type": "application/json"}
    if view["api_key"]:
        hdrs["Authorization"] = f"Bearer {view['api_key']}"
    payload = {"model": view["served_model_name"], "stream": False, "max_tokens": 16,
               "messages": [{"role": "user", "content": "ping, reply with: pong"}]}
    # 按被测模型自己的线路格式关思考 (vLLM chat_template_kwargs / DeepSeek thinking=disabled ...)。
    # set_turn_model 只影响当前 context, 不碰全局激活模型。
    tok = set_turn_model(target)
    try:
        inject_params(payload, force_disable=True)
    except Exception:
        pass
    finally:
        reset_turn_model(tok)

    result: dict = {"ok": False, "model_id": view["model_id"], "backend_url": url,
                    "backend_type": view["backend_type"], "latency_ms": 0,
                    "reply_preview": "", "error": "",
                    "supports_vision": view["supports_vision"]}
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(MODEL_TEST_TIMEOUT, connect=10.0)) as client:
            r = await client.post(f"{url}/chat/completions", headers=hdrs, json=payload)
            result["latency_ms"] = int((time.time() - t0) * 1000)
            result["http_status"] = r.status_code
            if r.status_code == 200:
                try:
                    msg = (r.json().get("choices") or [{}])[0].get("message") or {}
                except Exception:
                    msg = {}
                reply = msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or ""
                result["reply_preview"] = str(reply).strip()[:120]
                result["ok"] = True
                result["completion_test"] = "pass"
            else:
                result["error"] = f"HTTP {r.status_code}: {r.text[:200]}"
                result["completion_test"] = f"HTTP {r.status_code}"
            if body.get("test_vision") and result["ok"]:
                vp = {"model": view["served_model_name"], "max_tokens": 1, "stream": False,
                      "messages": [{"role": "user", "content": [
                          {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_TINY_PNG}"}},
                          {"type": "text", "text": "hi"}]}]}
                try:
                    r3 = await client.post(f"{url}/chat/completions", headers=hdrs, json=vp)
                    result["vision_test"] = "pass" if r3.status_code == 200 else f"HTTP {r3.status_code}"
                except Exception as ve:
                    result["vision_test"] = str(ve)[:80]
    except Exception as e:
        result["error"] = (f"{type(e).__name__}: {e}")[:200]
        result["latency_ms"] = int((time.time() - t0) * 1000)

    log.info(f"[MODEL-TEST] {view['model_id']} @ {url} → ok={result['ok']} {result['latency_ms']}ms")
    return result


@router.delete("/v1/model/{model_id}")
async def model_delete(model_id: str, request: Request):
    """从预设列表删除模型"""
    require_auth(request)
    srv = _srv()
    cfg = load_cfg()
    models = cfg.get("models", [])
    cfg["models"] = [m for m in models if m["id"] != model_id]
    _p = srv.BASE / "config.json"
    _p.write_text(json.dumps(cfg, ensure_ascii=False, indent=4))
    return {"ok": True}
