"""memory/manager.py — MemoryManager 单会话记忆管理器.

- L1: MEMORY.md (每轮注入 system prompt)
- L2: memory/*.md (按主题, LLM read_file 按需加载)
- L3: memory/archive/ (旧 L1 归档)
- meta.json: 元数据 (last_compress_ts, compress_fail_streak 等)

压缩走本地 vLLM /chat/completions, 不经过 gateway 自己 (防循环).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from .consts import (
    _CFG_MEM, _CFG_MODEL, _FORCE_FULL_EVERY,
    CHARS_PER_TOKEN, COMPACTION_PROMPT,
    L1_MAX_LINES, MSG_HARD_COUNT, MSG_SOFT_COUNT,
)
from .iohelp import _atomic_write, _validate_compressed, estimate_tokens


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


class MemoryManager:
    """Per-session 记忆管理器.
    - 压缩用本地 vLLM backend (直连, 不经过 litecode_server 自身)
    - 阈值基于 context_window 动态计算
    """

    def __init__(
        self,
        workspace: Path,
        session_id: str,
        vllm_url: str,
        model_id: str,
        api_key: str = "EMPTY",
        context_window: int = 65000,
    ):
        self.session_id     = session_id
        self.vllm_url       = vllm_url.rstrip("/")
        self.model_id       = model_id
        self.api_key        = api_key
        self.context_window = context_window

        _cfg_soft = _CFG_MEM.get("soft_token_limit") or 0
        _cfg_hard = _CFG_MEM.get("hard_token_limit") or 0
        _auto_soft = int(context_window * 0.45)
        _auto_hard = int(context_window * 0.75)
        self.soft_limit = min(_cfg_soft, _auto_soft) if _cfg_soft > 0 else _auto_soft
        self.hard_limit = min(_cfg_hard, _auto_hard) if _cfg_hard > 0 else _auto_hard

        self.root        = workspace / "sessions" / session_id
        self.l1_file     = self.root / "MEMORY.md"
        self.l2_dir      = self.root / "memory"
        self.archive_dir = self.root / "memory" / "archive"
        self.meta_file   = self.root / "meta.json"

        self.l2_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        self._pending: Optional[str] = None
        self._bg: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._write_count: int = 0
        self._last_hashes: dict = {}

        if not self.l1_file.exists():
            self._init_l1()

    # ── Init ──────────────────────────────────────────────────
    def _init_l1(self):
        self.l1_file.write_text("""\
# Session Memory — L1
_Auto-managed. Max 160 lines. Older content archived to memory/._

## Vault (Never Compress)
<!-- 永久保留: SSH 配置、命令、URL、IP:port、密钥 token、配置片段 -->

## User Intent
<!-- What is the user currently trying to accomplish? -->

## Completed Work
<!-- Verified, done items -->

## In-Progress
<!-- Current state — exact paths/IDs/values -->

## Errors & Corrections
<!-- Verbatim errors + user corrections. Never paraphrase. -->

## Key References
<!-- IDs, paths, URLs, API keys, hostnames, exact values -->

