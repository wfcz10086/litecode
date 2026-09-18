"""orchestrator/helpers.py — SSE 事件构造 + 占位符替换 + 原生 tool step 执行器."""
from __future__ import annotations

import json
import uuid


# ═══════════════════════════════════════════════════════
# SSE 事件（与现有格式兼容）
# ═══════════════════════════════════════════════════════

def _sse_event(event_type: str, data, model: str = "openclaw") -> str:
    d = {
        "id": f"orch-{uuid.uuid4().hex[:6]}",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {event_type: data},
            "finish_reason": None,
        }]
    }
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def _sse_content(text: str, model: str = "openclaw") -> str:
    return f'data: {json.dumps({"id": f"orch-{uuid.uuid4().hex[:6]}", "object": "chat.completion.chunk", "model": model, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}, ensure_ascii=False)}\n\n'


def _sse_step_status(step_id: str, label: str, status: str,
                     detail: str = "", model: str = "openclaw",
                     phase=None, output_delta=None, elapsed_ms=None,
                     stream=None) -> str:
    d: dict = {"step_id": step_id, "label": label, "status": status, "detail": detail}
    if phase is not None:
        d["phase"] = phase
    if output_delta is not None:
        d["output_delta"] = output_delta
    if elapsed_ms is not None:
        d["elapsed_ms"] = elapsed_ms
    if stream is not None:
        d["stream"] = stream
    return _sse_event("orchestrator_step", d, model)


# ═══════════════════════════════════════════════════════
# [Round 4 · 2026-07-24] 原生 tool step 执行器
# ═══════════════════════════════════════════════════════

_TPL_RE = None
_TPL_SINGLE_RE = None
_TPL_JSON_RE = None          # [2026-09-05] ${step:id:json:a.b[0].c} 内联
_TPL_JSON_SINGLE_RE = None   # 单占位版, 返回取到的原值 (对象)
_CODE_FENCE_RE = None


def _try_json_loads(s):
    """LLM 输出多形态归一化为 Python 对象.

    容错级别 (依次尝试):
      1. 直接 loads (perfect JSON)
      2. 剥 ```json ... ``` 代码围栏
      3. 找首个 '[' 到末个 ']' (数组围拢在废话中间)
      4. 找首个 '{' 到末个 '}' (单对象)
      5. raw_decode 迭代 — LLM 出多个裸 {...} 块 → 自动 wrap 成 list
    """
    if not isinstance(s, str):
        return None
    import re as _re
    global _CODE_FENCE_RE
    if _CODE_FENCE_RE is None:
        _CODE_FENCE_RE = _re.compile(r"```(?:json)?\s*(.+?)\s*```", _re.DOTALL)
    txt = s.strip()
    # Step 1: 整段直接 loads
    try:
        return json.loads(txt)
    except Exception:
        pass
    # Step 2: 剥围栏
    m = _CODE_FENCE_RE.search(txt)
    if m:
        inner = m.group(1).strip()
        try:
            return json.loads(inner)
        except Exception:
            txt = inner
    # Step 3-4: 数组 / 单对象 boundary
    for start_ch, end_ch in (("[", "]"), ("{", "}")):
        i = txt.find(start_ch)
        j = txt.rfind(end_ch)
        if 0 <= i < j:
            try:
                return json.loads(txt[i:j+1])
            except Exception:
                continue
    # Step 5: LLM 出多裸对象没 wrap 数组 — raw_decode 迭代拾取
    dec = json.JSONDecoder()
    out = []
    pos = 0
    n = len(txt)
    while pos < n:
        while pos < n and txt[pos] not in "{[":
            pos += 1
        if pos >= n:
            break
        try:
            obj, end = dec.raw_decode(txt, pos)
            out.append(obj)
            pos = end
        except Exception:
            pos += 1
    if len(out) >= 2:
        return out
    if len(out) == 1:
        return out[0]
    return None


