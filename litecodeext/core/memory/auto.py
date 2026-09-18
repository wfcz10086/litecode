"""memory/auto.py — 每轮结束后自动记忆更新.

三条主路径:
  1. rule_based_memory_update  — 无 LLM 依赖, 提取 user intent / question log /
                                 completed files / errors / key paths, 必执行
  2. smart_auto_memory         — 上面基础上再问 LLM 是否有跨 session 价值信息
  3. rule_capture_user_facts   — 从新 user 消息里捕获 SSH/host/preference/vault,
                                 写 L1 + L3 索引 (跨 session 检索)
"""
from __future__ import annotations

import json
import re as _re
import time
from typing import TYPE_CHECKING

import httpx

from .consts import _CFG_MEM, SMART_MEMORY_PROMPT


# [wire-name 2026-08-28] config 的 `id` 是显示名, 上游认的可能是别的名字
# (自建 vLLM 的 --served-model-name)。本模块直接打上游 /chat/completions,
# 不经过 lib.transport, 所以要自己翻译 —— 否则会 404
# ("The model `xxx-local` does not exist", 实测撞过)。
def _wire(mid: str) -> str:
    try:
        from lib.config import wire_model_name
        return wire_model_name(mid)
    except Exception:
        return mid

if TYPE_CHECKING:
    from .manager import MemoryManager


