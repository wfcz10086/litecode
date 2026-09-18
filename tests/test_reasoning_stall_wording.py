"""P1-1: 推理超限/死循环等面向用户的状态提示改成自然语言, 不再暴露
`[系统...]` / `[SYSTEM...]` 调试括号语法和内部字符数阈值.

用真实证据验证文案确实变了 (不是纸面改字符串):
1. 用 ast 解析 litecode_server.py, 抠出 _loop_msg / _nudge / _stall_msg / 历史损坏提示
   这几个变量实际被赋的字符串常量 (Python 会在语法层面把相邻字符串字面量拼成一个
   Constant, ast 拿到的就是拼接后的最终值, 不是从测试里抄一份自证).
2. 新文案喂给 wechat_bridge._filter_system_leak (P0-1 用来剔除内部调试语法泄漏的过滤器)
   后原样透传 —— 证明它已经不再是会被识别为"内部调试语法泄漏"的 [系统]/[SYSTEM] 格式.
   (作为对照, 旧文案 `[系统: 推理超过 7000 字仍未产出...]` 会被这个过滤器整行剔除.)

[2026-08-25 修] 原版用**硬编码行号范围**锁定这几处赋值 (因为 `_nudge` 同名变量在文件里
另有几处 model-facing 用法需要排除). 但 AGENT_STREAM_REFACTOR 的 P2-5/M1-M5b 重构把
litecode_server.py 的行号整体上移了约 165 行, 导致这个测试全线失败 —— 代码是好的, 是
测试的结构假设过期了 (跟 P2-4 那次测试硬编码扫 app.js 同一类问题).

改为**内容锚定 + 语义分类**, 不再依赖任何行号:
- 用户可见提示: 按预期自然语言文案定位 (它们本来就是被断言的内容)
- 模型纠偏指令: 按 `[SYSTEM` 前缀识别并单独归类
这样以后再怎么挪行号都不会误报.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "litecodeext"))

from wechat_bridge import _filter_system_leak

_FILE = Path(__file__).parent.parent / "litecodeext" / "litecode_server.py"
_TREE = ast.parse(_FILE.read_text())

_BRACKET_MARKERS = ("[系统", "[SYSTEM")


def _str_of(node) -> str | None:
    """从 Constant 或 f-string(JoinedStr) 里抠出字符串内容 (占位符替换成 {})."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else "{}"
            for v in node.values
        )
    return None


def _all_assigned_str_values(varname: str) -> list[str]:
    """找全文件里所有 `varname = <str/f-str>` 或 `varname = <a> if cond else <b>` 的字符串.

    不限行号 —— 靠调用方按内容/前缀区分用户可见 vs 模型纠偏.
    """
    out = []
    for node in ast.walk(_TREE):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == varname):
            continue
        v = node.value
        candidates = [v.body, v.orelse] if isinstance(v, ast.IfExp) else [v]
        for c in candidates:
            s = _str_of(c)
            if s is not None:
                out.append(s)
    return out


def _user_facing(values: list[str]) -> list[str]:
    """用户可见的那些 = 不带 [系统/[SYSTEM 调试括号前缀的."""
    return [v for v in values if not any(m in v for m in _BRACKET_MARKERS)]


def _model_facing(values: list[str]) -> list[str]:
    """喂给模型的纠偏指令 = 带 [系统/[SYSTEM 前缀的."""
    return [v for v in values if any(m in v for m in _BRACKET_MARKERS)]


def _all_sse_content_first_args() -> list[str]:
    """找全文件 `sse_content("<literal>", ...)` 的第一个位置参数字面量."""
    out = []
    for node in ast.walk(_TREE):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "sse_content" and node.args):
            s = _str_of(node.args[0])
            if s is not None:
                out.append(s)
    return out


def test_loop_message_is_natural_language():
    values = _all_assigned_str_values("_loop_msg")
    assert values, "_loop_msg 赋值未找到"
    assert any("思考绕进了同一个圈子里" in v for v in values)
    # _loop_msg 只有用户可见这一种用法, 全部不该带调试括号
    assert not any(any(m in v for m in _BRACKET_MARKERS) for v in values)