def _substitute_step_placeholders(v, prior_results, shell_quote=False):
    """把 ${step:<id>:output} 换成前步 output.
    递归处理 dict / list / str.

    [Round 5 · 2026-07-24] Auto-JSON: 若 str 值 == 单个占位符 (仅空白围绕),
    且前步输出能解析成 JSON, 则替换为解析后的 Python 对象 (list/dict/...);
    否则退化为纯字符串替换 (兼容 Round 4).

    [2026-08-25 fix] shell_quote=True (execute_shell 步骤专用): 前步 output
    常含换行/空格/引号, 直接字符串拼接进 shell 命令会把一条命令拆成多行,
    多出的行被当独立命令执行 → "xxx: command not found". 用 shlex.quote()
    把替换值转成安全的单个 shell token, 同时放弃 auto-JSON (shell 场景要的
    是字符串 token 不是 Python 对象).
    """
    global _TPL_RE, _TPL_SINGLE_RE, _TPL_JSON_RE, _TPL_JSON_SINGLE_RE
    if _TPL_RE is None:
        import re as _re
        _TPL_RE = _re.compile(r"\$\{step:([a-zA-Z0-9_\-]+):output\}")
        _TPL_SINGLE_RE = _re.compile(r"^\s*\$\{step:([a-zA-Z0-9_\-]+):output\}\s*$")
        # [2026-09-05] ${step:id:json:<jq路径>} — 前步 output 解析 JSON 后取字段,
        # 复用 core/_when_eval._jq_get, 与 when 条件的 $step.x.json.path 取值口径一致。
        _TPL_JSON_RE = _re.compile(r"\$\{step:([a-zA-Z0-9_\-]+):json:([^}]+)\}")
        _TPL_JSON_SINGLE_RE = _re.compile(r"^\s*\$\{step:([a-zA-Z0-9_\-]+):json:([^}]+)\}\s*$")
    assert (_TPL_RE is not None and _TPL_SINGLE_RE is not None
            and _TPL_JSON_RE is not None and _TPL_JSON_SINGLE_RE is not None)

    def _jq_field(sid, path):
        """取前步 output 解析成 JSON 后的 jq 路径值, 返回 (value, ok)。"""
        r = prior_results.get(sid)
        if r is None:
            return f"<no such step: {sid}>", False
        parsed = _try_json_loads(getattr(r, "output", "") or "")
        if parsed is None:
            return f"<step {sid} output 非 JSON>", False
        try:
            from core._when_eval import _jq_get
        except Exception:
            from .._when_eval import _jq_get  # type: ignore
        val, err = _jq_get(parsed, path)
        if err:
            return f"<{sid}.json.{path}: {err}>", False
        return val, True

    if isinstance(v, str):
        # 场景 A0: 整个 v 是单个 json 占位符 → 返回取到的原值 (对象/标量)
        mj = _TPL_JSON_SINGLE_RE.match(v)
        if mj:
            val, ok = _jq_field(mj.group(1), mj.group(2))
            if not ok:
                return val
            if shell_quote:
                import shlex
                return shlex.quote(val if isinstance(val, str) else json.dumps(val, ensure_ascii=False))
            return val
        # 场景 A: 整个 v 就是单 output 占位符 → 试 auto-JSON (shell 场景跳过, 直接转义字符串)
        m_single = _TPL_SINGLE_RE.match(v)
        if m_single:
            sid = m_single.group(1)
            r = prior_results.get(sid)
            if r is None:
                return f"<no such step: {sid}>"
            raw = getattr(r, "output", "") or ""
            if shell_quote:
                import shlex
                return shlex.quote(raw)
            parsed = _try_json_loads(raw)
            if parsed is not None:
                return parsed
            return raw
        # 场景 B: 字符串里混有占位符 → 先替 json 字段占位, 再替 output 占位
        def _repl_json(m):
            val, ok = _jq_field(m.group(1), m.group(2))
            s = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
            if shell_quote:
                import shlex
                return shlex.quote(s)
            return s
        v = _TPL_JSON_RE.sub(_repl_json, v)
        def _repl(m):
            sid = m.group(1)
            r = prior_results.get(sid)
            if r is None:
                return f"<no such step: {sid}>"
            out = getattr(r, "output", "") or ""
            if shell_quote:
                import shlex
                return shlex.quote(out)
            return out
        return _TPL_RE.sub(_repl, v)
    if isinstance(v, dict):
        return {k: _substitute_step_placeholders(x, prior_results, shell_quote) for k, x in v.items()}
    if isinstance(v, list):
        return [_substitute_step_placeholders(x, prior_results, shell_quote) for x in v]
    return v