# ── smart_auto_memory ────────────────────────────────────────────────
def smart_auto_memory(
    vllm_url: str,
    model_id: str,
    api_key: str,
    messages: list,
    manager: "MemoryManager",
    max_recent: int = 10,
) -> bool:
    """[v1.0] 每轮结束后更新记忆.
    策略: rule_based 是主路径(必执行), LLM 判断是补充(尝试执行).
    """
    import logging
    log = logging.getLogger("openclaw")

    # 第一步: 无条件执行规则提取 (不依赖 LLM)
    rule_updated = rule_based_memory_update(manager, messages)

    # [v1.9 P37-a] OFFLINE 模式跳过 LLM 调用
    import os as _os
    if _os.environ.get("LITECODE_TEST_OFFLINE") or _os.environ.get("OPENCLAW_TEST_OFFLINE"):
        log.debug("  [smart-memory] OFFLINE mode → skip LLM, return rule_updated")
        return rule_updated

    # 第二步: 尝试 LLM 判断是否有更深层的记忆价值 (可选, 失败不影响)
    recent = messages[-max_recent:] if len(messages) > max_recent else messages
    snippet_parts: list = []
    for m in recent:
        role = m.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if not isinstance(content, str):
            continue
        snippet_parts.append(f"[{role}] {content[:300]}")

    if not snippet_parts:
        return rule_updated

    conversation = "\n".join(snippet_parts)
    prompt = SMART_MEMORY_PROMPT.format(conversation=conversation[:4000])

    _LLM_MAX_ATTEMPTS = int(_CFG_MEM.get("smart_memory_attempts", 3))
    _LLM_MAX_TOKENS   = int(_CFG_MEM.get("smart_memory_max_tokens", 20000))
    _LLM_TIMEOUT_S    = int(_CFG_MEM.get("smart_memory_timeout", 120))
    _last_err = None
    for _attempt in range(_LLM_MAX_ATTEMPTS):
        try:
            payload = {
                "model": _wire(model_id),
                "max_tokens": _LLM_MAX_TOKENS,
                "temperature": 0.1,
                "stream": False,
                "messages": [{"role": "user", "content": prompt}],
            }
            try:
                from lib.thinking_adapter import inject_for_compress
                inject_for_compress(payload)
            except ImportError:
                pass
            r = httpx.post(
                f"{vllm_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=_LLM_TIMEOUT_S,
            )
            if r.status_code != 200:
                # [route-A 2026-07] 429/5xx 不再重试打爆 vLLM
                log.debug(f"  [smart-memory] vLLM {r.status_code}, rule-based already done")
                return rule_updated
            _msg = r.json()["choices"][0]["message"]
            try:
                from lib.thinking_adapter import extract_content
                resp_text = extract_content(_msg)
            except ImportError:
                resp_text = (_msg.get("content") or "").strip()
                resp_text = _re.sub(r"<think>.*?</think>", "", resp_text, flags=_re.DOTALL).strip()
            resp_text = _re.sub(r'```json\s*', '', resp_text)
            resp_text = _re.sub(r'```\s*', '', resp_text)
            _jm = _re.search(r'\{[\s\S]*\}', resp_text)
            if _jm:
                resp_text = _jm.group(0)
            if not resp_text:
                log.debug("  [smart-memory] empty response, skipping")
                return rule_updated
            try:
                result = json.loads(resp_text)
            except json.JSONDecodeError:
                # 修复 LaTeX/非法 JSON 转义: \x (x 非合法转义) → \\x
                _cleaned = _re.sub(r'\\([^"\\/bfnrtu\n\r0-9])', lambda m: '\\\\' + m.group(1), resp_text)
                result = json.loads(_cleaned)
            if not result.get("worth"):
                return rule_updated
            items = result.get("items", [])
            if not items:
                return rule_updated
            _SECT_MAP = {
                "vault": "Vault",
                "userintent": "User Intent",
                "completed": "Completed Work",
                "correction": "Errors & Corrections",
                "reference": "Key References",
            }
            saved = 0
            for item in items[:2]:
                raw_sec = str(item.get("section", "Reference")).strip().lower()
                section = _SECT_MAP.get(raw_sec, "Key References")
                content = (item.get("content") or "").strip()
                if not content:
                    continue
                if len(content) > 200:
                    content = content[:200] + "…"
                manager.save(section, content)
                saved += 1
            log.info(f"  [smart-memory:{manager.session_id[:8]}] saved {saved} items")
            return True
        except (json.JSONDecodeError,
                httpx.ReadTimeout, httpx.ConnectTimeout,
                httpx.WriteTimeout, httpx.PoolTimeout,
                httpx.ReadError, httpx.WriteError,
                httpx.RemoteProtocolError) as retryable_err:
            _last_err = retryable_err
            if _attempt < _LLM_MAX_ATTEMPTS - 1:
                _backoff = 1.5 ** _attempt
                log.debug(f"  [smart-memory] attempt {_attempt+1} failed ({type(retryable_err).__name__}), "
                          f"retry in {_backoff:.1f}s: {retryable_err}")
                time.sleep(_backoff)
                continue
            log.debug(f"  [smart-memory] LLM supplement failed after {_LLM_MAX_ATTEMPTS} attempts: {retryable_err}")
            return rule_updated
        except Exception as e:
            log.warning(f"  [smart-memory] LLM supplement failed (non-retryable): {e}")
            return rule_updated
    return rule_updated


