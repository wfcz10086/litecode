#!/usr/bin/env python3
"""V6.5 smoke: 3 官方 API (Kimi K2.7 / GLM-5.2 / Qwen3.7-Max).

对每个模型:
  1. non-stream chat -> 拿到 content
  2. stream chat with thinking=high -> 期待 thinking + content 都有
  3. 打印 usage/reasoning 字段, 便于人眼校验

任何一家失败, exit 1.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# eager side-effect imports so registry is populated
from litecodeext.v3.providers import llm as _llm_pkg  # noqa: F401,E402
from litecodeext.v3.providers.base import (  # noqa: E402
    ThinkingSpec, UnifiedMessage, UnifiedRequest,
)
from litecodeext.v3.config_bridge import (  # noqa: E402
    build_provider, list_models, load_raw_config,
)

OFFICIAL_IDS = ["kimi-k2.7-code", "glm-5.2", "qwen3.7-max", "deepseek-chat"]


async def run_one(model_id: str) -> tuple[bool, str]:
    cfg = load_raw_config()
    ids = {m.get("id") for m in list_models(cfg)}
    if model_id not in ids:
        return False, f"config.json 中找不到 model id={model_id}"

    try:
        prov = build_provider(model_id, cfg)
    except Exception as e:
        return False, f"build_provider 失败: {e}"

    msgs = [UnifiedMessage(role="user", content="你是谁？请一句话中文自我介绍。")]

    # -- pass 1: non-stream (thinking off) ---------------------------------
    req = UnifiedRequest(
        messages=msgs, model=model_id, stream=False,
        max_tokens=256, temperature=0.2,
        thinking=ThinkingSpec(effort="off"),
    )
    content = ""
    try:
        async for ch in prov.stream(req):
            if ch.kind == "content" and ch.delta:
                content += ch.delta
            if ch.kind == "error":
                return False, f"non-stream 错误: {ch.delta}"
    except Exception as e:
        return False, f"non-stream 异常: {e}"
    if not content.strip():
        return False, "non-stream 返回空 content"
    print(f"  [{model_id}] non-stream OK: {content[:80]}...")

    # -- pass 2: stream + thinking high -----------------------------------
    req2 = UnifiedRequest(
        messages=[UnifiedMessage(role="user", content="9.11 和 9.9 哪个大? 一步步推理然后给出结论。")],
        model=model_id, stream=True,
        max_tokens=2048, temperature=0.2,
        thinking=ThinkingSpec(effort="high"),
    )
    got_content = False
    got_thinking = False
    got_done = False
    content_buf = ""
    thinking_buf = ""
    err = None
    try:
        async for ch in prov.stream(req2):
            if ch.kind == "content" and ch.delta:
                got_content = True
                content_buf += ch.delta
            if ch.kind == "thinking" and ch.delta:
                got_thinking = True
                thinking_buf += ch.delta
            if ch.kind == "done":
                got_done = True
            if ch.kind == "error":
                err = ch.delta
                break
    except Exception as e:
        return False, f"stream 异常: {e}"
    if thinking_buf:
        print(f"  [{model_id}] thinking snippet: {thinking_buf[:100]!r}...")
    if content_buf:
        print(f"  [{model_id}] content snippet:  {content_buf[:100]!r}...")
    if err:
        return False, f"stream 错误: {err}"
    if not got_content:
        return False, "stream 未产生 content"
    if not got_done:
        return False, "stream 未收到 done"
    thinking_note = "有 thinking" if got_thinking else "无 thinking (可能该模型该请求默认关)"
    print(f"  [{model_id}] stream OK: {thinking_note}")
    return True, "ok"


async def main() -> int:
    print("=== V6.5 smoke: 官方 API 三家 ===")
    results = []
    for mid in OFFICIAL_IDS:
        print(f"\n-- {mid} --")
        ok, msg = await run_one(mid)
        results.append((mid, ok, msg))
        if not ok:
            print(f"  FAIL: {msg}")

    print("\n=== 汇总 ===")
    passed = sum(1 for _, ok, _ in results if ok)
    for mid, ok, msg in results:
        print(f"  {'OK' if ok else 'FAIL'}  {mid}  {msg if not ok else ''}")
    print(f"\n{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
