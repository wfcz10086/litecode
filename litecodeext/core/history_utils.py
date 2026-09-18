"""history_utils.py — 从 litecode_server.py 抽出的历史/工具/清洗辅助函数.

无副作用, 纯函数式, 不引用 litecode_server 中的可变全局状态.
拆出的目的是把 litecode_server 从 4151 行降到 3550 行以内, 让主推理循环更好读.
"""
import json, os, re, time, logging
from pathlib import Path

log = logging.getLogger("litecode_server")

# _auto_rotate_session 用到了 WORKSPACE (litecode_server 全局, 源自 lib.config).
# 不循环导入: history_utils 是新文件, lib.config 不 import 它.
from lib.config import WORKSPACE


def _clear_old_tool_results(messages: list, keep_recent: int = 6,
                             max_total_chars: int = 15000) -> list:
    """
    [v1.0] Tool Result 激进淘汰 -- 灵感来自 Claude Code 的滑动窗口策略。

    v6 问题: keep_recent=4, max_total_chars=8000, 旧 result 累积导致 token 膨胀。
    v7 改进:
    - keep_recent 降到 2: 只保留最近 2 个 tool result 完整内容
    - max_total_chars 降到 3000: 硬预算, 超出后旧 result 截到 50c
    - 旧 result 只保留工具名+字符数, 不保留内容
    """
    tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_indices) <= keep_recent:
        return messages

    # 第一轮: 条目淘汰 — 超出 keep_recent 的旧 result 截短
    to_clear = tool_indices[:-keep_recent]
    for idx in to_clear:
        content = messages[idx].get("content") or ""
        if not isinstance(content, str) or len(content) <= 200:
            continue
        # 回溯找对应的 tool_call name
        tool_name = ""
        call_id = messages[idx].get("tool_call_id", "")
        if call_id:
            for j in range(idx - 1, max(idx - 5, -1), -1):
                for tc in messages[j].get("tool_calls", []):
                    if tc.get("id") == call_id:
                        tool_name = tc.get("function", {}).get("name", "")
                        break
                if tool_name:
                    break
        # [v1.0] 旧 result 只保留 50c 摘要
        messages[idx]["content"] = (
            f"[cleared:{tool_name or '?'} {len(content)}c] {content[:50]}..."
        )

    # [v1.0] 第二轮: 总量预算 — 超预算时截到 50c
    total_chars = sum(
        len(messages[i].get("content") or "")
        for i in tool_indices if i < len(messages)
    )
    if total_chars > max_total_chars:
        for idx in tool_indices:
            if idx >= len(messages):
                continue
            content = messages[idx].get("content") or ""
            if len(content) > 100:
                messages[idx]["content"] = content[:50] + "..."
                total_chars -= (len(content) - 53)
                if total_chars <= max_total_chars:
                    break

    return messages


def _count_orphan_user_tail(history: list) -> int:
    """
    [orphan-user-detect 2026-05-23] 从 history 末尾倒查, 数最后一个 assistant msg 之后
    有几条 user msg 没被回应. 用于检测 "L4 模式": agent 连续 N 轮 空回, user 又追发了 N 条,
    history 末尾累积了 ≥2 个 orphan user → 给新 user msg prepend 强制 action prefix.

    [P0-fix 2026-05-23] 跳过 LiteCode 自注入的 pseudo-user (以 [SYSTEM-FORCE-ACTION] 开头),
    它们不是真用户 msg, 不应计入 orphan 数 (否则 prefix 自己 prefix 自己, 实测看到 orphan_n
    被自己的 FORCE-ACTION 推高).

    返回 orphan user 数量 (0 = 上一轮有正常 assistant 回, 不是 orphan).
    """
    count = 0
    for m in reversed(history):
        if m.get("role") == "user":
            _c = m.get("content", "")
            # 多模态 list 内取第一个 text part 看是否 SYSTEM-FORCE-ACTION
            if isinstance(_c, list):
                for _p in _c:
                    if isinstance(_p, dict) and _p.get("type") == "text":
                        _c = _p.get("text", "")
                        break
                else:
                    _c = ""
            if isinstance(_c, str) and _c.startswith("[SYSTEM-FORCE-ACTION]"):
                continue  # P0-fix: 跳过自注入伪 user
            count += 1
        elif m.get("role") in ("assistant", "tool"):
            break
    return count


