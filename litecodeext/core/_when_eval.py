"""_when_eval.py — DAG step.when v2 DSL 求值器

设计 (BOOTSTRAP "大型项目实施规则" Step 1):
- 取值: $step.<sid>.<field> / $env.<NAME> / $cfg.<a.b> / $time.now / 字面量
- 运算符: eq ne gt ge lt le contains startswith endswith regex in not_in
          exists not_exists is (status 简写)
- 逻辑: list = AND  /  {"any":[...]} = OR  /  {"all":[...]} = AND  /  {"not": x} = NOT
- 任意嵌套
- 错误 → False + 原因字符串 (不静默吞)
- 兼容旧 v1.3 字符串语法: "success:sid" / "failure:sid" / "contains:sid:kw" / "!<expr>"

主入口:
    evaluate_when(when, ctx) -> (passed: bool, reason: str)

ctx 形如:
    {
      "steps": {
         "fetch": {
            "status": "success",   # success/failure/skipped/running
            "output": "the full string output",
            "json":   {"price": 123, ...} | None,   # output 解析为 JSON (可选)
            "tool_count": 4,
            "duration_ms": 12345,
         },
         ...
      },
      "env": dict(os.environ),
      "cfg": json.load(config.json),
    }
"""
import json
import logging
import os
import re
import time
from typing import Any, Optional, Tuple

log = logging.getLogger(__name__)


_TOKEN_RX = re.compile(r"^\$(step|env|cfg|time)\.([\w.\-\[\]\d]+)$")
# v1.3 旧语法识别: success: / failure: / contains: 开头 (可加 !)
_V1_RX = re.compile(r"^!?(success|failure|contains):")
# v2 字符串语法 (中缀): "$step.x.status eq success"
_V2_INFIX_RX = re.compile(
    r"^(?P<left>\S+)\s+(?P<op>eq|ne|gt|ge|lt|le|contains|startswith|endswith|regex|"
    r"in|not_in|exists|not_exists|is)\b\s*(?P<right>.*?)$"
)


# ── 取值 ─────────────────────────────────────────────────────
def _resolve(token: Any, ctx: dict) -> Tuple[Any, Optional[str]]:
    """解析单个 token (字符串 token 形如 '$step.x.json.price', 或 字面量).
    返回 (value, err); err 非 None 表示取值失败 (调用方决定要不要传播).
    """
    if not isinstance(token, str):
        # dict/list/数字/bool 直接当字面量
        return token, None
    s = token.strip()
    # JSON 字面量 (数字/bool/null/数组/字符串带引号)
    if s and (s[0] in "0123456789-\"'[{" or s in ("true", "false", "null")):
        try:
            return json.loads(s.replace("'", '"')), None
        except Exception:
            pass
    if not s.startswith("$"):
        return s, None  # 裸字符串当字面量
    # [FIX 2026-05-22] $ 后没东西 / 后面不是合法 token → 当普通字面量
    # 之前 "$" / "$54.35" 这种当 token 解析 fail → None → contains None 报错
    m = _TOKEN_RX.match(s)
    if not m:
        return s, None  # 不是合法 $token 也当字面量
    src, path = m.group(1), m.group(2)
    if src == "time" and path == "now":
        return time.time(), None
    if src == "env":
        return os.environ.get(path, ctx.get("env", {}).get(path)), None
    if src == "cfg":
        cur = ctx.get("cfg", {})
        for seg in path.split("."):
            if not isinstance(cur, dict):
                return None, f"$cfg.{path} 路径中断于非 dict"
            if seg not in cur:
                return None, f"$cfg.{path}: 字段 '{seg}' 不存在"
            cur = cur[seg]
        return cur, None
    if src == "step":
        return _resolve_step(path, ctx)
    return None, f"未知取值源: {src}"