# ── rule_based_memory_update ─────────────────────────────────────────
def rule_based_memory_update(manager: "MemoryManager", messages: list) -> bool:
    """[OPT] P0-B: 不依赖 LLM 的规则记忆提取, 作为 smart_auto_memory 的回退."""
    import logging
    log = logging.getLogger("openclaw")
    updates: list = []

    # 1. user intent (最近一条 user)
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content") or ""
            if isinstance(content, str) and len(content) > 15:
                intent = _re.sub(r'```[\s\S]*?```', '[代码]', content)
                intent = _re.sub(r'\n\s*\n', '\n', intent).strip()
                if len(intent) > 20:
                    updates.append(("User Intent", intent[:200]))
                break

    # 1b. 全部用户问题列表 (最多 100 条)
    question_list = []
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content") or ""
        if not isinstance(content, str):
            continue
        # [FIX 2026-08-31] 原先只挡 "[SYSTEM:" (冒号), 而实际注入用的是连字符
        # ([SYSTEM-TEST] / [SYSTEM-PLAN-GATE] / [SYSTEM-READONLY] ...), 全部漏过。
        # 实测某会话 16 条"用户问题"里 15 条是系统注入, 且随 MEMORY.md 继承污染后续会话。
        if _re.match(r'\[SYSTEM[-:\]]', content):
            continue
        q = _re.sub(r'```[\s\S]*?```', '[代码]', content)
        # [FIX 2026-08-31] 微信桥会把格式规则拼在用户文本后面一起送进来,
        # 不剥掉的话每条"问题"约 90% 是同一段样板 ——
        # 实测某微信会话 Question Log 8025 字符占全文件 85%, 有效信息不足 1/10。
        for _marker in ("[微信模式]", "[wechat-mode]", "[企业微信模式]"):
            if _marker in q:
                q = q.split(_marker)[0]
                break
        q = q.replace('\n', ' ').strip()
        if len(q) > 5:
            question_list.append(q[:100])
    if question_list:
        existing_qs = []
        try:
            if manager.l1_file.exists():
                l1_text = manager.l1_file.read_text(errors="replace")
                mmm = _re.search(r'## Question Log\n- (.*?)(?:\n## |\Z)', l1_text, _re.DOTALL)
                if mmm:
                    raw = mmm.group(1).strip()
                    parts = _re.split(r'\s*\|\s*Q\d+:\s*', raw)
                    existing_qs = [p.strip() for p in parts if p.strip()]
                    if existing_qs and _re.match(r'Q\d+:', existing_qs[0]):
                        existing_qs[0] = existing_qs[0].split(':', 1)[1].strip()
        except Exception:
            pass
        seen = set(existing_qs)
        for q in question_list:
            if q not in seen:
                existing_qs.append(q)
                seen.add(q)
        all_qs = existing_qs[-100:]
        formatted = " | ".join(f"Q{i+1}: {q}" for i, q in enumerate(all_qs))
        updates.append(("Question Log", formatted))

    # 2. 已完成文件路径
    completed_files = set()
    for m in messages:
        if m.get("role") != "assistant":
            continue
        text = m.get("content") or ""
        if not isinstance(text, str):
            continue
        if "[DONE]" in text or "已完成" in text or "测试通过" in text:
            paths = _re.findall(r'[`/]([/\w\-\.]+\.\w{1,6})[`\s]', text)
            completed_files.update(p for p in paths if '/' in p)
    if completed_files:
        updates.append(("Completed Work", ", ".join(sorted(completed_files)[:5])))

    # 3. 错误修复经验
    for m in messages:
        if m.get("role") != "assistant":
            continue
        text = m.get("content") or ""
        if not isinstance(text, str):
            continue
        if "[ERROR]" in text:
            for line in text.split("\n"):
                if "[ERROR]" in line and ("根因" in line or "修复" in line):
                    updates.append(("Errors & Corrections", line.strip()[:200]))
                    break
            if len(updates) >= 4:
                break

    # 4. 关键路径 (从 tool 结果)
    for m in messages:
        if m.get("role") != "tool":
            continue
        content = m.get("content") or ""
        if not isinstance(content, str):
            continue
        path_match = _re.search(r'Written \d+ lines .+ -> (.+)', content)
        if path_match:
            p = path_match.group(1).strip()
            if p not in str(updates):
                updates.append(("Key References", f"文件: {p}"))

    # [2026-05] 偏好/配置/约束捕获 → L1 + L3
    try:
        rule_capture_user_facts(manager, messages)
    except Exception as _ce:
        log.warning(f"  [rule-memory:{manager.session_id[:8]}] capture_user_facts failed: {_ce}")

    if not updates:
        return False

    for section, content in updates[:5]:
        manager.save(section, content)
    log.info(f"  [rule-memory:{manager.session_id[:8]}] saved {len(updates)} items (rule-based)")
    return True


# ── rule_capture_user_facts ──────────────────────────────────────────
# 从用户消息里捕获配置/偏好/约束类事实, 写 L1 + L3 (跨 session 检索).