## Next Steps
<!-- Numbered actionable items -->
""")

    def _atomic_write_if_changed(self, path: Path, content: str, force: bool = False) -> bool:
        """skip-if-unchanged: 相同 hash 跳过写盘, 每 _FORCE_FULL_EVERY 次强制全量.
        返回 True=写盘了, False=跳过"""
        import hashlib as _hl
        self._write_count += 1
        key = str(path)
        new_hash = _hl.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        must_full = force or (self._write_count % _FORCE_FULL_EVERY == 0)
        if not must_full and self._last_hashes.get(key) == new_hash:
            return False
        _atomic_write(path, content)
        self._last_hashes[key] = new_hash
        return True

    # ── Load L1 for system prompt ─────────────────────────────
    def load_for_prompt(self) -> str:
        """返回注入 system prompt 的内容. 如后台压缩完成, 无缝写入并返回新内容."""
        with self._lock:
            if self._pending:
                self._atomic_write_if_changed(self.l1_file, self._pending)
                self._pending = None
                self._update_meta({"last_applied": time.time()})

        if not self.l1_file.exists():
            return ""

        content = self.l1_file.read_text(errors="replace")
        l2 = self._l2_index()
        if l2:
            content += "\n\n" + l2
        return content[:6000]

    def _l2_index(self) -> str:
        files = list(self.l2_dir.glob("*.md"))
        if not files:
            return ""
        return "## L2 Memory files (load with read_file):\n" + \
               "\n".join(f"  - sessions/{self.session_id}/memory/{f.name}" for f in files)

    # ── Check & compact ───────────────────────────────────────
    def on_task_end(self, messages: list):
        """[v1.0] 每个任务结束后无条件更新 L1 记忆.
        不依赖压缩触发, 不依赖 LLM 调用. 直接用规则提取.
        灵感来自 Claude Code: 每轮结束后立即做增量 context 更新.
        """
        import logging
        log = logging.getLogger("openclaw")
        try:
            from .auto import rule_based_memory_update
            updated = rule_based_memory_update(self, messages)
            if updated:
                log.info(f"  [memory:{self.session_id[:8]}] on_task_end: incremental update done")
        except Exception as e:
            log.warning(f"  [memory:{self.session_id[:8]}] on_task_end error: {e}")

    def check_and_compact(self, messages: list, force: bool = False, _force_async: bool = False) -> bool:
        tokens    = estimate_tokens(messages)
        msg_count = len(messages)
        self._update_meta({"last_token_estimate": tokens,
                           "last_msg_count":      msg_count,
                           "last_turn_ts":        time.time()})

        import logging
        log = logging.getLogger("openclaw")

        # [FIX] 熔断: 最近 2 次 timed out, 30 min 内不再尝试, 只跑 rule-based
        _meta = self._load_meta()
        _cfail_streak = int(_meta.get("compress_fail_streak", 0))
        _cfail_last   = float(_meta.get("compress_fail_last_ts", 0))
        _backoff_base = int(_CFG_MEM.get("compress_breaker_window", 1800))
        _backoff_max  = int(_CFG_MEM.get("compress_breaker_max", 14400))
        _extra_steps = max(_cfail_streak - 2, 0)
        _wait_window = min(_backoff_max, _backoff_base * (2 ** _extra_steps))
        if _cfail_streak >= 2 and (time.time() - _cfail_last) < _wait_window:
            log.warning(
                f"  [memory:{self.session_id[:8]}] compress 熔断中 "
                f"(连续 {_cfail_streak} 次失败, 距上次 {int(time.time() - _cfail_last)}s, "
                f"<{_wait_window}s 退避窗口), 本轮跳过 LLM 压缩"
            )
            try:
                from .auto import rule_based_memory_update
                rule_based_memory_update(self, messages)
                log.info(f"  [memory:{self.session_id[:8]}] rule-based fallback applied during breaker")
            except Exception as _e:
                log.warning(f"  [memory] rule-based breaker fallback err: {_e}")
            return False

        # [FIX] 超大上下文跳过 — LLM 压缩 100k+ 消息列表几乎必失败, 硬跳
        _max_compress_tokens = int(_CFG_MEM.get("compress_max_input_tokens", 90000))
        if tokens > _max_compress_tokens and not force:
            log.warning(
                f"  [memory:{self.session_id[:8]}] skip compress: "
                f"{tokens}t > {_max_compress_tokens}t (太大, 不尝试)"
            )
            return False

        _last_compress = _meta.get("last_compress_ts", 0)
        _compress_cooldown = (time.time() - _last_compress) >= 60

        do_hard = (tokens >= self.hard_limit or msg_count >= MSG_HARD_COUNT or force) and (_compress_cooldown or force)
        do_soft = tokens >= self.soft_limit or msg_count >= MSG_SOFT_COUNT

        if do_hard:
            mode_label = "async" if _force_async else "sync"
            log.info(
                f"  [memory:{self.session_id[:8]}] HARD "
                f"{tokens}t/{self.hard_limit}t  msgs={msg_count} -> {mode_label} compress"
            )
            self._do_compress(messages, async_mode=_force_async)
            return True

        if do_soft:
            with self._lock:
                running = self._bg and self._bg.is_alive()
            if not running:
                log.info(
                    f"  [memory:{self.session_id[:8]}] soft "
                    f"{tokens}t/{self.soft_limit}t  msgs={msg_count} -> bg compress"
                )
                t = threading.Thread(
                    target=self._do_compress, args=(messages, True), daemon=True
                )
                with self._lock:
                    self._bg = t
                t.start()
            return True

        return False

    # ── Compress via local vLLM ───────────────────────────────
    def _do_compress(self, messages: list, async_mode: bool = True):
        """直接调用 vLLM backend HTTP API, 不依赖 anthropic SDK."""
        import logging
        log = logging.getLogger("openclaw")
        try:
            l1_current = self.l1_file.read_text(errors="replace") if self.l1_file.exists() else ""

            transcript = ""
            total = len(messages)
            for idx, m in enumerate(messages):
                role = m.get("role", "?")
                if role == "system":
                    continue
                content = m.get("content") or ""
                recency  = (idx + 1) / max(total, 1)
                txt_cap  = int(200 + recency * 300)
                res_cap  = int(100 + recency * 200)

                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        bt = block.get("type", "")
                        if bt == "text":
                            transcript += f"\n[{role}] {block['text'][:txt_cap]}"
                        elif bt == "tool_use":
                            transcript += f"\n[tool:{block.get('name','')}] {str(block.get('input',''))[:res_cap]}"
                        elif bt == "tool_result":
                            rc = block.get("content") or ""
                            if isinstance(rc, list):
                                rc = " ".join(b.get("text", "") for b in rc if isinstance(b, dict))
                            transcript += f"\n[result] {str(rc)[:res_cap]}"
                elif isinstance(content, str):
                    if role == "tool" and content.startswith("[cleared:"):
                        continue
                    transcript += f"\n[{role}] {content[:txt_cap]}"

            combined = (
                f"=== Current L1 Memory ===\n{l1_current}\n\n"
                f"=== Full Conversation ({total} messages) ===\n{transcript}"
            )
            prompt = COMPACTION_PROMPT.format(content=combined[:32000])

            def _call_compress(enable_thinking: bool) -> str:
                payload = {
                    "model":       _wire(self.model_id),
                    "max_tokens":  4096,
                    "temperature": 0.1,
                    "stream":      False,
                    "messages":    [{"role": "user", "content": prompt}],
                }
                try:
                    from lib.thinking_adapter import inject_for_compress
                    inject_for_compress(payload)
                except ImportError:
                    if enable_thinking:
                        payload["chat_template_kwargs"] = {"enable_thinking": True}
                _compress_to = int(_CFG_MEM.get("compress_timeout", 600))
                _http_timeout = httpx.Timeout(
                    connect=30.0,
                    read=float(_compress_to),
                    write=60.0,
                    pool=60.0,
                )
                r = httpx.post(
                    f"{self.vllm_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=_http_timeout,
                )
                if r.status_code != 200:
                    log.warning(f"  [memory] vLLM {r.status_code}: {r.text[:200]}")
                    return ""
                body = r.json()
                msg = body["choices"][0]["message"]
                try:
                    from lib.thinking_adapter import extract_content
                    return extract_content(msg)
                except ImportError:
                    import re as _re_ct
                    txt = (msg.get("content") or "").strip()
                    txt = _re_ct.sub(r"<think>.*?</think>", "", txt, flags=_re_ct.DOTALL).strip()
                    if not txt:
                        txt = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
                    return txt

            _enable = _CFG_MODEL.get("enable_thinking", True)
            resp_text = _call_compress(enable_thinking=_enable)
            compressed = resp_text
            if not compressed:
                log.warning(f"  [memory:{self.session_id[:8]}] compress: model returned empty content after retry, skipping")
                return

            old_l1_text = self.l1_file.read_text(errors="replace") if self.l1_file.exists() else ""
            is_valid, reason = _validate_compressed(compressed, old_l1_text)
            if not is_valid:
                log.warning(
                    f"  [memory:{self.session_id[:8]}] compress quality FAIL ({reason}), "
                    f"discarding new ({len(compressed)}c) and keeping old ({len(old_l1_text)}c)"
                )
                bad_path = self.archive_dir / f"rejected_{int(time.time())}.md"
                bad_path.parent.mkdir(parents=True, exist_ok=True)
                bad_path.write_text(f"# REJECTED compression\n# reason: {reason}\n\n{compressed}")
                log.info(f"  [memory:{self.session_id[:8]}] rejected compression saved to {bad_path.name}")
                return

            if self.l1_file.exists() and len(old_l1_text.splitlines()) > L1_MAX_LINES:
                self._archive_l1()

            if async_mode:
                with self._lock:
                    self._pending = compressed
            else:
                self._atomic_write_if_changed(self.l1_file, compressed)

            self._update_meta({"last_compress_ts":     time.time(),
                               "compress_msg_count":   total,
                               "mode":                 "async" if async_mode else "sync",
                               "compress_fail_streak": 0,
                               "compress_fail_last_ts": 0})
            log.info(f"  [memory:{self.session_id[:8]}] compress done ({total} msgs, {'queued' if async_mode else 'written'})")

        except Exception as e:
            _log2 = logging.getLogger("openclaw")
            _meta2 = self._load_meta()
            _cfail2 = int(_meta2.get("compress_fail_streak", 0)) + 1
            self._update_meta({"compress_fail_streak":  _cfail2,
                               "compress_fail_last_ts": time.time()})
            _log2.warning(f"  [memory:{self.session_id[:8]}] compress err ({_cfail2}连): {e}")
            try:
                from .auto import rule_based_memory_update
                if rule_based_memory_update(self, messages):
                    _log2.info(f"  [memory:{self.session_id[:8]}] rule-based fallback applied after compress fail")
            except Exception as _re:
                _log2.debug(f"  [memory] rule-based fail-fallback err: {_re}")

    def _archive_l1(self):
        dst = self.archive_dir / f"l1_{int(time.time())}.md"
        dst.write_text(self.l1_file.read_text(errors="replace"))

    # ── Agent tool: save_memory ───────────────────────────────
    def save(self, section: str, content: str, topic: Optional[str] = None):
        if topic:
            p = self.l2_dir / f"{topic}.md"
            existing = p.read_text(errors="replace") if p.exists() else f"# {topic}\n"
            p.write_text(existing + f"\n## {section}\n{content.strip()}\n")
        else:
            text = self.l1_file.read_text(errors="replace") if self.l1_file.exists() else ""
            heading = f"## {section}"
            entry = f"- {content.strip()}"
            if section == "Question Log" and heading in text:
                import re as _re2
                text = _re2.sub(
                    rf'{_re2.escape(heading)}\n.*?(?=\n## |\Z)',
                    f"{heading}\n{entry}\n",
                    text, flags=_re2.DOTALL,
                )
            else:
                text = text.replace(heading, f"{heading}\n{entry}", 1) if heading in text \
                       else text + f"\n{heading}\n{entry}\n"
            lines = text.splitlines()
            if len(lines) > L1_MAX_LINES:
                self._archive_l1()
                text = "# Session Memory — L1\n_[older content archived]_\n\n" + \
                       "\n".join(lines[L1_MAX_LINES // 2:])
            self._atomic_write_if_changed(self.l1_file, text)

    # ── Structure for web UI ──────────────────────────────────
    def get_structure(self, messages: Optional[list] = None) -> dict:
        tokens = estimate_tokens(messages) if messages else 0
        meta = self._load_meta()

        result = {
            "sid": self.session_id,
            "token_estimate": tokens,
            "soft_limit": self.soft_limit,
            "hard_limit": self.hard_limit,
            "context_window": self.context_window,
            "pct": round(tokens / self.hard_limit * 100, 1) if self.hard_limit else 0,
            "l1": None, "l2": [], "archive": [], "meta": meta,
            "compress_pending": self._pending is not None,
            "compress_running": bool(self._bg and self._bg.is_alive()),
        }

        if self.l1_file.exists():
            c = self.l1_file.read_text(errors="replace")
            sects, cur = {}, None
            for line in c.splitlines():
                if line.startswith("## "):
                    cur = line[3:].strip(); sects[cur] = []
                elif cur and line.strip().startswith("- ") and len(sects[cur]) < 5:
                    sects[cur].append(line.strip()[2:])
            result["l1"] = {
                "content": c,
                "lines": len(c.splitlines()),
                "tokens": len(c) // CHARS_PER_TOKEN,
                "sections": {k: v for k, v in sects.items() if v},
            }

        if self.l2_dir.exists():
            for f in sorted(self.l2_dir.glob("*.md")):
                c = f.read_text(errors="replace")
                result["l2"].append({"name": f.name, "topic": f.stem,
                                     "lines": len(c.splitlines()),
                                     "tokens": len(c) // CHARS_PER_TOKEN,
                                     "preview": c[:150]})

        if self.archive_dir.exists():
            for f in sorted(self.archive_dir.glob("*.md"), reverse=True):
                ts = f.stem.replace("l1_", "")
                try:
                    import datetime
                    dt = datetime.datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")
                except Exception:
                    dt = ts
                result["archive"].append({"name": f.name, "date": dt})

        return result

    def _load_meta(self) -> dict:
        if self.meta_file.exists():
            try:
                return json.loads(self.meta_file.read_text())
            except Exception:
                pass
        return {}

    def _update_meta(self, upd: dict):
        m = self._load_meta(); m.update(upd)
        try:
            self.meta_file.write_text(json.dumps(m, ensure_ascii=False, indent=2))
        except Exception:
            pass