async def _run_tool_step(spec, prior_results, sse_emit, label):
    """agent_type=='tool' 时执行. 直接 plugins.registry.call.
    output 是人类可读字符串, 但也会附一段 JSON 尾巴供下游解析."""
    try:
        from plugins.registry import registry, scan_once_if_empty
    except Exception as e:
        return f"[tool-error] plugin registry import failed: {e!r}"
    scan_once_if_empty()
    name = spec.tool_name or ""
    if not name:
        return f"[tool-error] step {spec.id}: tool_name empty (agent_type=tool 时必须指定 tool_name)"
    if registry.get(name) is None:
        avail = sorted(registry.names())
        return f"[tool-error] tool '{name}' not registered. available: {avail}"
    args = _substitute_step_placeholders(spec.tool_args or {}, prior_results,
                                          shell_quote=(name == "execute_shell"))
    if sse_emit:
        try:
            await sse_emit(_sse_content(f"\n🔧 [tool] {name}({str(args)[:120]})\n"))
        except Exception:
            pass
    ctx = {"job_id": spec.id}
    try:
        ret = await registry.call(name, args if isinstance(args, dict) else {}, ctx)
    except Exception as e:
        return f"[tool-error] {name} crashed: {e!r}"
    if not isinstance(ret, dict):
        return f"[tool] {name} → {ret}"
    if ret.get("ok") is False:
        return f"[tool-error] {name}: {ret.get('error') or ret}"
    head = ret.get("result") or "ok"
    files = ret.get("files") or []
    tail = ""
    if files:
        tail = "\n\nfiles:\n" + "\n".join(f"  - {p}" for p in files)
    tail += "\n\n<tool_json>" + json.dumps(ret, ensure_ascii=False)[:1500] + "</tool_json>"
    return f"[tool:{name}] {head}{tail}"


async def _run_dag_step(spec, prior_results, sse_emit, label, orch):
    """[#2 2026-09-05] agent_type=='dag' 时执行: 加载并运行 spec.dag_name 指向的子 DAG,
    把它的 final_output 当本步 output。复用父 orchestrator 的 run_subagent_fn, 带递归深度上限。"""
    name = getattr(spec, "dag_name", "") or ""
    if not name:
        return f"[subdag-error] step {spec.id}: dag_name empty (agent_type=dag 时必须指定 dag_name)"
    depth = getattr(orch, "_subdag_depth", 0)
    if depth >= 3:
        return f"[subdag-error] step {spec.id}: 子 DAG 嵌套超过 3 层 (防无限递归), dag={name}"
    try:
        from lib.dag_schema import load_dag, dag_from_dict
    except Exception as e:  # pragma: no cover
        return f"[subdag-error] step {spec.id}: 加载子 DAG 模块失败: {e!r}"
    d = load_dag(orch._workspace, name)
    if not d:
        return f"[subdag-error] step {spec.id}: 子 DAG '{name}' 不存在 (workspace 下无此 DAG)"
    try:
        plan = dag_from_dict(d)
    except Exception as e:
        return f"[subdag-error] step {spec.id}: 子 DAG '{name}' 解析失败: {e!r}"
    try:
        from core.orchestrator.dag import DAGOrchestrator
    except Exception:
        from .dag import DAGOrchestrator  # type: ignore
    nested = DAGOrchestrator(
        run_subagent_fn=orch._run,
        workspace=orch._workspace,
        parent_sid=getattr(orch, "_parent_sid", None),
        pause_check_fn=getattr(orch, "_pause_check", None),
    )
    nested._subdag_depth = depth + 1   # 递归深度传递
    try:
        res = await nested.execute(plan, sse_emit)
    except Exception as e:
        return f"[subdag-error] step {spec.id}: 子 DAG '{name}' 执行异常: {e!r}"
    out = (getattr(res, "final_output", "") or "").strip()
    ok = getattr(res, "success", False)
    tag = f"[子DAG:{name}]" if ok else f"[子DAG:{name} 部分失败]"
    return f"{tag} {out}"
