"""
itrace.py -- LiteCode v1.0 树状追踪引擎
========================================
在 v6 基础上新增:
  - OpenTelemetry 风格的 trace_id / span_id / parent_span_id
  - 子代理嵌套追踪（树状结构）
  - 运行统计面板数据输出
  - 追踪 DAG 编排步骤

数据格式:
  [TAG] {"trace_id": "xxx", "span_id": "yyy", "parent_span_id": "zzz", ...}

前端渲染:
  ▼ [Task] 开发登录模块 (trace_id=abc)
    ▼ [Step] 数据库设计 (span=s1, parent=root)
      - SQL 执行 (Success)
    ▼ [Step] 后端 API (span=s2, parent=root)
      - 代码运行 (Failed)
      ▼ [Critic] 审查 (span=s3, parent=s2)
        - 变量未定义 → 修复
      - 代码重新运行 (Success)

用法不变:
  from itrace import Tracer, make_tracer
  tracer = make_tracer(config, workspace, session_id)
  tracer.start(user_message)
"""

import json
import os
import re as _re
import time
import hashlib
import threading
import uuid
from collections import Counter as _Counter
from pathlib import Path
from typing import Dict, List, Optional


# ── 状态码定义 ──────────────────────────────────────
STATUS_CODES = {
    "ITER_OK":           "本轮正常完成",
    "ITER_TOOL_CALL":    "本轮有工具调用，继续循环",
    "ITER_EMPTY_REPLY":  "模型返回空文本，触发重试",
    "ITER_ENFORCE":      "首轮无前置文字，强制重跑",
    "TOKEN_LIMIT_NEAR":  "token 估算接近 soft limit",
    "TOKEN_LIMIT_HARD":  "token 估算接近 hard limit",
    "RETRY_ERROR":       "工具执行出错，error_streak 递增",
    "RETRY_PERMANENT":   "永久性错误，不重试",
    "LOGIC_LOOP":        "疑似逻辑循环（连续相同工具调用）",
    "TIMEOUT":           "任务超时",
    "SUBAGENT_START":    "子智能体启动",
    "SUBAGENT_END":      "子智能体结束",
    "COMPRESS_TRIGGER":  "触发记忆压缩",
    "SKILL_LOADED":      "技能自动加载",
    "TASK_DONE":         "任务正常完成",
    "TASK_ERROR":        "任务异常终止",
    # v1.0 新增
    "DAG_STEP_START":    "DAG 步骤开始",
    "DAG_STEP_END":      "DAG 步骤结束",
    "CRITIC_START":      "Critic 审查开始",
    "CRITIC_END":        "Critic 审查结束",
    "BLACKBOARD_WRITE":  "写入共享黑板",
    "CHECKPOINT_SAVE":   "保存状态快照",
    "CHECKPOINT_RESTORE":"从快照恢复",
}

CHARS_PER_TOKEN = 4


def _estimate_tokens(messages: list) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // CHARS_PER_TOKEN


def _hash_short(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:8]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _gen_id() -> str:
    return uuid.uuid4().hex[:12]


# ═══════════════════════════════════════════════════════
# Span 上下文管理
# ═══════════════════════════════════════════════════════

class SpanContext:
    """追踪上下文: 管理 trace/span 层级关系。"""

    def __init__(self, trace_id: str = None):
        self.trace_id = trace_id or _gen_id()
        self._span_stack: List[str] = []  # span_id 栈
        self.root_span_id = _gen_id()
        self._span_stack.append(self.root_span_id)

    @property
    def current_span_id(self) -> str:
        return self._span_stack[-1] if self._span_stack else self.root_span_id

    @property
    def parent_span_id(self) -> str:
        if len(self._span_stack) > 1:
            return self._span_stack[-2]
        return ""

    def push_span(self, span_id: str = None) -> str:
        """进入子 span，返回新 span_id。"""
        sid = span_id or _gen_id()
        self._span_stack.append(sid)
        return sid

    def pop_span(self) -> str:
        """退出子 span，返回退出的 span_id。"""
        if len(self._span_stack) > 1:
            return self._span_stack.pop()
        return self._span_stack[0]

    @property
    def depth(self) -> int:
        return len(self._span_stack) - 1