def _extract_first_user_summary(history: list, max_chars: int = 800) -> str:
    """从 history 找第一条 user msg (任务描述), 截断到 max_chars."""
    for m in history:
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, list):
                # 多模态: 取第一个 text part
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "text":
                        c = p.get("text", "")
                        break
                else:
                    c = ""
            if isinstance(c, str) and c.strip():
                return c[:max_chars]
    return ""


def _extract_last_completed_turn(history: list) -> list:
    """
    [auto-session-rotate 2026-05-23] 从 history 倒序找最近一个完整的 turn:
    user → assistant → tool* → 下一个 user / 结束 之间的所有 msg.
    返回这一组 msgs (空 list = 没找到完整 turn).
    用于 rotate 时保留最后一段有产出的对话作为上下文.
    """
    if not history:
        return []
    # 找最后一个 assistant msg
    last_assistant_idx = -1
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "assistant":
            last_assistant_idx = i
            break
    if last_assistant_idx < 0:
        return []
    # 找它前面的 user msg
    user_idx = -1
    for i in range(last_assistant_idx - 1, -1, -1):
        if history[i].get("role") == "user":
            user_idx = i
            break
    if user_idx < 0:
        return []
    # 从 user_idx 到 last_assistant_idx 后的 tool 都带上 (但跳过下一个 user 之后)
    end_idx = last_assistant_idx + 1
    while end_idx < len(history) and history[end_idx].get("role") in ("tool", "assistant"):
        end_idx += 1
    return list(history[user_idx:end_idx])


