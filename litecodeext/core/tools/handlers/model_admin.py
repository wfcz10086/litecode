"""handlers/model_admin.py — manage_models: 对话里管理大模型配置 (实现见 core/model_admin.py)。"""
from __future__ import annotations

from . import register


@register("manage_models")
async def h_manage_models(sid: str, args: dict):
    from core import model_admin
    try:
        return await model_admin.run(args or {}), None
    except Exception as e:   # 兜底也要脱敏: 异常文本里可能带请求参数
        return f"ERROR: manage_models {type(e).__name__}: {model_admin.redact(e, (args or {}).get('api_key'))}", None
