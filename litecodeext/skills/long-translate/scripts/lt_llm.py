"""long-translate: 读 LiteCode 模型配置 + OpenAI 兼容 /chat/completions 调用 (重试 / 用量累计)。

独立脚本, 不 import LiteCode 的 lib (那边 import 时会加载整套服务配置)。配置读法与 lib/config.py 一致:
- config.json 位置: --config > 环境变量 LITECODE_CONFIG > 容器 /opt/litecode/config.json > 仓库根 config.json
- 模型: --model 指定 id 时在 models[] 里找, 否则用 config["model"] (当前激活模型)
- 发给上游的模型名: served_model_name 优先, 否则 id; api_key 为空时用 "EMPTY"
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
LITECODE_ROOT = HERE.parent.parent.parent          # litecodeext/ (容器里 /opt/litecode)


def find_config(explicit: str = "") -> Path:
    cands = [explicit, os.environ.get("LITECODE_CONFIG", "")]
    # 宿主开发布局: /opt/litecode/config.json 才是生产配置 (挂进容器), litecodeext/config.json 是旧模板
    if (LITECODE_ROOT.parent / "litecodeext").resolve() == LITECODE_ROOT.resolve():
        cands.append(str(LITECODE_ROOT.parent / "config.json"))
    cands += [str(LITECODE_ROOT / "config.json"), "/opt/litecode/config.json"]
    for c in cands:
        if c and Path(c).is_file():
            return Path(c)
    raise SystemExit("找不到 config.json (可用 --config 或 LITECODE_CONFIG 指定)")


def load_model(config: str = "", model_id: str = "") -> dict:
    """返回 {id, wire, url, key, context_window, max_tokens, deployment}; 不打印 key。"""
    cfg = json.loads(find_config(config).read_text(encoding="utf-8"))
    ent = None
    if model_id:
        for m in cfg.get("models") or []:
            if isinstance(m, dict) and m.get("id") == model_id and m.get("backend_url"):
                ent = m
                break
        if ent is None:
            raise SystemExit(f"config.models 里没有模型 {model_id!r}")
    else:
        ent = cfg.get("model") or {}
        if not ent.get("backend_url"):           # 兜底: default_model_id
            did = cfg.get("default_model_id")
            ent = next((m for m in cfg.get("models") or [] if m.get("id") == did and m.get("backend_url")), {})
    if not ent.get("backend_url"):
        raise SystemExit("config.json 里没有可用模型 (model.backend_url 为空)")
    return {
        "id": ent.get("id", ""),
        "wire": str(ent.get("served_model_name") or "").strip() or ent.get("id", ""),
        "url": str(ent["backend_url"]).rstrip("/"),
        "key": str(ent.get("api_key") or "").strip() or "EMPTY",
        "context_window": int(ent.get("context_window") or 32000),
        "max_tokens": int(ent.get("max_tokens") or 8192),
        "deployment": str(ent.get("deployment") or "").lower(),
    }


class Usage:
    """线程安全的 token 累计。"""

    def __init__(self, prompt=0, completion=0, reasoning=0, calls=0):
        self.lock = threading.Lock()
        self.prompt, self.completion, self.reasoning, self.calls = prompt, completion, reasoning, calls

    def add(self, u: dict):
        with self.lock:
            self.prompt += int(u.get("prompt_tokens") or 0)
            self.completion += int(u.get("completion_tokens") or 0)
            self.reasoning += int(u.get("reasoning_tokens") or 0)
            self.calls += 1

    def as_dict(self):
        return {"prompt": self.prompt, "completion": self.completion,
                "reasoning": self.reasoning, "calls": self.calls}


class LLMError(RuntimeError):
    pass


_THINK_RE = re.compile(r"^.*?</think>\s*", re.S)


class LLM:
    """最小 OpenAI 兼容客户端。chat() 返回 (content, finish_reason, usage_dict)。

    thinking: "low"  = 发 reasoning_effort=low (默认)。翻译不需要长思考: GLM-5.3-Flash 实测同一块
                       默认思考 3191 token/9.3s, low 只要 358 token/2.6s, 译文质量相当;
                       上游不认这个参数 (HTTP 400) 时自动去掉重试, 并在本客户端里记住。
              "auto" = 不发任何思考开关 (模型默认);
              "off"  = 发 chat_template_kwargs.enable_thinking=false (Qwen3 类有效;
                       GLM-5.3 实测会让思考漏进正文, 别用)。
    """

    def __init__(self, model: dict, timeout: float = 600, retries: int = 4, thinking: str = "low",
                 temperature: float | None = None, transport=None):
        self.m = model
        self.retries = retries
        self.thinking = thinking
        self.temperature = temperature
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=30), transport=transport)

    def chat(self, messages: list, max_tokens: int = 4096) -> tuple[str, str, dict]:
        body = {"model": self.m["wire"], "messages": messages, "max_tokens": max_tokens, "stream": False}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.thinking == "off":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        elif self.thinking == "low":
            body["reasoning_effort"] = "low"
        headers = {"Authorization": f"Bearer {self.m['key']}", "Content-Type": "application/json"}
        last = ""
        for attempt in range(self.retries + 1):
            try:
                r = self.client.post(self.m["url"] + "/chat/completions", json=body, headers=headers)
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    raise LLMError(last)
                if r.status_code == 400 and "reasoning_effort" in body and "reasoning" in r.text.lower():
                    body.pop("reasoning_effort")           # 上游不支持 → 去掉重发
                    self.thinking = "auto"
                    continue
                if r.status_code >= 400:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")   # 4xx 不重试
                j = r.json()
                ch = (j.get("choices") or [{}])[0]
                msg = ch.get("message") or {}
                content = msg.get("content") or ""
                if "</think>" in content:                  # 嵌入式思考: 只要 </think> 之后
                    content = _THINK_RE.sub("", content, count=1)
                u = dict(j.get("usage") or {})
                u["reasoning_tokens"] = int(((u.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
                return content.strip(), ch.get("finish_reason") or "", u
            except (httpx.TransportError, LLMError, ValueError) as e:
                last = str(e) or type(e).__name__
                if isinstance(e, LLMError) and last.startswith("HTTP 4") and not last.startswith("HTTP 429"):
                    raise
                if attempt >= self.retries:
                    break
                time.sleep(min(60, 2 ** attempt * 2) + random.random())
        raise LLMError(f"重试 {self.retries} 次仍失败: {last}")

    def close(self):
        self.client.close()
