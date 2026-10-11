"""side_model.py — 旁路 LLM 调用 (记忆提取 / 压缩等) 的模型路由。

ollama (llama.cpp) 每个并发槽只缓存最近一次请求的 KV 前缀。旁路请求若与主对话
共用同一模型, 会把主对话 ~5 万 token 的前缀缓存挤掉, 下一轮整段重新 prefill。
config.json 里配置:

    "side_models": {"memory": "Qwen3.6-35B"}

值为 models 列表中的 id; 未配置、找不到或读配置失败 → 原样返回调用方的主模型三元组。
"""
from __future__ import annotations

import json

from lib.config import BASE


def resolve(purpose: str, url: str, model_id: str, api_key: str) -> tuple[str, str, str]:
    """返回 (url, model_id, api_key)。"""
    try:
        cfg = json.loads((BASE / "config.json").read_text())
    except Exception:
        return url, model_id, api_key
    target = (cfg.get("side_models") or {}).get(purpose)
    if not target:
        return url, model_id, api_key
    for m in cfg.get("models", []):
        if m.get("id") == target and m.get("backend_url"):
            return m["backend_url"], m["id"], m.get("api_key") or api_key
    return url, model_id, api_key
