"""
vision_check.py — v2.0 #10 全场景 vision_ocr 自检 helper
=========================================================
任何 e2e 测试每步留 jpg + 调 vision_ocr 让 LLM 判断符合预期.
失败时写 vision_*.json 记录 LLM 的判断和理由 + 标红.

用法:
    from tests.web.vision_check import vision_assert, snap_then_check

    # 已有截图直接判断
    vision_assert("/path/login.png",
                  expect="登录后主界面, 见会话列表和 chat",
                  test_id="01_login_after")

    # 一步到位: Playwright 截图 + vision 判断
    snap_then_check(page, "02_dag_panel",
                    expect="DAG 编辑器面板打开, 见 canvas + 节点工具栏")

输出落地:
- 截图: tests/web/snapshots/<test_id>.png
- 判断: tests/web/snapshots/vision_<test_id>.json
        {pass, reason, expect, jpg, model, latency_ms, raw}

vision 不可用 (无 fallback / 路由失败): 降级 WARN, 不 FAIL 整个测试.
"""
from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import urllib.request
import urllib.error

SERVER_URL = os.environ.get("LITECODE_SERVER_URL", "http://127.0.0.1:18789")
SERVER_TOKEN = os.environ.get("SERVER_TOKEN", "CHANGE_ME_TOKEN")
SNAPSHOTS_DIR = Path(__file__).resolve().parent / "snapshots"
SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


_JSON_RE = re.compile(r"\{[\s\S]*?\}")


def _parse_vision_json(raw: str) -> dict:
    """从 LLM 返回里抠 {pass/login_ok: bool, reason: str}.

    支持: 纯 JSON / ```json ... ``` / 文本里带 JSON / 兼容 pass/login_ok/ok 三键."""
    if not raw:
        return {"pass": None, "reason": "empty response"}
    raw = raw.strip()
    # 去 ```json 围栏
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    # 找第一个 {...}
    m = _JSON_RE.search(raw)
    if not m:
        return {"pass": None, "reason": f"no JSON in response: {raw[:200]}"}
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return {"pass": None, "reason": f"JSON parse error: {e}"}
    # 兼容三种键名
    for k in ("pass", "login_ok", "ok", "match"):
        if k in d:
            d["pass"] = bool(d[k])
            break
    if "reason" not in d:
        d["reason"] = d.get("explanation") or d.get("note") or ""
    return d


_VISION_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}


def _call_vision_ocr(jpg_path: str, prompt: str, timeout: int = 60) -> tuple[Optional[str], int]:
    """发图给 server /v1/chat/completions (server 内 vision-route 自动 fallback 到 vision 模型).

    把 jpg 读出来 base64 + data URL 直发 — 不走 file:// (远程 backend 不接受).
    返回 (raw_response, latency_ms). raw 以 "ERROR:" 开头表示请求失败.
    """
    import base64
    t0 = time.time()
    p = Path(jpg_path)
    ext = p.suffix.lower()
    mime = _VISION_MIME.get(ext, "image/png")
    try:
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError as e:
        return f"ERROR: read jpg failed: {e}", 0
    data_url = f"data:{mime};base64,{b64}"
    body = {
        "model": "deepseek-v4-pro",  # 主模型, server 内 vision-route 自动 fallback
        "stream": False,
        "messages": [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": prompt}
            ]}
        ],
    }
    try:
        req = urllib.request.Request(
            f"{SERVER_URL}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {SERVER_TOKEN}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        latency = int((time.time() - t0) * 1000)
        try:
            content = d["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            content = json.dumps(d)[:500]
        return content, latency
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError) as e:
        return f"ERROR: {type(e).__name__}: {e}", int((time.time() - t0) * 1000)


def vision_assert(jpg_path: str, expect: str, test_id: str = "",
                  hard: bool = False, timeout: int = 120) -> dict:
    """对截图发 vision_ocr 判断. 写 vision_<test_id>.json + 返回 dict.

    Args:
        jpg_path: 截图绝对路径
        expect:   预期描述 (自然语言, 一句话)
        test_id:  测试 id, 用于 vision_*.json 命名
        hard:     True = 失败 raise AssertionError; False = 只 WARN 返回
        timeout:  HTTP 超时秒

    Returns:
        {pass: bool|None, reason: str, jpg: str, expect: str, latency_ms: int,
         raw: str, test_id: str}
    """
    p = Path(jpg_path)
    if not p.exists():
        result = {"pass": None, "reason": f"截图不存在: {jpg_path}",
                  "jpg": str(p), "expect": expect, "test_id": test_id,
                  "latency_ms": 0, "raw": ""}
        if hard:
            raise AssertionError(result["reason"])
        return result

    prompt = (
        f"判断这张截图是否符合下述预期, 用 JSON 回复:\n"
        f"  {{\"pass\": true|false, \"reason\": 简短理由 ≤ 80 字}}\n\n"
        f"预期: {expect}"
    )
    raw, latency = _call_vision_ocr(jpg_path, prompt, timeout=timeout)
    parsed = _parse_vision_json(raw or "")
    result = {
        "pass": parsed.get("pass"),
        "reason": parsed.get("reason") or "",
        "jpg": str(p), "expect": expect, "test_id": test_id,
        "latency_ms": latency, "raw": (raw or "")[:1500],
    }
    if test_id:
        json_path = SNAPSHOTS_DIR / f"vision_{test_id}.json"
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        result["vision_json"] = str(json_path)
    if hard and result["pass"] is not True:
        raise AssertionError(
            f"[vision_assert FAIL] {test_id or 'unnamed'}: "
            f"{result['reason'] or 'pass=' + str(result['pass'])} "
            f"(jpg={result['jpg']})"
        )
    return result


def snap_then_check(page, test_id: str, expect: str, hard: bool = False) -> dict:
    """Playwright page → 截图 → vision_assert 一步到位.

    Args:
        page:    playwright sync_api Page
        test_id: 用于 <id>.png + vision_<id>.json 命名
        expect:  预期描述
        hard:    True = pass!=True raise
    """
    jpg = SNAPSHOTS_DIR / f"{test_id}.png"
    try:
        page.screenshot(path=str(jpg), timeout=5000, animations="disabled",
                        caret="hide")
    except Exception as e:
        result = {"pass": None, "reason": f"screenshot failed: {e}",
                  "jpg": str(jpg), "expect": expect, "test_id": test_id,
                  "latency_ms": 0, "raw": ""}
        if hard:
            raise AssertionError(result["reason"])
        return result
    return vision_assert(str(jpg), expect, test_id=test_id, hard=hard)