def _resolve_step(path: str, ctx: dict) -> Tuple[Any, Optional[str]]:
    """解析 $step.<sid>.<field>[.<json-path>] 取值."""
    parts = path.split(".", 1)
    if not parts:
        return None, "step path 空"
    sid = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    steps = ctx.get("steps", {}) or {}
    if sid not in steps:
        return None, f"step '{sid}' 不存在 (depends_on 未配置?)"
    step = steps[sid]
    if not rest:
        return step, None
    # rest 第一段是 field
    field_parts = rest.split(".", 1)
    field = field_parts[0]
    deeper = field_parts[1] if len(field_parts) > 1 else ""
    if field == "json":
        j = step.get("json")
        if j is None:
            # 尝试从 output 解析
            try:
                j = json.loads(step.get("output", ""))
            except Exception:
                return None, f"$step.{sid}.json: output 不是 JSON"
        return _jq_get(j, deeper) if deeper else (j, None)
    if field in step:
        v = step[field]
        if deeper:
            # 类似 status.something - 不太可能, 但允许
            if isinstance(v, dict):
                return _jq_get(v, deeper)
            return None, f"$step.{sid}.{field}.{deeper}: 字段非 dict"
        return v, None
    return None, f"$step.{sid}.{field}: 字段不存在"


def _jq_get(obj: Any, path: str) -> Tuple[Any, Optional[str]]:
    """简易 jq path 取值: a.b[0].c  支持 dict.field + list[N]."""
    cur = obj
    # tokenize: a.b[0].c → ['a', 'b', '[0]', 'c']
    tokens = re.findall(r"[\w-]+|\[\d+\]", path)
    for tok in tokens:
        if tok.startswith("["):
            idx = int(tok[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return None, f"json 路径 {path}: 数组 [{idx}] 越界"
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                return None, f"json 路径 {path}: 字段 '{tok}' 不存在"
            cur = cur[tok]
    return cur, None


# ── 运算 ─────────────────────────────────────────────────────
def _eq_coerce(a: Any, b: Any) -> bool:
    """eq 比较带数值-字符串互转: "1" eq 1 -> True, "3.14" eq 3.14 -> True."""
    if a == b:
        return True
    if a is None or b is None:
        return False
    try:
        if isinstance(a, (int, float)) and isinstance(b, str):
            return float(a) == float(b)
        if isinstance(b, (int, float)) and isinstance(a, str):
            return float(a) == float(b)
    except Exception:
        pass
    return False


def _eval_op(op: str, left: Any, right: Any) -> Tuple[bool, str]:
    """执行二元运算. 返回 (结果, 文字描述)."""
    desc = f"{_show(left)} {op} {_show(right)}"
    try:
        if op == "eq":
            return _eq_coerce(left, right), desc
        if op == "ne":
            return not _eq_coerce(left, right), desc
        if op == "gt":
            return _num(left) > _num(right), desc
        if op == "ge":
            return _num(left) >= _num(right), desc
        if op == "lt":
            return _num(left) < _num(right), desc
        if op == "le":
            return _num(left) <= _num(right), desc
        if op == "contains":
            return str(right) in str(left), desc
        if op == "startswith":
            return str(left).startswith(str(right)), desc
        if op == "endswith":
            return str(left).endswith(str(right)), desc
        if op == "regex":
            return bool(re.search(str(right), str(left))), desc
        if op == "in":
            if not isinstance(right, (list, tuple, set)):
                return False, f"{desc} → 'in' 右侧必须是数组"
            return left in right, desc
        if op == "not_in":
            if not isinstance(right, (list, tuple, set)):
                return False, f"{desc} → 'not_in' 右侧必须是数组"
            return left not in right, desc
        if op == "exists":
            return left is not None, desc
        if op == "not_exists":
            return left is None, desc
        if op == "is":
            return str(left) == str(right), desc  # status 比较语义同 eq, 容错
        return False, f"未知运算符 {op}"
    except Exception as e:
        return False, f"{desc} → 求值异常 {type(e).__name__}: {e}"


def _num(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        raise ValueError("数值运算时操作数为 None")
    return float(str(v))


def _show(v: Any) -> str:
    s = repr(v) if not isinstance(v, str) else f"{v!r}"
    return s if len(s) <= 60 else s[:60] + "…"


# ── 顶层入口 ─────────────────────────────────────────────────
def evaluate_when(when: Any, ctx: dict) -> Tuple[bool, str]:
    """评估 when 表达式. 返回 (passed, reason).

    when 形态:
      - None / [] / "" / {} → True (无条件)
      - str  → v1.3 旧语法 或 v2 中缀字符串
      - dict → 单条件 (op→[left,right]) 或逻辑组合 (any/all/not)
      - list → 顶层 AND
    """
    if when is None or when == "" or when == [] or when == {}:
        return True, ""
    try:
        return _eval(when, ctx)
    except Exception as e:
        log.warning(f"[when_eval] 求值异常 → 默认 False: {e}")
        return False, f"求值异常 {type(e).__name__}: {e}"


def _eval(node: Any, ctx: dict) -> Tuple[bool, str]:
    if isinstance(node, list):
        # 顶层 AND
        for sub in node:
            ok, reason = _eval(sub, ctx)
            if not ok:
                return False, reason
        return True, "all-passed"

    if isinstance(node, dict):
        if not node:
            return True, ""
        # 逻辑组合优先
        if "any" in node:
            arr = node["any"]
            if not isinstance(arr, list) or not arr:
                return False, "any 必须非空数组"
            reasons = []
            for sub in arr:
                ok, reason = _eval(sub, ctx)
                if ok:
                    return True, f"any → {reason}"
                reasons.append(reason)
            return False, f"any 全失败: {reasons[:3]}"
        if "all" in node:
            arr = node["all"]
            if not isinstance(arr, list) or not arr:
                return False, "all 必须非空数组"
            for sub in arr:
                ok, reason = _eval(sub, ctx)
                if not ok:
                    return False, f"all → {reason}"
            return True, "all-passed"
        if "not" in node:
            ok, reason = _eval(node["not"], ctx)
            return (not ok, f"not({reason})")
        # 二元运算 dict, 如 {"eq": ["$step.x.status", "success"]}
        if len(node) != 1:
            return False, f"dict 必须只有 1 个 key (op 或 any/all/not), 实际: {list(node.keys())}"
        op, operands = next(iter(node.items()))
        if not isinstance(operands, list) or len(operands) not in (1, 2):
            return False, f"运算符 {op} 操作数必须是 1 (exists/not_exists) 或 2 个数组"
        if op in ("exists", "not_exists") and len(operands) == 1:
            lv, lerr = _resolve(operands[0], ctx)
            if lerr and op != "not_exists":
                return False, f"取值失败: {lerr}"
            return _eval_op(op, lv, None)
        lv, lerr = _resolve(operands[0], ctx)
        rv, rerr = _resolve(operands[1], ctx)
        # 取值失败默认当 None, 让操作符自己决定
        if lerr:
            log.debug(f"[when_eval] left resolve err: {lerr}")
        if rerr:
            log.debug(f"[when_eval] right resolve err: {rerr}")
        return _eval_op(op, lv, rv)

    if isinstance(node, str):
        return _eval_str(node, ctx)

    return False, f"不支持的 when 节点类型: {type(node).__name__}"


def _eval_str(s: str, ctx: dict) -> Tuple[bool, str]:
    """字符串条件: 先试 v1.3 兼容, 再试 v2 中缀."""
    s = s.strip()
    if not s:
        return True, ""
    # v1.3 兼容
    if _V1_RX.match(s) or s.startswith("!"):
        return _eval_v1(s, ctx)
    # v2 中缀
    m = _V2_INFIX_RX.match(s)
    if m:
        left_token = m.group("left")
        op = m.group("op")
        right_token = m.group("right").strip()
        # 一元 op (exists/not_exists) 右侧可空
        if op in ("exists", "not_exists"):
            operands = [left_token]
            return _eval({op: operands}, ctx)
        if not right_token:
            return False, f"运算符 {op} 缺右操作数"
        return _eval({op: [left_token, right_token]}, ctx)
    return False, f"无法解析的 when 字符串: {s!r}"


def _eval_v1(s: str, ctx: dict) -> Tuple[bool, str]:
    """v1.3 字符串语法兼容."""
    negate = False
    if s.startswith("!"):
        negate = True
        s = s[1:]
    parts = s.split(":", 2)
    op = parts[0]
    steps = ctx.get("steps", {}) or {}
    if op in ("success", "failure") and len(parts) >= 2:
        sid = parts[1]
        st = (steps.get(sid) or {}).get("status", "")
        ok_raw = st == ("success" if op == "success" else "failure")
        ok = ok_raw ^ negate
        return ok, f"v1: {sid}.status={st} want={'!' if negate else ''}{op}"
    if op == "contains" and len(parts) == 3:
        sid, kw = parts[1], parts[2]
        text = (steps.get(sid) or {}).get("output", "") or ""
        ok_raw = kw in text
        ok = ok_raw ^ negate
        return ok, f"v1: {sid}.output {'含' if ok_raw else '不含'} {kw!r}"
    return False, f"v1 未知语法: {s}"
