"""lib/scrub.py — 导出/分享前脱敏 (P0-#16).

覆盖 5 类明文秘钥/凭据:
    - OpenAI/Anthropic 风格: sk-xxx, sk-ant-xxx (≥20 char body)
    - Bearer token: `Bearer <20+>` / `bearer <20+>`
    - Basic auth: `Basic <base64>` (≥16 char)
    - SSH 路径: ~/.ssh/... / /root/.ssh/... / /home/<u>/.ssh/...
    - .env 常见 KEY=value (KEY 匹配 API_KEY / TOKEN / SECRET / PASSWORD / PASSWD)

每处替换成 `<REDACTED:kind>` 便于人眼审阅.
"""
from __future__ import annotations
import re
from typing import Tuple, List

_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}\b"), "<REDACTED:api-key>"),
    (re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{20,}\b"), "<REDACTED:bearer>"),
    (re.compile(r"\b[Bb]asic\s+[A-Za-z0-9+/=]{16,}\b"), "<REDACTED:basic>"),
    (re.compile(r"(?:~|/root|/home/[^/\s]+)/\.ssh/[^\s\"'\)]+"), "<REDACTED:ssh-path>"),
    (re.compile(r"\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD))\s*=\s*[^\s\"']{6,}",
                re.IGNORECASE), r"\1=<REDACTED:env-value>"),
]


def scrub_secrets(text: str) -> str:
    """输入任意字符串, 返回脱敏后副本. 非 str 原样返回."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pat, sub in _PATTERNS:
        out = pat.sub(sub, out)
    return out


def scrub_session(session: dict) -> dict:
    """脱敏 session 字典副本 (不改原对象). 只扫 messages[*].content."""
    if not isinstance(session, dict):
        return session
    d = dict(session)
    msgs = d.get("messages") or []
    d["messages"] = [
        {**m, "content": scrub_secrets(m.get("content", ""))} if isinstance(m, dict) else m
        for m in msgs
    ]
    return d