def _auto_rotate_session(src_sid: str, base_history: list, sessions_dir,
                          first_user: str, last_turn: list, reason: str = "orphan_overflow"):
    """
    [auto-session-rotate 2026-05-23] 创建一个 fresh session 继承老 session 的关键上下文.

    动作:
    1. [P2-fix] 限流: 老 session meta 里若已有 auto_rotated_to, 检查 rotate 链长度 > 3 拒绝
       (防止 fork_a → fork_b → fork_c → ... 套娃)
    2. 用 session_fork.fork_session 复制 SOUL/USER/MEMORY 等 init 文件到新 sid
    3. 新 session 的 history.json 放: 第一条 user msg + AUTO-ROTATE hint + last_turn
       + [P1-fix] MEMORY.md 摘要 + 项目状态 (从 ls 项目目录最近改的文件抽)
    4. [P2-fix] 老 session meta.json 写 auto_rotated_to=new_sid + assistant tail msg
       让老 session 也能看到迁移历史
    5. 返回 new_sid (失败返回 None)
    """
    try:
        from lib.session_fork import fork_session
        from pathlib import Path
        # P2-fix: 限流 — 检查 src_sid 是否本身已经是 N 级 rotated, 链长 > 3 拒绝
        _chain_len = 0
        _cur = src_sid
        _seen = set()
        while _cur and _cur not in _seen and _chain_len <= 4:
            _seen.add(_cur)
            _meta_path = Path(sessions_dir) / _cur / "meta.json"
            if not _meta_path.exists():
                break
            try:
                _meta = json.loads(_meta_path.read_text())
                _cur = _meta.get("auto_rotated_from")
                if _cur:
                    _chain_len += 1
            except Exception:
                break
        if _chain_len >= 3:
            import logging
            logging.getLogger("openclaw").warning(
                f"  [auto-rotate] REFUSED: rotate chain length={_chain_len} >= 3 (从 {src_sid} 反查), "
                f"避免套娃. 用户应该新建 session 或简化指令."
            )
            return None

        new_sid = fork_session(WORKSPACE, src_sid, until_turn=0, sessions_dir=sessions_dir)
        if not new_sid:
            return None
        # 重写 new sid 的 session 文件: 只保留 first_user + 最近完整 turn + 项目状态摘要
        # LiteCode 实际 disk_load 读 sessions/<sid>.json 单文件 (不是 <sid>/history.json),
        # 所以同时写两个路径以兼容.
        from pathlib import Path
        import time as _time
        # [P1-fix 2026-05-23] 提取 MEMORY.md 摘要 + 项目状态作为 SYSTEM context
        _project_context_parts = []
        # 1. 老 session 的 MEMORY.md (lessons learned)
        _mem_path = Path(sessions_dir) / src_sid / "MEMORY.md"
        if _mem_path.exists():
            try:
                _mem_text = _mem_path.read_text(errors="replace")[:800]
                if _mem_text.strip():
                    _project_context_parts.append(f"[继承自 {src_sid[:8]} MEMORY.md]\n{_mem_text.strip()}")
            except Exception:
                pass
        # 2. 当前项目目录最近改动的 N 个文件 (通过 first_user 推断项目根)
        import re as _re_pr
        _project_hint = ""
        _path_match = _re_pr.search(r'/tmp/(\w[\w/-]+)', first_user or "")
        if _path_match:
            _project_root = "/tmp/" + _path_match.group(1).split("/")[0]
            try:
                import os as _os
                if _os.path.isdir(_project_root):
                    _files = sorted(
                        ((f, _os.path.getmtime(_os.path.join(_project_root, f)))
                         for f in _os.listdir(_project_root) if not f.startswith(".")),
                        key=lambda x: -x[1]
                    )[:8]
                    if _files:
                        _project_hint = (
                            f"[项目最近改动 {_project_root}]\n"
                            + "\n".join(f"  - {fn}" for fn, _ in _files)
                        )
            except Exception:
                pass
        if _project_hint:
            _project_context_parts.append(_project_hint)
        _project_context = "\n\n".join(_project_context_parts)

        new_hist = []
        if first_user:
            new_hist.append({"role": "user", "content": first_user})
            _rotate_hint = (
                f"[AUTO-ROTATE 2026-05-23] 已从 session `{src_sid[:12]}` 继承上下文 (原因: {reason}). "
                f"老 session 因 vLLM 状态污染连续空回, 这里保留任务描述 + 最近一段有产出的对话 + 项目状态摘要."
            )
            if _project_context:
                _rotate_hint += "\n\n" + _project_context
            new_hist.append({"role": "assistant", "content": _rotate_hint})
        # 加最近 completed turn (如果有)
        # [P0-fix 2026-05-23] 去重: 如果 last_turn[0] 是同一条 first_user (因为
        # _extract_last_completed_turn 倒序找时可能拿到同一条任务描述), 跳过避免重复.
        if last_turn:
            _first_turn_content = ""
            if last_turn[0].get("role") == "user":
                _fc = last_turn[0].get("content", "")
                if isinstance(_fc, str):
                    _first_turn_content = _fc
            # 简单匹配: 前 200 字相同就认作同一条
            if (_first_turn_content and first_user
                    and _first_turn_content[:200] == first_user[:200]):
                new_hist.extend(last_turn[1:])  # 跳过重复的首条 user
            else:
                new_hist.extend(last_turn)
        # 关键: 写 sessions/<sid>.json (单文件 disk_load 实际读取的)
        single_file = Path(sessions_dir) / f"{new_sid}.json"
        try:
            single_file.write_text(json.dumps({
                "sid": new_sid,
                "msgs": new_hist,
                "ts": _time.time(),
                "artifacts": [],
            }, ensure_ascii=False, indent=2))
        except Exception:
            pass
        # 也写 history.json 给 web UI / fork_meta 配套
        hist_path = Path(sessions_dir) / new_sid / "history.json"
        try:
            hist_path.write_text(json.dumps(new_hist, ensure_ascii=False, indent=2))
        except Exception:
            pass
        # 写 meta.json (跟新建 session 时一样的 name)
        meta_path = Path(sessions_dir) / new_sid / "meta.json"
        try:
            meta_path.write_text(json.dumps({
                "name": f"[ROTATED FROM {src_sid[:8]}]",
                "ts_created": _time.time(),
                "auto_rotated_from": src_sid,
                "rotated_reason": reason,
            }, ensure_ascii=False, indent=2))
        except Exception:
            pass

        # [P2-fix 2026-05-23] 老 session 写反向链接 auto_rotated_to + 末尾追加 assistant msg
        # 让用户在老 session 看到 "任务迁移到 fork_xxx", 全链路可追溯
        try:
            _src_meta_path = Path(sessions_dir) / src_sid / "meta.json"
            _src_meta = {}
            if _src_meta_path.exists():
                try:
                    _src_meta = json.loads(_src_meta_path.read_text())
                except Exception:
                    _src_meta = {}
            _src_meta["auto_rotated_to"] = new_sid
            _src_meta["rotated_at"] = _time.time()
            _src_meta_path.write_text(json.dumps(_src_meta, ensure_ascii=False, indent=2))
        except Exception:
            pass
        # 老 session 单文件 .json 追加 marker assistant msg
        try:
            _src_single = Path(sessions_dir) / f"{src_sid}.json"
            if _src_single.exists():
                _sd = json.loads(_src_single.read_text())
                _msgs = _sd.get("msgs", [])
                # 避免重复 append (检查 tail msg)
                _tail = _msgs[-1] if _msgs else {}
                if not (_tail.get("role") == "assistant"
                        and "[ARCHIVED]" in (_tail.get("content","") or "")):
                    _msgs.append({
                        "role": "assistant",
                        "content": f"[ARCHIVED 2026-05-23] 本 session 因 vLLM 状态污染连续空回, "
                                   f"任务已自动迁移到 fork session `{new_sid}` (原因: {reason}). "
                                   f"如需继续, 请打开 {new_sid}.",
                    })
                    _sd["msgs"] = _msgs
                    _src_single.write_text(json.dumps(_sd, ensure_ascii=False, indent=2))
        except Exception:
            pass

        return new_sid
    except Exception as _re:
        import logging
        logging.getLogger("openclaw").warning(f"  [auto-rotate] failed: {_re}")
        return None