# ═══════════════════════════════════════════════════════
# Memory Snapshot（保持兼容）
# ═══════════════════════════════════════════════════════

class MemorySnapshot:
    """记忆快照，用于计算 delta。"""
    def __init__(self, l1_path: Path = None):
        self.content = ""
        self.sections: dict = {}
        self.hash = ""
        if l1_path and l1_path.exists():
            self.content = l1_path.read_text(errors="replace")
            self.hash = _hash_short(self.content)
            cur = None
            for line in self.content.splitlines():
                if line.startswith("## "):
                    cur = line[3:].strip()
                    self.sections[cur] = []
                elif cur and line.strip():
                    self.sections[cur].append(line.strip())

    def diff(self, other: 'MemorySnapshot') -> dict:
        try:
            if self.hash == other.hash:
                return {"changed": False}
            added_sections = set(other.sections.keys()) - set(self.sections.keys())
            removed_sections = set(self.sections.keys()) - set(other.sections.keys())
            modified = {}
            for sec in set(self.sections.keys()) & set(other.sections.keys()):
                old_set = set(self.sections[sec])
                new_set = set(other.sections[sec])
                if old_set != new_set:
                    modified[sec] = {
                        "added": list(new_set - old_set)[:5],
                        "removed": list(old_set - new_set)[:5],
                    }
            return {
                "changed": True,
                "added_sections": list(added_sections),
                "removed_sections": list(removed_sections),
                "modified": modified,
                "old_hash": self.hash,
                "new_hash": other.hash,
            }
        except Exception:
            return {"changed": False, "error": "diff_failed"}


# ═══════════════════════════════════════════════════════
# Tracer v1.0
# ═══════════════════════════════════════════════════════

