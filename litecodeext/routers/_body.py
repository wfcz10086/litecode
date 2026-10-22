"""routers/_body.py — 请求体解析 helper (不是 router, discover() 只收 *_router 模块).

[ui-audit 2026-10-21] 原来各 handler 直接 `await request.json()`:
- 非法 JSON → JSONDecodeError → 500 Internal Server Error
- 合法但不是对象 (如 `[]` / `"x"` / `null`) → 后续 body.get(...) AttributeError → 500
统一换成 json_object(): 两种情况都回 400 + 中文说明, 前端能直接展示。
"""
import json

from fastapi import HTTPException, Request


async def json_object(request: Request) -> dict:
    """读请求体为 JSON 对象; 不合法 → HTTPException(400)。"""
    raw = await request.body()
    if not raw.strip():
        raise HTTPException(400, "请求体为空, 需要 JSON 对象")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "请求体不是合法 JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "请求体必须是 JSON 对象")
    return body