def _sanitize_history(messages: list) -> list:
    """
    修复 session history 防止 vLLM/MiniMax 400 错误。
    关键: 必须直接写 tc["function"] 而不是用 tc.get("function",{}) 临时对象。
    """
    def _fix_args(args) -> str:
        if isinstance(args, str):
            s = args.strip()
            if not s:
                return "{}"
            try:
                json.loads(s)
                return s
            except Exception:
                return "{}"
        elif isinstance(args, dict):
            try:
                return json.dumps(args, ensure_ascii=False)
            except Exception:
                return "{}"
        return "{}"

    # Pass 1: arguments 规范化 + 补全缺失字段
    for m in messages:
        tool_calls = m.get("tool_calls")
        if not tool_calls:
            continue
        for tc in tool_calls:
            # 确保 function key 存在并直接写回 tc（不用临时变量）
            if not isinstance(tc.get("function"), dict):
                tc["function"] = {"name": tc.get("name", "unknown"), "arguments": "{}"}
            # 确保 id 存在
            if not tc.get("id"):
                import uuid as _uuid
                tc["id"] = f"call_{_uuid.uuid4().hex[:8]}"
            # 修复 arguments（直接写 tc["function"]）
            tc["function"]["arguments"] = _fix_args(tc["function"].get("arguments"))
            if not tc["function"].get("name"):
                tc["function"]["name"] = tc.get("name", "unknown")
        # content=None 部分 API 不接受
        if m.get("role") == "assistant" and m.get("content") is None:
            m["content"] = ""

    # Pass 2: 移除 dangling tool_calls（有调用无对应 tool_result）
    answered_ids: set = set()
    for m in messages:
        if m.get("role") == "tool":
            cid = m.get("tool_call_id")
            if cid:
                answered_ids.add(cid)

    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            clean = [tc for tc in m["tool_calls"] if tc.get("id") in answered_ids]
            if len(clean) < len(m["tool_calls"]):
                if clean:
                    m["tool_calls"] = clean
                else:
                    m.pop("tool_calls", None)

    # Pass 3: 最终验证 — 确保所有 arguments 是合法 JSON 字符串（防御性双检）
    for m in messages:
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            args = fn.get("arguments", "{}")
            if isinstance(args, dict):
                fn["arguments"] = json.dumps(args, ensure_ascii=False)
            elif isinstance(args, str):
                s = args.strip()
                try:
                    json.loads(s)
                    fn["arguments"] = s
                except Exception:
                    fn["arguments"] = "{}"
            else:
                fn["arguments"] = "{}"

    # Pass 4: 移除 tool role 消息如果对应的 tool_call_id 不存在于任何 assistant 消息
    all_tc_ids: set = set()
    for m in messages:
        for tc in m.get("tool_calls") or []:
            tid = tc.get("id")
            if tid:
                all_tc_ids.add(tid)
    messages[:] = [
        m for m in messages
        if not (m.get("role") == "tool" and m.get("tool_call_id") not in all_tc_ids)
    ]

    return messages