_RE_SSH_CMD = _re.compile(
    r"\bssh\s+(?:-[a-zA-Z]+\s*\S+\s+)*([A-Za-z0-9_\-]{1,32})@([A-Za-z0-9_\-\.]{3,80})"
    r"(?:\s+-p\s+(\d{1,5}))?"
    r"(?:\s+-i\s+(\S+))?",
    _re.IGNORECASE,
)
_RE_MY_X_IS = _re.compile(
    r"(?:我的|我|my)\s*([A-Za-z_一-龥][A-Za-z0-9_一-龥]{0,24})\s*(?:是|为|=|:|就是|用的是)\s*"
    r"([^\s,，。\n][^,，。\n]{2,80}[^\s,，。\n])",
    _re.IGNORECASE,
)
_RE_PREFER = _re.compile(
    r"(我希望|我不希望|我习惯|我一般|我倾向|记住|请记住|别再|不要再|不要|必须|"
    r"禁止|永远不|不能再|以后|每次)\s*[，:：]?\s*"
    r"([^\n]{6,200})",
)
_RE_HOST_PORT = _re.compile(
    r"(?:host|hostname|server|主机|地址|IP)\s*[:=:]\s*"
    r"([A-Za-z0-9_\-\.]{3,80}(?::\d{2,5})?)",
    _re.IGNORECASE,
)
# [route-A 2026-07] Vault 段: 命令/URL/IP:port/token 命中即锁进 Never Compress
_RE_VAULT_CMD = _re.compile(
    r"(?m)^\s*(?:curl\s+-[A-Za-z]*\s*[^\n]{5,300}"
    r"|docker\s+(?:run|exec|compose|-c)\s+[^\n]{5,300}"
    r"|kubectl\s+[^\n]{3,300}"
    r"|export\s+[A-Z_][A-Z0-9_]*=\S+)",
)
_RE_VAULT_URL = _re.compile(
    r"\bhttps?://[A-Za-z0-9\-\._~:/?#\[\]@!$&'()*+,;=%]{6,300}",
)
_RE_VAULT_IPPORT = _re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b",
)
_RE_VAULT_TOKEN = _re.compile(
    r"\b(?:sk-|pk-|ghp_|gho_|github_pat_|Bearer\s+)[A-Za-z0-9_\-]{16,80}\b",
)
_NOISE_PREFER_TAIL = ("吗？", "吗?", "怎么", "如何", "什么", "为什么", "?", "？")


