"""config_router.py — 读 / 编辑 config.json 非敏感字段.

跟原 web_ui.py 实现完全兼容 (前端依赖现有 schema):
- GET 返回 {ok, config, editable_sections}
- PATCH 接受 {section1: {...}, ...} 顶级, 跳过 •••• 脱敏占位符
"""
import copy as _copy
import json

from fastapi import APIRouter, HTTPException, Request

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()


_SENSITIVE_FIELDS = {
    ("server", "token"),
    ("web_ui", "auth", "password"),
}
_EDITABLE_SECTIONS = {"agent", "memory", "wechat", "search", "iteration_trace", "web_ui",
                      # [2026-05-22] 加 model: 让用户能开关 enable_thinking / 改 max_tokens / context_window 等
                      # api_key 敏感字段已被 _mask_sensitive 脱敏, PATCH 时占位符 ••••xxx 不会覆盖真值
                      "model"}


def _mask_sensitive(cfg: dict) -> dict:
    out = _copy.deepcopy(cfg)
    srv = out.get("server", {})
    if srv.get("token"):
        srv["token"] = "••••" + srv["token"][-4:]
    mdl = out.get("model", {}) or {}
    if mdl.get("api_key") and mdl["api_key"] not in ("", "EMPTY"):
        mdl["api_key"] = "••••" + mdl["api_key"][-4:]
    for m in out.get("models", []) or []:
        if m.get("api_key") and m["api_key"] not in ("", "EMPTY"):
            m["api_key"] = "••••" + m["api_key"][-4:]
    wa = (out.get("web_ui", {}) or {}).get("auth", {})
    if wa.get("password"):
        wa["password"] = "••••••••"
    return out


@router.get("/api/config")
async def api_config_get(request: Request):
    """读当前 config.json (敏感字段脱敏)."""
    require_auth(request)
    try:
        cfg = json.loads(S.cfg_path.read_text())
        return {
            "ok": True,
            "config": _mask_sensitive(cfg),
            "editable_sections": sorted(_EDITABLE_SECTIONS),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.patch("/api/config")
async def api_config_patch(request: Request):
    """浅合并更新 config.json 的白名单字段.
    请求体: {"agent": {"task_timeout_seconds": 1800}, "memory": {...}, ...}
    - 只接受 _EDITABLE_SECTIONS 里的顶层段
    - 脱敏占位符 (••••xxx) 会被保留为原值, 不覆盖
    - 写入后不热加载 agent/memory 配置 (需要重启服务生效, 前端提示)
    """
    require_auth(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be object")
    try:
        cfg = json.loads(S.cfg_path.read_text())
    except Exception as e:
        raise HTTPException(500, f"read config failed: {e}")

    applied = {}
    rejected = []
    for section, new_val in body.items():
        if section not in _EDITABLE_SECTIONS:
            rejected.append(section)
            continue
        if not isinstance(new_val, dict):
            rejected.append(section)
            continue
        old = cfg.get(section, {}) or {}
        merged = dict(old)
        for k, v in new_val.items():
            # 跳过脱敏占位符
            if isinstance(v, str) and v.startswith("••••"):
                continue
            merged[k] = v
        cfg[section] = merged
        applied[section] = merged

    # [migrate-2026-05] bind-mount inode 友好: 用 truncate-then-write, 不 rename
    # 避免容器内 /opt/litecode/config.json 跟 host /opt/litecode/config.json 失联
    try:
        S.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=4))
    except Exception as e:
        raise HTTPException(500, f"write config failed: {e}")

    # [hot-reload 2026-05-22] model section 改了 → 立即调 reload_model 同步 _LIVE
    # 否则 transport 还读旧 enable_thinking, 用户 toggle 后实测无效, 误导"已立即生效"提示
    model_reloaded = False
    if "model" in applied:
        try:
            from lib.config import reload_model
            reload_model(cfg["model"])
            model_reloaded = True
        except Exception as _re:
            import logging; logging.getLogger("openclaw").warning(f"  [config.reload_model] fail: {_re}")

    # agent/memory/iteration_trace 这些没有热 reload 路径, 仍需 server 重启
    needs_restart = any(s in applied for s in ("agent", "memory", "iteration_trace"))
    if "model" in applied and not model_reloaded:
        needs_restart = True   # 热 reload 失败, 退化成 "需重启"
    return {
        "ok": True,
        "applied": applied,
        "rejected": rejected,
        "needs_restart": needs_restart,
        "model_reloaded": model_reloaded,
        "hint": (
            "部分字段需要重启 server 才生效" if needs_restart else
            ("model 已热 reload (推理参数立即生效)" if model_reloaded else "已立即生效")
        ),
    }