def test_nudge_user_facing_message_is_natural_language():
    """_nudge 同名变量有两类用法: 用户可见的 SSE 提示 (本次改的) 和
    喂给模型的 [SYSTEM 硬约束] 纠偏指令 (故意保留). 只断言前者."""
    user = _user_facing(_all_assigned_str_values("_nudge"))
    assert any("还在思考中" in v for v in user)
    assert any("这次想得有点久了" in v for v in user)
    assert not any("推理" in v and "字" in v for v in user), "不应再暴露字符数阈值"


def test_nudge_model_facing_messages_kept_as_is():
    """对照: 模型纠偏用的 _nudge 仍保留 [SYSTEM 前缀, 本次刻意不改
    (改成自然语言会削弱对模型的纠偏效果, 且用户看不到它们)."""
    model = _model_facing(_all_assigned_str_values("_nudge"))
    assert model, "模型纠偏用的 _nudge 应仍存在"
    assert any("[SYSTEM" in v for v in model)


def test_stall_message_is_natural_language():
    values = _all_assigned_str_values("_stall_msg")
    assert values, "_stall_msg 赋值未找到"
    assert any("这轮想得太久了" in v for v in values)
    assert not any(any(m in v for m in _BRACKET_MARKERS) for v in values)
    assert not any("字仍未产出" in v for v in values)


def test_history_corruption_message_is_natural_language():
    args = _all_sse_content_first_args()
    assert any("对话记录出了点小问题" in a for a in args)
    # 走 sse_content 的都是用户可见的, 不该有 [系统] 前缀
    corrupt = [a for a in args if "对话记录出了点小问题" in a]
    assert not any("[系统]" in a for a in corrupt)


def test_new_wording_passes_wechat_leak_filter_unchanged():
    msgs = (
        _user_facing(_all_assigned_str_values("_loop_msg"))
        + _user_facing(_all_assigned_str_values("_nudge"))
        + _user_facing(_all_assigned_str_values("_stall_msg"))
        + [a for a in _all_sse_content_first_args() if "对话记录出了点小问题" in a]
    )
    assert len(msgs) >= 5
    for msg in msgs:
        assert _filter_system_leak(msg) == msg.strip(), f"应原样透传, 不该被判定为系统泄漏: {msg!r}"


def test_old_bracket_wording_would_have_been_stripped():
    """对照组: 旧文案确实会被过滤器整行吃掉, 证明改自然语言前用户在 WeChat 端根本看不到这些提示."""
    legacy = "\n[系统: 推理超过 7000 字仍未产出 (第 1 次超限, 已中断本轮)]\n后面是真正的回复"
    cleaned = _filter_system_leak(legacy)
    assert "[系统:" not in cleaned
    assert "推理超过" not in cleaned
    assert "后面是真正的回复" in cleaned


def test_model_facing_constraint_messages_kept_as_is():
    """_constraint 只 append 进 messages(role=user) 喂给模型下一轮,
    从不经过 sse_content 到用户, 属于纠偏指令, 本次不改."""
    values = _all_assigned_str_values("_constraint")
    assert values, "_constraint 赋值未找到"
    assert any("[SYSTEM-EMERGENCY]" in v for v in values)
    # _constraint 应仍是 append 进 messages(role=user), 从不经过 sse_content.
    # 迁移后 messages 已进 TurnContext, append 形态变成 _ctx.messages.append({...})
    found_append = False
    for node in ast.walk(_TREE):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"):
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    keys = [k.value for k in arg.keys if isinstance(k, ast.Constant)]
                    vals = [v.id for v in arg.values if isinstance(v, ast.Name)]
                    if "content" in keys and "_constraint" in vals:
                        found_append = True
    assert found_append, "_constraint 应仍是 append 进 messages(role=user), 未曾经过 sse_content"
    # 反向确认: _constraint 的文案从不作为 sse_content 的实参出现
    sse_args = _all_sse_content_first_args()
    assert not any("[SYSTEM-EMERGENCY]" in a for a in sse_args), \
        "_constraint 类文案不该出现在 sse_content (那是用户可见通道)"