class Tracer:
    """
    树状追踪器。

    v1.0 新增:
    - span_context: trace_id / span_id / parent_span_id
    - dag_step_begin/end: DAG 编排步骤追踪
    - critic_event: Critic Agent 事件
    - blackboard_event: 黑板写入事件
    - get_dashboard_data: 运行面板数据
    """

    def __init__(self, config: dict, workspace: Path = None, session_id: str = ""):
        self.enabled = config.get("enabled", False)
        if not self.enabled:
            return

        self.log_level = config.get("log_level", "NORMAL")
        self.include_memory_diff = config.get("include_memory_diff", True)
        self.include_raw_prompt = config.get("include_raw_prompt", False)
        self.max_prompt_chars = config.get("max_prompt_chars", 2000)

        save_path = config.get("save_path", "./logs/iteration_full.log")
        self._log_path = Path(save_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

        self._workspace = workspace or Path("/tmp/litecode_workspace")
        self._session_id = session_id
        self._task_id = f"T{int(time.time())}{_hash_short(session_id)[:4]}"
        self._lock = threading.Lock()
        self._tool_history: list = []
        self._consecutive_loops: int = 0
        self._finished: bool = False
        self._iteration_count = 0
        self._total_tool_calls = 0
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._start_time = 0.0
        self._last_loop = False
        self._memory_before: Optional[MemorySnapshot] = None

        # v1.0: Span 上下文
        self.span_context = SpanContext()

        # 运行统计
        self._stats = {
            "tool_calls": {},
            "tool_times": {},
            "errors": [],
            "status_codes": [],
            "token_estimates": [],
            # v1.0 新增
            "dag_steps": [],        # [(step_id, status, elapsed)]
            "critic_issues": [],    # [issue_text]
            "blackboard_writes": 0,
        }

    def _write(self, text: str):
        if not self.enabled:
            return
        try:
            with self._lock:
                with self._log_path.open("a", encoding="utf-8") as f:
                    f.write(text + "\n")
        except Exception:
            pass

    def _w(self, tag: str, data: dict):
        """写一个标签块，自动注入 trace 上下文。"""
        if hasattr(self, 'span_context'):
            data["trace_id"] = self.span_context.trace_id
            data["span_id"] = self.span_context.current_span_id
            data["parent_span_id"] = self.span_context.parent_span_id
            data["depth"] = self.span_context.depth
        self._write(f"[{tag}] {json.dumps(data, ensure_ascii=False, default=str)}")

    # ── 生命周期方法（保持 v6 兼容）────────────────────────

    def start(self, user_message: str, sys_prompt: str = "",
              messages_count: int = 0, history_tokens: int = 0):
        if not self.enabled:
            return
        self._start_time = time.time()

        l1_path = self._workspace / "sessions" / self._session_id / "MEMORY.md"
        self._memory_before = MemorySnapshot(l1_path)

        self._write("\n" + "=" * 80)
        self._w("TRACE_START", {
            "timestamp": _now_iso(),
            "task_id": self._task_id,
            "session_id": self._session_id[:12],
            "user_message": user_message[:500],
            "history_messages": messages_count,
            "history_tokens_est": history_tokens,
        })

        if self.include_raw_prompt and sys_prompt:
            cap = self.max_prompt_chars
            self._w("PROMPT_BLOCK", {
                "system_prompt_chars": len(sys_prompt),
                "system_prompt_head": sys_prompt[:cap],
                "system_prompt_tail": sys_prompt[-500:] if len(sys_prompt) > cap else "",
            })

    def iteration_begin(self, iter_n: int, messages: list):
        if not self.enabled:
            return
        self._iteration_count = iter_n
        tokens = _estimate_tokens(messages)
        self._total_tokens_in = tokens
        self._stats["token_estimates"].append(tokens)

        self._w("ITER_BEGIN", {
            "iteration": iter_n,
            "messages_count": len(messages),
            "tokens_est": tokens,
            "elapsed_s": round(time.time() - self._start_time, 1),
        })

    def tool_call(self, name: str, args: dict, result: str,
                  elapsed: float, is_error: bool = False):
        if not self.enabled:
            return
        self._total_tool_calls += 1

        self._stats["tool_calls"][name] = self._stats["tool_calls"].get(name, 0) + 1
        self._stats["tool_times"][name] = self._stats["tool_times"].get(name, 0) + elapsed

        if is_error:
            self._stats["errors"].append((
                self._iteration_count, name, result[:200]
            ))

        # LOGIC_LOOP 检测（保持原有逻辑）
        call_sig = f"{name}:{json.dumps(args, sort_keys=True)[:200]}"
        result_hash = hashlib.md5(result[:500].encode()).hexdigest()[:8]
        self._tool_history.append({
            "sig": call_sig, "name": name,
            "result_hash": result_hash, "is_error": is_error,
            "_result_head": result[:200],
        })

        is_loop = self._detect_loop(name, args, result, is_error, result_hash)

        args_preview = {}
        for k, v in list(args.items())[:3]:
            args_preview[k] = str(v)[:80]

        data = {
            "iteration": self._iteration_count,
            "tool": name,
            "args_preview": args_preview,
            "result_chars": len(result),
            "result_head": result[:200].replace("\n", " "),
            "elapsed_s": round(elapsed, 2),
            "is_error": is_error,
        }
        if is_loop:
            data["LOGIC_LOOP"] = True
            self._consecutive_loops += 1
        else:
            self._consecutive_loops = 0
        self._w("TOOL_EXEC", data)
        self._last_loop = is_loop

    def _detect_loop(self, name: str, args: dict, result: str,
                     is_error: bool, result_hash: str) -> bool:
        """循环检测（合并 v6 的 5 种检测规则）。"""
        hist = self._tool_history

        # 检测1: 连续 3 次完全相同
        if (len(hist) >= 3 and
            hist[-1]["sig"] == hist[-2]["sig"] == hist[-3]["sig"]):
            return True

        # 检测2: 频率检测
        # [v1.0] execute_shell 也需检查参数多样性（批量 curl 测试是正常行为）
        _PATH_TOOLS = {"write_file", "read_file", "patch_file"}
        _DIVERSE_TOOLS = _PATH_TOOLS | {"execute_shell"}  # 这些工具允许高频但不同参数
        recent = hist[-8:]
        threshold = 5 if name in _PATH_TOOLS else 4
        if len(recent) >= 6:
            name_counts = _Counter(h["name"] for h in recent)
            if name_counts.get(name, 0) >= threshold:
                if name in _DIVERSE_TOOLS:
                    # 参数足够多样化（≥3种不同签名）则不算循环
                    sigs = [h["sig"][:120] for h in recent if h["name"] == name]
                    if len(set(sigs)) < 3:
                        return True
                else:
                    return True

        # 检测3: 相同错误输出
        if is_error:
            recent_errors = [h for h in hist[-5:] if h["is_error"]]
            error_hashes = [h["result_hash"] for h in recent_errors]
            if error_hashes.count(result_hash) >= 2:
                return True

        # 检测4: 相同错误模式
        if is_error and len(hist) >= 2:
            def _error_prefix(r):
                first_line = r.split("\n")[0][:120] if r else ""
                return _re.sub(r'(pid|after)\s*=?\s*\d+', 'NUM', first_line)
            cur_prefix = _error_prefix(result)
            same = sum(
                1 for h in hist[-4:]
                if h["is_error"] and h["name"] == name and
                _error_prefix(h.get("_result_head", "")) == cur_prefix
            )
            if same >= 2:
                return True

        # 检测5: 服务重启循环
        if name == "execute_shell":
            restart_kw = ("nohup", "pkill", "fuser -k", "restart", "kill -9")
            cmd = args.get("command", "")
            if any(kw in cmd for kw in restart_kw):
                count = sum(
                    1 for h in hist[-5:]
                    if h["name"] == "execute_shell" and
                    any(kw in json.dumps(h.get("sig", "")) for kw in restart_kw)
                )
                if count >= 3:
                    return True

        return False

    def should_force_break(self) -> bool:
        """连续 3 次 LOGIC_LOOP 后返回 True。"""
        if not self.enabled:
            return False
        return self._consecutive_loops >= 3

    def reset_loop(self):
        self._consecutive_loops = 0
        self._last_loop = False
        self._tool_history.clear()

    def get_loop_hint(self) -> str:
        if not self.enabled or not getattr(self, '_last_loop', False):
            return ""
        self._last_loop = False
        return (
            "\n\n[SYSTEM: 检测到重复模式。你已多次尝试相似操作但未成功。"
            "必须立即加载调试技能: load_skill systematic-debugging，走4阶段根因分析，不要再盲目重试。\n"
            "策略变更参考: "
            "服务反复启动失败→先隔离: PYTHONPATH=. python3 -c 'from server.main import create_app; print(create_app())'; "
            "background=true 反复超时→改用前台运行 timeout 5 python3 main.py 2>&1 查看完整报错; "
            "web_fetch 反复失败→改用 web_search; "
            "patch_file 反复失败→改用 write_file 完全重写; "
            "正则/提取不匹配→先用 repr() 检查原始字符编码; "
            "陌生报错→web_search '错误关键词 python fix'。"
            "不要再重复同一种方法。]"
        )

    def llm_output(self, text: str, tool_calls: list,
                   tokens_est_out: int = 0):
        if not self.enabled:
            return
        self._total_tokens_out += tokens_est_out

        data = {
            "iteration": self._iteration_count,
            "text_chars": len(text),
            "text_head": text[:300].replace("\n", " ") if text else "",
            "tool_calls_count": len(tool_calls),
            "tool_names": [tc.get("function", {}).get("name", "") for tc in tool_calls][:5],
            "tokens_est_out": tokens_est_out,
        }
        if self.log_level == "VERBOSE" and text:
            data["text_full"] = text[:2000]
        self._w("LLM_RAW_OUT", data)

    def data_chain(self, chain_desc: str):
        if not self.enabled:
            return
        self._w("DATA_CHAIN", {
            "iteration": self._iteration_count,
            "chain": chain_desc,
        })

    def iteration_end(self, status_code: str, detail: str = ""):
        if not self.enabled:
            return
        self._stats["status_codes"].append(status_code)
        data = {
            "iteration": self._iteration_count,
            "status": status_code,
            "status_desc": STATUS_CODES.get(status_code, ""),
        }
        if detail:
            data["detail"] = detail[:300]
        self._w("ITER_END", data)

    def memory_state(self, detail: str = ""):
        if not self.enabled or not self.include_memory_diff:
            return
        l1_path = self._workspace / "sessions" / self._session_id / "MEMORY.md"
        current = MemorySnapshot(l1_path)

        data = {
            "iteration": self._iteration_count,
            "l1_lines": len(current.content.splitlines()),
            "l1_hash": current.hash,
            "sections": list(current.sections.keys()),
        }
        if self._memory_before:
            delta = self._memory_before.diff(current)
            data["DELTA"] = delta if delta.get("changed") else "NO_CHANGE"
        if detail:
            data["detail"] = detail

        self._w("MEMORY_STATE", data)
        self._memory_before = current

    def subagent_event(self, agent_type: str, task: str,
                       event: str = "start", result: str = "",
                       elapsed: float = 0):
        if not self.enabled:
            return
        code = "SUBAGENT_START" if event == "start" else "SUBAGENT_END"

        if event == "start":
            span_id = self.span_context.push_span()
        else:
            span_id = self.span_context.current_span_id

        self._w(code, {
            "iteration": self._iteration_count,
            "agent_type": agent_type,
            "task": task[:200],
            "result_chars": len(result) if result else 0,
            "result_head": result[:150].replace("\n", " ") if result else "",
            "elapsed_s": round(elapsed, 2) if elapsed else 0,
        })

        if event == "end":
            self.span_context.pop_span()

    def skill_loaded(self, skill_names: list, trigger: str = "auto"):
        if not self.enabled:
            return
        self._w("SKILL_LOADED", {"skills": skill_names, "trigger": trigger})

    def compress_event(self, mode: str, tokens_before: int, msg_count: int):
        if not self.enabled:
            return
        self._w("COMPRESS_TRIGGER", {
            "mode": mode, "tokens_before": tokens_before, "msg_count": msg_count,
        })

    # ── v1.0 新增追踪方法 ────────────────────────────────

    def dag_step_begin(self, step_id: str, label: str,
                       agent_type: str, task: str):
        """DAG 步骤开始: 创建新 span。"""
        if not self.enabled:
            return
        span_id = self.span_context.push_span()
        self._w("DAG_STEP_START", {
            "step_id": step_id, "label": label,
            "agent_type": agent_type, "task": task[:200],
        })

    def dag_step_end(self, step_id: str, label: str,
                     status: str, elapsed: float,
                     error: str = ""):
        """DAG 步骤结束: 退出 span。"""
        if not self.enabled:
            return
        self._stats["dag_steps"].append((step_id, status, elapsed))
        self._w("DAG_STEP_END", {
            "step_id": step_id, "label": label,
            "status": status, "elapsed_s": round(elapsed, 2),
            "error": error[:200] if error else "",
        })
        self.span_context.pop_span()

    def critic_event(self, event: str, issues: list = None):
        """Critic Agent 事件。"""
        if not self.enabled:
            return
        code = "CRITIC_START" if event == "start" else "CRITIC_END"
        data = {"event": event}
        if issues:
            data["issues"] = issues[:10]
            self._stats["critic_issues"].extend(issues[:10])
        self._w(code, data)

    def blackboard_write(self, key: str, entry_type: str, source: str):
        """黑板写入事件。"""
        if not self.enabled:
            return
        self._stats["blackboard_writes"] += 1
        self._w("BLACKBOARD_WRITE", {
            "key": key, "type": entry_type, "source": source,
        })

    def checkpoint_event(self, event: str, plan_id: str,
                         completed_count: int = 0):
        """Checkpoint 事件。"""
        if not self.enabled:
            return
        code = "CHECKPOINT_SAVE" if event == "save" else "CHECKPOINT_RESTORE"
        self._w(code, {
            "plan_id": plan_id, "completed_count": completed_count,
        })

    # ── 结束 ──────────────────────────────────────────

    def finish(self, final_text: str = "", error: str = ""):
        if not self.enabled or self._finished:
            return
        self._finished = True
        elapsed = time.time() - self._start_time

        self.memory_state("final")

        status = "TASK_ERROR" if error else "TASK_DONE"
        summary = {
            "timestamp": _now_iso(),
            "task_id": self._task_id,
            "status": status,
            "total_elapsed_s": round(elapsed, 1),
            "total_iterations": self._iteration_count + 1,
            "total_tool_calls": self._total_tool_calls,
            "tokens_in_est": self._total_tokens_in,
            "tokens_out_est": self._total_tokens_out,
            "final_text_chars": len(final_text),
            "error": error[:300] if error else "",
            "tool_call_stats": self._stats["tool_calls"],
            "tool_time_stats": {k: round(v, 2) for k, v in self._stats["tool_times"].items()},
            "error_count": len(self._stats["errors"]),
            "errors": self._stats["errors"][:10],
            "status_code_sequence": self._stats["status_codes"],
            "token_trend": self._stats["token_estimates"][:50],
            # v1.0 新增
            "dag_steps": self._stats["dag_steps"],
            "critic_issues": self._stats["critic_issues"],
            "blackboard_writes": self._stats["blackboard_writes"],
        }

        loop_count = sum(1 for s in self._stats["status_codes"] if s == "LOGIC_LOOP")
        if loop_count > 0:
            summary["logic_loop_count"] = loop_count

        self._w("TRACE_END", summary)
        self._write("=" * 80 + "\n")

    def ensure_finished(self):
        if not self._finished:
            self.finish(error="TRACE_END safety net - task did not call finish()")

    # ── 运行面板数据 ──────────────────────────────────

    def get_dashboard_data(self) -> dict:
        """
        返回运行面板数据（供 web_ui 展示）。

        {
          "trace_id": "...",
          "elapsed_s": 30.5,
          "iterations": 8,
          "tool_calls": {"execute_shell": 5, "write_file": 3},
          "errors": [...],
          "dag_steps": [...],
          "loop_count": 0,
          "token_usage": [12000, 15000, ...],
        }
        """
        if not self.enabled:
            return {}

        return {
            "trace_id": self.span_context.trace_id if hasattr(self, 'span_context') else "",
            "task_id": self._task_id,
            "elapsed_s": round(time.time() - self._start_time, 1),
            "iterations": self._iteration_count + 1,
            "tool_calls": dict(self._stats["tool_calls"]),
            "tool_times": {k: round(v, 2) for k, v in self._stats["tool_times"].items()},
            "errors": self._stats["errors"][:10],
            "dag_steps": self._stats["dag_steps"],
            "critic_issues": self._stats["critic_issues"],
            "loop_count": sum(1 for s in self._stats["status_codes"] if s == "LOGIC_LOOP"),
            "token_usage": self._stats["token_estimates"],
            "blackboard_writes": self._stats["blackboard_writes"],
        }


# ═══════════════════════════════════════════════════════
# NullTracer（零开销占位）
# ═══════════════════════════════════════════════════════

class NullTracer(Tracer):
    def __init__(self):
        self.enabled = False
        self._finished = True
    def start(self, *a, **kw): pass
    def iteration_begin(self, *a, **kw): pass
    def tool_call(self, *a, **kw): pass
    def llm_output(self, *a, **kw): pass
    def data_chain(self, *a, **kw): pass
    def iteration_end(self, *a, **kw): pass
    def memory_state(self, *a, **kw): pass
    def subagent_event(self, *a, **kw): pass
    def skill_loaded(self, *a, **kw): pass
    def compress_event(self, *a, **kw): pass
    def finish(self, *a, **kw): pass
    def ensure_finished(self): pass
    def should_force_break(self): return False
    def reset_loop(self): pass
    def get_loop_hint(self): return ""
    # v1.0 新增
    def dag_step_begin(self, *a, **kw): pass
    def dag_step_end(self, *a, **kw): pass
    def critic_event(self, *a, **kw): pass
    def blackboard_write(self, *a, **kw): pass
    def checkpoint_event(self, *a, **kw): pass
    def get_dashboard_data(self): return {}


# ═══════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════

def make_tracer(config: dict, workspace: Path = None,
                session_id: str = "") -> Tracer:
    if not config or not config.get("enabled", False):
        return NullTracer()
    try:
        return Tracer(config, workspace, session_id)
    except Exception:
        return NullTracer()
