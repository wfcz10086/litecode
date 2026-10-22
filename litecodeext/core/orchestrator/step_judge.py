"""orchestrator/step_judge.py — DAG 步骤成功判定 (P0-3 2026-10-21)。

原先各类步骤都是 "输出非空且不以失败前缀开头 → 成功":
  - 子 DAG 步返回 "[子DAG:x 部分失败] …" 也非空 → 判成功, 下游拿着失败结果继续跑;
  - 工具步返回 dict 里 exit_code/returncode 非 0、或字符串 "(exit 2)" → 判成功;
  - LLM 步: 子代理改完代码、最后一次相关测试明确失败, 只要它写了段总结 → 判成功。
现在按步骤类型看退出码 / 明确成功标记 / 子代理证据, 并支持步骤级断言 expect。

expect (StepSpec.expect, 可选, 全部满足才算成功):
  "关键字"        输出必须包含
  "!关键字"       输出不能包含
  "re:正则"       输出必须匹配 (re.search, 多行)
"""
from __future__ import annotations

import json
import re
from typing import Optional, Tuple

# 子代理失败标记: 原来只认 [子代理异常]/[子代理已中断], "[子代理超时] …" / "[子代理错误栈] …"
# 被当成功写进黑板, 下游步骤拿着失败文本继续跑
SUBAGENT_FAIL_RE = re.compile(r"^\s*\[子代理(超时|异常|已中断|中断|已取消|嵌套过深|错误栈)")
_TOOL_JSON_RE = re.compile(r"<tool_json>(.*?)</tool_json>", re.S)
_EXIT_KEYS = ("exit_code", "returncode", "exitcode", "exit_status")


def _exit_of(text: str) -> Optional[int]:
    """工具字符串结果里的明确退出码 (执行器约定 ERROR[EXIT=n] / "(exit n)"), 没有返回 None。"""
    t = (text or "").strip()
    m = re.match(r"ERROR\[EXIT=(-?\d+)\]", t) or re.fullmatch(r"\(exit (-?\d+)\)", t)
    if m:
        return int(m.group(1))
    if t.startswith("ERROR[TEST_FAIL]"):
        return 1
    return None


def _judge_tool(output: str) -> Tuple[bool, str]:
    if output.startswith("[tool-error]") or output.lstrip().startswith("ERROR"):
        return False, output[:300]
    m = re.match(r"\[tool\] \S+ → (.*)\Z", output, re.S)
    if m:   # 工具返回了非 dict
        body = m.group(1).strip()
        if body in ("", "None", "null", "{}", "[]"):
            return False, "工具没有返回任何结果"
        code = _exit_of(body)
        if code not in (None, 0):
            return False, f"工具退出码 {code}"
        return True, ""
    tj = _TOOL_JSON_RE.search(output)
    if tj:
        raw = tj.group(1)
        try:
            ret = json.loads(raw)
        except ValueError:
            ret = None
        codes = []
        if isinstance(ret, dict):
            codes = [ret.get(k) for k in _EXIT_KEYS if ret.get(k) is not None]
            if ret.get("ok") is False or ret.get("success") is False:
                return False, "工具结果 ok=false"
        else:   # tool_json 被截断解析不了: 退回正则找退出码
            codes = [int(x) for x in re.findall(r'"(?:%s)"\s*:\s*(-?\d+)' % "|".join(_EXIT_KEYS), raw)]
        for c in codes:
            try:
                if int(c) != 0:
                    return False, f"工具退出码 {c}"
            except (TypeError, ValueError):
                return False, f"工具退出码无法识别: {c!r}"
    return True, ""


def _check_expect(spec, output: str) -> Tuple[bool, str]:
    for exp in list(getattr(spec, "expect", None) or []):
        exp = str(exp)
        if exp.startswith("re:"):
            try:
                hit = re.search(exp[3:], output, re.M) is not None
            except re.error as e:
                return False, f"断言正则无效 {exp!r}: {e}"
            if not hit:
                return False, f"断言未满足: 输出不匹配 {exp[3:]!r}"
        elif exp.startswith("!"):
            if exp[1:] and exp[1:] in output:
                return False, f"断言未满足: 输出不应包含 {exp[1:]!r}"
        elif exp and exp not in output:
            return False, f"断言未满足: 输出缺少 {exp!r}"
    return True, ""


def judge_step(spec, output, evidence_failure: Optional[str] = None) -> Tuple[bool, str]:
    """→ (成功?, 失败原因)。evidence_failure: 子代理证据采集器给出的"最后一次修改后验证明确失败"原因。"""
    out = output if isinstance(output, str) else ("" if output is None else str(output))
    if not out.strip():
        return False, "步骤输出为空"
    kind = getattr(spec, "agent_type", "")
    if kind == "tool":
        ok, why = _judge_tool(out)
    elif kind == "dag":
        name = getattr(spec, "dag_name", "") or ""
        # 明确成功标记: _run_dag_step 只在子 DAG success=True 时给 "[子DAG:<name>]"; 部分失败/错误都不算
        ok = out.startswith(f"[子DAG:{name}]")
        why = "" if ok else f"子 DAG 未成功: {out[:120]}"
    else:
        ok, why = not SUBAGENT_FAIL_RE.match(out), out[:300]
        if ok and evidence_failure:
            ok, why = False, f"[证据] 最后一次修改之后的验证明确失败: {evidence_failure}"
    if not ok:
        return False, why
    return _check_expect(spec, out)