def _nuke_tool_history(messages: list) -> list:
    """
    核武器级别清理：当连续 400 错误时调用。
    完全移除所有 tool_calls 和 tool role 消息，只保留 user/assistant 文本。
    保证后续 LLM 调用不再 400。
    """
    clean = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            continue  # 完全删除 tool result
        if role == "assistant":
            m_copy = {k: v for k, v in m.items() if k != "tool_calls"}
            if not m_copy.get("content"):
                m_copy["content"] = "(tool call removed for error recovery)"
            clean.append(m_copy)
        else:
            clean.append(m)
    return clean


def _fmt_args(name: str, args: dict) -> str:
    """格式化工具调用参数用于日志/SSE显示。"""
    if name == "execute_shell":
        return args.get("command", "")[:120]
    if name in ("write_file", "patch_file", "read_file"):
        return args.get("filepath", "")
    if name in ("web_fetch",):
        return args.get("url", "")[:100]
    if name in ("web_search",):
        return args.get("query", "")[:100]
    if name in ("find_files",):
        return f"{args.get('pattern','')} in {args.get('path','.')}"
    if name in ("search_code",):
        return f"{args.get('query','')} in {args.get('path','.')}"
    if name in ("get_tree",):
        return args.get("path", ".")
    if name in ("load_skill",):
        return args.get("name", "")
    if name in ("spawn_agent",):
        return f"[{args.get('agent_type','')}] {args.get('task','')[:80]}"
    if name in ("update_profile",):
        return f"{args.get('file','')} action={args.get('action','')}"
    if name in ("save_memory",):
        return f"[{args.get('section','')}] {args.get('content','')[:60]}"
    vals = list(args.values())
    return str(vals[0])[:100] if vals else ""

