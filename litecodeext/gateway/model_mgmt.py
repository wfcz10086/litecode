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
    return {
        "active": srv.MODEL_ID,
        "active_backend": srv.BACKEND_URL,
        "active_backend_type": srv.BACKEND_TYPE,
        "models": models,
    }


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
            models[i] = body
            break
    else:
        models.append(body)

    _p = srv.BASE / "config.json"
    _p.write_text(json.dumps(cfg, ensure_ascii=False, indent=4))
    return {"ok": True, "count": len(models)}


@router.post("/v1/model/test")
async def model_test(request: Request):
    """测试模型后端连通性：尝试调 /models 或发一条极短 completion"""
    require_auth(request)
    srv = _srv()
    body = await request.json()
    url = (body.get("backend_url") or srv.BACKEND_URL).rstrip("/")
    key = body.get("api_key") or srv.API_KEY
    mid = body.get("id") or srv.MODEL_ID
    btype = body.get("backend_type", "openai")

    hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    result: dict = {"ok": False, "backend_url": url, "model_id": mid, "backend_type": btype,
                    "supports_vision": False, "latency_ms": 0}

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            # 1) 尝试 GET /models (vLLM / OpenAI / ollama 都支持)
            models_url = f"{url}/models"
            r = await client.get(models_url, headers=hdrs)
            if r.status_code == 200:
                data = r.json()
                model_list_data = data.get("data", []) if isinstance(data, dict) else []
                model_ids = [m.get("id", "") for m in model_list_data] if model_list_data else []
                result["available_models"] = model_ids[:10]
                result["ok"] = True
            else:
                result["error"] = f"GET /models → HTTP {r.status_code}"

            # 2) 快速 completion 测试（1 token）
            test_payload = {
                "model": mid, "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1, "stream": False,
            }
            r2 = await client.post(f"{url}/chat/completions", headers=hdrs, json=test_payload)
            result["latency_ms"] = int((time.time() - t0) * 1000)
            if r2.status_code == 200:
                result["ok"] = True
                result["completion_test"] = "pass"
            else:
                result["completion_test"] = f"HTTP {r2.status_code}"

            # 3) 检测视觉支持：发带图片的消息看是否报错
            if body.get("supports_vision") or btype == "vllm":
                try:
                    # 1x1 白色 PNG base64
                    tiny_img = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
                    vision_payload = {
                        "model": mid, "max_tokens": 1, "stream": False,
                        "messages": [{"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{tiny_img}"}},
                            {"type": "text", "text": "hi"}
                        ]}],
                    }
                    r3 = await client.post(f"{url}/chat/completions", headers=hdrs, json=vision_payload)
                    result["supports_vision"] = r3.status_code == 200
                    result["vision_test"] = "pass" if r3.status_code == 200 else f"HTTP {r3.status_code}"
                except Exception as ve:
                    result["vision_test"] = str(ve)[:80]

    except Exception as e:
        result["error"] = str(e)[:200]
        result["latency_ms"] = int((time.time() - t0) * 1000)

    log.info(f"[MODEL-TEST] {mid} @ {url} → ok={result['ok']} {result.get('latency_ms',0)}ms")
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