def rule_capture_user_facts(manager: "MemoryManager", messages: list) -> int:
    """从用户消息里捕获配置/偏好/约束类事实, 写 L1 + L3. 返回入库条数."""
    import logging
    log = logging.getLogger("openclaw")

    last_idx = getattr(manager, "_rcuf_last_idx", -1)
    captured: list = []

    for i, m in enumerate(messages):
        if i <= last_idx:
            continue
        if m.get("role") != "user":
            continue
        content = m.get("content") or ""
        if not isinstance(content, str) or len(content) < 5:
            continue
        if content.startswith("[SYSTEM"):
            continue

        # 1) SSH: "ssh long@10.0.0.5 -p 22 -i ~/.ssh/work"
        for mm in _RE_SSH_CMD.finditer(content):
            user, host = mm.group(1), mm.group(2)
            port = mm.group(3) or ""
            key  = mm.group(4) or ""
            parts = [f"ssh {user}@{host}"]
            if port: parts.append(f"-p {port}")
            if key:  parts.append(f"-i {key}")
            fact = " ".join(parts)
            captured.append(("ssh_config", f"SSH 连接: `{fact}`"))

        # 2) "host=x.y.z" 配置块
        for mm in _RE_HOST_PORT.finditer(content):
            host_v = mm.group(1).strip()
            if len(host_v) >= 4 and "." in host_v or ":" in host_v:
                captured.append(("config", f"主机/服务器: `{host_v}`"))

        # 3) "我的 X 是 Y" 通用陈述
        for mm in _RE_MY_X_IS.finditer(content):
            k = mm.group(1).strip()
            v = mm.group(2).strip()
            if len(k) < 1 or len(v) < 2:
                continue
            v_redact = _redact_secret(k, v)
            captured.append(("user_fact", f"用户事实: 我的 {k} = {v_redact}"))

        # 4) 偏好类
        for mm in _RE_PREFER.finditer(content):
            keyword = mm.group(1).strip()
            rest    = mm.group(2).strip()
            if rest.rstrip().endswith(_NOISE_PREFER_TAIL):
                continue
            rest = rest[:180]
            captured.append(("preference", f"{keyword}: {rest}"))

        # 5) [route-A 2026-07] Vault: 命令/URL/IP:port/token
        for mm in _RE_VAULT_CMD.finditer(content):
            cmd = mm.group(0).strip()[:200]
            captured.append(("vault", f"命令: `{cmd}`"))
        _url_seen = set()
        for mm in _RE_VAULT_URL.finditer(content):
            url = mm.group(0)[:200]
            if url in _url_seen:
                continue
            _url_seen.add(url)
            captured.append(("vault", f"URL: {url}"))
        for mm in _RE_VAULT_IPPORT.finditer(content):
            captured.append(("vault", f"端点: {mm.group(0)}"))
        for mm in _RE_VAULT_TOKEN.finditer(content):
            _tok = mm.group(0)
            _redacted = _tok[:6] + "…" + _tok[-4:] if len(_tok) > 12 else _tok
            captured.append(("vault", f"Token: `{_redacted}` (完整值仅本地保留)"))

    manager._rcuf_last_idx = len(messages) - 1

    if not captured:
        return 0

    # 去重
    seen = set()
    final = []
    for cat, text in captured:
        key = text.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        final.append((cat, text))

    # 写 L1: 按 category 路由到 Vault / User Preferences
    _VAULT_CATS = {"ssh_config", "config", "vault"}
    for cat, text in final:
        section = "Vault" if cat in _VAULT_CATS else "User Preferences"
        try:
            manager.save(section, text)
        except Exception as _se:
            log.warning(f"  [capture-fact] L1 save failed: {_se}")

    # 写 L3 (跨 session 索引) — 容器/host 双路 import
    MemoryIndex = None
    try:
        from memory_index import MemoryIndex as _MI  # 容器
        MemoryIndex = _MI
    except ImportError:
        try:
            from core.memory_index import MemoryIndex as _MI  # host 测试
            MemoryIndex = _MI
        except ImportError:
            log.warning("  [capture-fact] memory_index 模块不可用, 跳过 L3 写入")
    if MemoryIndex is not None:
        ws = None
        root = getattr(manager, "root", None)
        if root is not None:
            try:
                ws = root.parent.parent  # workspace/sessions/<sid> → workspace
            except Exception:
                ws = None
        if ws:
            try:
                idx_path = ws / ".memory_index.db"
                mi = MemoryIndex(idx_path)
                for cat, text in final:
                    try:
                        mi.index_memory(
                            session_id=getattr(manager, "session_id", "unknown"),
                            category=cat,
                            content=text,
                            tags=cat,
                        )
                    except Exception as _ie:
                        log.warning(f"  [capture-fact] L3 index failed: {_ie}")
            except Exception as _me:
                log.warning(f"  [capture-fact] L3 init failed: {_me}")

    log.info(f"  [capture-fact:{getattr(manager,'session_id','?')[:8]}] "
             f"L1+L3 captured {len(final)} facts (ssh/host/pref)")
    return len(final)


def _redact_secret(key: str, value: str) -> str:
    """轻量脱敏 — 看着像 token/key/password 的值, 保留前2后2."""
    k_low = (key or "").lower()
    secret_marker = any(s in k_low for s in (
        "token", "key", "secret", "password", "passwd", "pass",
        "api", "auth", "credential", "私钥", "密码", "密钥"
    ))
    if not secret_marker and len(value) > 24 and _re.match(r"^[A-Za-z0-9_\-\.]+$", value):
        secret_marker = True
    if not secret_marker:
        return value
    if len(value) <= 6:
        return "****"
    return f"{value[:2]}****{value[-2:]}"