def _enhance_tool_error(tool_name: str, result: str, args: dict) -> str:
    """[OPT] P2-B: 根据错误类型注入调试建议，减少模型盲目重试。"""
    hints = []

    # 正则/编码类错误
    if ("已提取" in result and ("null" in result or ": None" in result)) or \
       "匹配不到" in result:
        hints.append("先用 python3 -c \"print(repr(text[:300]))\" 检查字符编码（全角/半角冒号等）")

    # SyntaxError after patch
    if "SyntaxError" in result:
        hints.append("代码结构可能被 patch 破坏，考虑用 write_file 完全重写文件")

    # Host unreachable / DNS
    if any(kw in result for kw in ("Host unreachable", "DNS", "name resolution",
                                     "Connection refused", "No route")):
        hints.append("该域名不可达，请改用 web_search 工具或已配置的搜索源")

    # ImportError / ModuleNotFoundError
    if "ModuleNotFoundError" in result or "ImportError" in result:
        mod = re.search(r"No module named '(\S+?)'", result)
        if mod:
            hints.append(f"先安装: pip3 install {mod.group(1)} --break-system-packages")

    # AttributeError / TypeError - 方法不存在
    if "AttributeError" in result or "has no attribute" in result:
        hints.append("用 python3 -c \"import sys; sys.path.insert(0,'.'); from module import Cls; print([m for m in dir(Cls()) if not m.startswith('_')])\" 检查可用方法")

    # DeprecationWarning / exit 0 STDERR - 不是错误
    if "[STDERR_WARNINGS - exit 0" in result:
        hints.append("注意：这是 exit 0，命令成功。STDERR 警告不影响执行，直接继续下一步")

    # 服务器内部错误 - 先隔离import
    if "服务器内部错误" in result or "Internal Server Error" in result:
        hints.append("先隔离问题: cd /project && PYTHONPATH=. python3 -c 'from server.main import create_app; print(create_app())' 而不是重启整个服务")

    # 未知/陌生错误 -> 建议搜索
    unknown_err_patterns = [
        "Unknown error", "ERR_", "ECONNREFUSED", "ETIMEDOUT",
        "ENOENT", "permission denied", "segmentation fault",
    ]
    is_unknown = (
        result.startswith("ERROR") and
        not any(kw in result for kw in ("ModuleNotFoundError", "ImportError",
            "SyntaxError", "AttributeError", "Host unreachable", "Connection refused",
            "服务器内部错误")) and
        any(kw.lower() in result.lower() for kw in unknown_err_patterns)
    )
    if is_unknown:
        # 提取关键错误词用于搜索建议
        err_keyword = re.search(r"(ERR_[A-Z_]+|[A-Z][a-zA-Z]+Error|\w+Exception)", result)
        if err_keyword:
            hints.append(f"陌生错误，建议: web_search '{err_keyword.group(0)} fix' 搜索解决方案")
        else:
            hints.append("陌生错误，建议用 web_search 搜索错误关键词")

    # [v1.0.3] Python/JS/Go traceback 自动提示 search_code_error (GitHub + SO)
    _traceback_patterns = (
        r"Traceback \(most recent call last\):",              # Python
        r"(?:Type|Reference|Syntax|Range)Error:",              # JS
        r"panic:\s*runtime error",                             # Go
        r"thread '.+?' panicked at",                           # Rust
    )
    if any(re.search(p, result) for p in _traceback_patterns) and \
       "search_code_error" not in result:
        hints.append(
            "[RECOMMEND] 这是真实 traceback — 建议下一步调用 "
            "search_code_error(error_text=<完整stderr>) 自动查 GitHub issues + "
            "StackOverflow 已知方案, 比盲改代码快很多"
        )

    if hints:
        return result + "\n[SYSTEM 调试建议: " + "; ".join(hints) + "]"
    return result


_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*.*?</tool_call>", re.DOTALL
)
_FUNCTION_BLOCK_RE = re.compile(
    r"<function=[^>]*>.*?</function>", re.DOTALL
)


def _strip_tool_call_literals(text: str) -> tuple[str, str]:
    """Strip XML tool-call literals from a reasoning chunk (single-chunk, no cross-chunk state).

    Returns (cleaned_text, stripped_for_trace).
    """
    stripped_parts: list[str] = []

    def _collect(m: re.Match) -> str:
        stripped_parts.append(m.group(0))
        return ""

    cleaned = _TOOL_CALL_BLOCK_RE.sub(_collect, text)
    cleaned = _FUNCTION_BLOCK_RE.sub(_collect, cleaned)
    return cleaned, "".join(stripped_parts)
