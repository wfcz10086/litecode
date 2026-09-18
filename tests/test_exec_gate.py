#!/usr/bin/env python3
# EXEC-GATE (规划→执行断链, P0): agent 会规划/验证/标完成, 唯独跳过真正的写文件动作
# (四轮实测 write_file/edit_file/apply_patch 调用数全是 0, 数据见 BACKLOG.md Inbox 首条)。
#
# 现有 [READONLY] 中途提醒已证伪 (第 2 轮实测 "提醒 2 次 | 写文件 0 次", 喊了照样不写),
# 所以本轮不是再加一条中途提醒, 而是把钩子挪到全新位置: agent_stream() 里"本轮无
# tool_call、即将给出最终回复、准备结束这一轮"的那一刻 (litecode_server.py 的
# `else:` 分支, `_ctx.tracer.iteration_end("ITER_OK")` + `break` 之前, PLAN 自动续行
# 判断之后) —— 这个位置此前从未被检查过。
#
# 硬约束 (对应今天 Plan-Gate 踩过的坑): 绝对不判文本/关键词, 只用机械信号——写工具
# (litecode_server._WRITE_TOOL_NAMES: write_file/patch_file/apply_blocks/edit_symbol)
# *成功执行*的调用计数是否为 0, 叠加"本回合已有 ≥N 轮工具调用"门槛排除纯问答/闲聊。
# 触发文案写成"误判也无害": 明确说"如果这个任务本来就不需要改文件, 忽略这条"。
#
# 覆盖:
#   1. BatchWriteState.exec_gate_tick() / exec_gate_task_done_needs_warning() 状态机本身
#      (litecode_server.py 主循环直接调这两个方法, 见 agent_state.py -- 单测这两个方法
#      就是单测真实生产逻辑, 不是重新实现一遍再测)
#   2. 离线复现: monkeypatch litecode_server._vllm_stream, 驱动真实 agent_stream()跑完整
#      的 SSE 循环, 验证收尾闸在真实代码路径里按预期触发/放行/不死锁
#   3. 修复轮 #1 (编排者退回) 补的 2 条永久回归: edit_symbol 真写盘不该触发 (名单缺口) /
#      write_file 执行失败不算写入, 该触发时不许哑火 (按名字数改成按执行结果数)
#   4. 修复轮 #2 (TESTER FAIL) 补的 5 条永久回归: `is_err = result.startswith("ERROR")`
#      本身是乐观脆弱判据 (任何不以 ERROR 开头的返回都被当成成功), apply_blocks 全部块
#      失败时走的就是这条不带 ERROR 前缀的路径 (真实生产 bug)。改成
#      `_write_call_confirmed_success()`: 只在命中该工具*已知的成功输出格式*时才算成功,
#      判不准一律算没成功 —— 覆盖 apply_blocks 全失败 / 部分成功 / 空返回 / 无 ERROR 前缀
#      的失败文案 / WARNING 文案 五种场景。
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "lib"))

from lib.agent_state import BatchWriteState  # noqa: E402

MIN_TOOL_ITERS = 4  # 与 litecode_server.py 里 _AGT.get("exec_gate_min_tool_iters", 4) 默认值一致


# ══════════════════════════════════════════════════════════════
# 第一部分: 状态机单测 (脱离 agent_stream() 直接测)
# ══════════════════════════════════════════════════════════════

def _run_iters(s, n, write_at=None, min_tool_iters=MIN_TOOL_ITERS):
    """模拟主循环连续 n 轮"有工具调用的迭代", 每轮先累计 plan_gate 计数器(exec_gate 复用它),
    再视 write_at (第几轮, 1-based, None 表示全程不写) 决定是否累计 exec_gate_write_calls,
    最后在每轮末尾调一次 exec_gate_tick 模拟"这一轮结束时模型又给了个空文本/最终答案"。
    返回: 每轮 tick() 的返回值列表 (True 表示那一轮注入了提醒)。"""
    injected_at = []
    for i in range(1, n + 1):
        s.plan_gate_tool_call_iters += 1
        if i == write_at:
            s.exec_gate_write_calls += 1
        if s.exec_gate_tick(s.plan_gate_tool_call_iters, min_tool_iters=min_tool_iters):
            injected_at.append(i)
    return injected_at


# ── 场景 1: 有写入的回合 → 收尾不触发 ──
def test_has_write_never_triggers():
    s = BatchWriteState()
    injected = _run_iters(s, 8, write_at=2)  # 第 2 轮写过一次, 后面还有 6 轮零写入
    assert injected == []
    assert s.exec_gate_injected is False
    print("[OK] 本回合有过写入 (哪怕只 1 次) → 收尾闸永不触发")


def test_write_on_last_iter_before_gate_check_still_counts():
    # 写入发生在"即将触发那一轮"本身, 也应该算数 (顺序无关, 只看累计次数)
    s = BatchWriteState()
    injected = _run_iters(s, MIN_TOOL_ITERS, write_at=MIN_TOOL_ITERS)
    assert injected == []
    print("[OK] 写入哪怕发生在恰好达阈值的那一轮, 累计次数一样生效, 不触发")


# ── 场景 2: 零写入 + 工具轮数达阈值 → 收尾触发恰好 1 次 ──
def test_zero_write_reaches_threshold_triggers_exactly_once():
    s = BatchWriteState()
    injected = _run_iters(s, 10)  # 全程零写入
    assert injected == [MIN_TOOL_ITERS], f"应恰好在第 {MIN_TOOL_ITERS} 轮触发一次, 实际: {injected}"
    assert s.exec_gate_injected is True
    print(f"[OK] 零写入且达到阈值({MIN_TOOL_ITERS}) → 恰好第 {MIN_TOOL_ITERS} 轮触发 1 次")


def test_exactly_at_threshold_triggers_boundary():
    s = BatchWriteState()
    injected = _run_iters(s, MIN_TOOL_ITERS)  # 正好等于阈值
    assert injected == [MIN_TOOL_ITERS]
    print(f"[OK] 恰好达到阈值({MIN_TOOL_ITERS})边界即触发, 不多不少")


# ── 场景 3: 零写入但工具轮数不足 → 不触发 (纯问答只调 1-2 次工具) ──
def test_zero_write_below_threshold_never_triggers():
    for n in (0, 1, 2, MIN_TOOL_ITERS - 1):
        s = BatchWriteState()
        injected = _run_iters(s, n)
        assert injected == [], f"n={n} 轮不该触发, 实际: {injected}"
    print(f"[OK] 零写入但工具轮数不足阈值({MIN_TOOL_ITERS}) 的各种轮数 (含 0/1/2) 都不触发")


def test_pure_chat_zero_tool_calls_never_triggers():
    # "你好" 这类闲聊全程不调工具, exec_gate_tick 从未被主循环调用过 (else 分支第一次
    # 就是最终回复), 状态机默认值本身就该是安全的零触发态
    s = BatchWriteState()
    assert s.plan_gate_tool_call_iters == 0
    assert s.exec_gate_write_calls == 0
    assert s.exec_gate_injected is False
    print("[OK] 完全没调工具的闲聊, 默认状态即安全, 零触发")


def test_simple_qa_one_tool_call_never_triggers():
    # 只调 1 次工具就给出最终答案的简单问答 (例如 "看下这个目录有什么" → get_tree → 回答)
    s = BatchWriteState()
    injected = _run_iters(s, 1)
    assert injected == []
    print("[OK] 只调 1 次工具就答完的简单任务不触发")


# ── 场景 4: 触发后再走若干轮 → 不重复注入 ──
def test_triggered_then_continues_many_iters_no_duplicate_injection():
    s = BatchWriteState()
    injected = _run_iters(s, 30)  # 阈值 4, 一路跑到 30 轮全程零写入
    assert injected == [MIN_TOOL_ITERS], f"30 轮里应该只在第 {MIN_TOOL_ITERS} 轮触发过 1 次, 实际: {injected}"
    assert s.plan_gate_tool_call_iters == 30, "计数器本身仍应继续累加, 只是不再重复提醒"
    print(f"[OK] 触发后继续跑 30 轮, 仅第 {MIN_TOOL_ITERS} 轮注入过 1 次, 不重复提醒 (不死锁)")


def test_no_deadlock_write_after_injection_stays_quiet():
    # 注入后模型真的去写了 → 不该有第二次触发, 状态干净收敛
    s = BatchWriteState()
    _run_iters(s, MIN_TOOL_ITERS)  # 先触发一次
    assert s.exec_gate_injected is True
    injected_after = _run_iters(s, 3, write_at=1)  # 触发后又跑 3 轮, 第 1 轮就写了
    assert injected_after == []
    print("[OK] 已注入后模型去写了文件, 状态正常收敛, 无重复注入")


# ── 场景 5: task_done 证据提示 ──
def test_task_done_warns_when_no_write_at_all():
    s = BatchWriteState()  # 全程(含这一轮)零写入
    assert s.exec_gate_task_done_needs_warning(iter_has_write=False) is True
    print("[OK] task_done 调用时本回合全程零写入 → 需要附加证据缺失提醒")


def test_task_done_no_warn_when_prior_iter_wrote():
    s = BatchWriteState()
    s.exec_gate_write_calls = 1  # 之前某轮写过
    assert s.exec_gate_task_done_needs_warning(iter_has_write=False) is False
    print("[OK] task_done 调用前的某轮已经写过 → 不附加提醒")


def test_task_done_no_warn_when_same_iter_writes_too():
    # write_file 和 task_done 同一批工具调用里一起出现 (顺序不重要), 也不该被误伤
    s = BatchWriteState()
    assert s.exec_gate_task_done_needs_warning(iter_has_write=True) is False
    print("[OK] task_done 与 write_file 同一轮出现 (顺序无关) → 不误伤, 不附加提醒")


def test_gate_state_default_zero_cost():
    s = BatchWriteState()
    assert s.exec_gate_write_calls == 0
    assert s.exec_gate_injected is False
    print("[OK] BatchWriteState EXEC-GATE 字段默认全零, 简单任务零开销")


# ══════════════════════════════════════════════════════════════
# 第二部分: 离线复现 -- monkeypatch _vllm_stream 驱动真实 agent_stream()
# ══════════════════════════════════════════════════════════════

class _FakeVLLM:
    """伪造 lib.transport.vllm_stream 的流式返回, 按预设"轮次脚本"依次回放,
    最后一轮之后的调用重复最后一项 (兜底, 防脚本没写够导致 IndexError)。
    turn 格式:
      ("text", "最终文本")
      ("tool", [(tool_name, args_dict), ...])   # 一轮里可以有多个并发工具调用
    """
    def __init__(self, turns):
        self.turns = turns
        self.calls = 0

    async def __call__(self, messages, tools, max_tokens_override=None):
        idx = min(self.calls, len(self.turns) - 1)
        kind, payload = self.turns[idx]
        self.calls += 1
        if kind == "text":
            yield {"choices": [{"delta": {"content": payload}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        else:
            for i, (name, args) in enumerate(payload):
                yield {"choices": [{"delta": {"tool_calls": [{
                    "index": i, "id": f"call_{idx}_{i}",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }]}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}


def _drive(monkeypatch, turns, session_id=None, user_message="test"):
    """monkeypatch litecode_server._vllm_stream 后跑一次 agent_stream(), 返回
    (完整 SSE 拼接串, FakeVLLM 实例本身供检查调用次数)。"""
    import litecode_server as srv
    fake = _FakeVLLM(turns)
    monkeypatch.setattr(srv, "_vllm_stream", fake)

    async def _run():
        chunks = []
        async for c in srv.agent_stream(user_message, session_id):
            chunks.append(c)
        return "".join(chunks)

    full = asyncio.run(_run())
    return full, fake


def _drive_with_tool_result_override(monkeypatch, turns, tool_name, canned_result,
                                      session_id=None, user_message="test"):
    """跟 _drive 一样驱动真实 agent_stream(), 但把某个写类工具的执行结果替换成
    canned_result (不落盘, 只换 execute_tool 的返回字符串), 用来复现"handler 返回一个
    既不是 ERROR: 开头、也不是任何已知成功格式"的脆弱字符串场景 (修复轮 #2 TESTER 报的
    空返回 / 无 ERROR 前缀的失败文案 / WARNING 文案三个反例)。"""
    import litecode_server as srv
    real_execute_tool = srv.execute_tool

    async def _fake_execute_tool(name, args, sid=None):
        if name == tool_name:
            return canned_result, None
        return await real_execute_tool(name, args, sid=sid)

    monkeypatch.setattr(srv, "execute_tool", _fake_execute_tool)
    return _drive(monkeypatch, turns, session_id=session_id, user_message=user_message)


def test_offline_explore_then_final_zero_write_triggers_gate_and_continues(monkeypatch):
    """探索若干轮后直接给最终答案且零写入 → 收尾闸注入, 循环多走一轮而不是立刻结束"""
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("text", "已确认修复就位。"),          # 第 5 次调用: 零写入, 应被 gate 拦一次
        ("text", "确认后仍不写, 这轮该放行了。"),  # 第 6 次调用: gate 已注入过, 必须放行
    ]
    full, fake = _drive(monkeypatch, turns)
    assert fake.calls == len(turns), f"应该恰好走完预设的 {len(turns)} 轮, 实际 {fake.calls}"
    assert "[DONE]" in full, "循环必须正常收尾, 不能卡死"
    print("[OK] 离线复现: 探索 4 轮 + 零写入最终答案 → 收尾闸注入, 多走一轮后放行, 正常结束")


def test_offline_gate_injected_then_still_no_write_does_not_deadlock(monkeypatch):
    """模拟"注入后模型仍不写" → 放行, 回合正常结束, 不死锁"""
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("text", "第一次收尾, 应被拦一次。"),
        ("text", "被拦后我还是不想写, 也必须放行, 不能一直卡住。"),
    ]
    full, fake = _drive(monkeypatch, turns)
    assert fake.calls == len(turns)
    assert full.endswith("data: [DONE]\n\n") or "[DONE]" in full
    print("[OK] 离线复现: 注入后模型仍不写 → 不硬阻断, 正常放行结束, 不死锁")


def test_offline_gate_injected_then_writes_completes_normally(monkeypatch):
    """模拟"注入后模型去写了文件" → 正常完成, 且不会有第二次注入"""
    tmp_file = str(ROOT.parent / "tests" / f"_exec_gate_probe_{uuid.uuid4().hex[:8]}.tmp")
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("text", "确认修复就位。"),  # 触发收尾闸
        ("tool", [("write_file", {"filepath": tmp_file, "content": "x = 1\n"})]),
        ("text", "现在真的改完了。"),
    ]
    try:
        full, fake = _drive(monkeypatch, turns)
        assert fake.calls == len(turns)
        assert "[DONE]" in full
        assert Path(tmp_file).exists(), "write_file 应该真的落盘"
    finally:
        Path(tmp_file).unlink(missing_ok=True)
    print("[OK] 离线复现: 注入后模型去写了文件 → 正常完成, 不产生第二次注入")


def test_offline_simple_qa_never_triggers_gate(monkeypatch):
    """模拟简单问答 (1 次工具就答完) → 零注入, 且完全不影响原有行为"""
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("text", "这是目录结构。"),
    ]
    full, fake = _drive(monkeypatch, turns)
    assert fake.calls == len(turns), "简单问答不该被多问一轮"
    assert "[DONE]" in full
    print("[OK] 离线复现: 简单问答 (1 次工具) → 零注入, 2 轮内自然结束")


def test_offline_pure_chat_never_triggers_gate(monkeypatch):
    """完全不调工具的闲聊 → 零注入"""
    turns = [("text", "你好，有什么可以帮你？")]
    full, fake = _drive(monkeypatch, turns)
    assert fake.calls == 1, "纯闲聊 1 轮就该结束"
    assert "[DONE]" in full
    print("[OK] 离线复现: 纯闲聊零工具调用 → 零注入, 1 轮结束")


def test_offline_task_that_already_writes_never_triggers_gate(monkeypatch):
    """本来就有写入的任务 → 收尾闸不触发 (哪怕工具轮数远超阈值)"""
    tmp_file = str(ROOT.parent / "tests" / f"_exec_gate_probe_{uuid.uuid4().hex[:8]}.tmp")
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("write_file", {"filepath": tmp_file, "content": "x = 1\n"})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("read_file", {"filepath": tmp_file})]),
        ("text", "改完了，已验证。"),
    ]
    try:
        full, fake = _drive(monkeypatch, turns)
        assert fake.calls == len(turns), "有写入的任务不该被额外多问一轮"
        assert "[DONE]" in full
    finally:
        Path(tmp_file).unlink(missing_ok=True)
    print("[OK] 离线复现: 本来就有写入的任务 → 收尾闸零触发")


# ── 修复轮 #1 永久回归 · A: edit_symbol 真写盘不该被误判为"零写入" ──
def test_offline_edit_symbol_only_real_write_never_triggers_gate(monkeypatch):
    """只调 edit_symbol (不调 write_file/patch_file/apply_blocks), 且真的把磁盘上的代码
    从 `return 1` 改成了 `return 2` (落盘可验) → 收尾闸不该触发。回归此前的名单缺口:
    edit_symbol 不在 (write_file, patch_file, apply_blocks) 里, 会被判定为"磁盘上没有
    任何改动", 是一句事实性错误 (不是"不适用"的无害提醒)。"""
    from lib.config import WORKSPACE
    tmp_file = str(ROOT.parent / "tests" / f"_exec_gate_probe_{uuid.uuid4().hex[:8]}.py")
    Path(tmp_file).write_text("def foo():\n    return 1\n")
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("edit_symbol", {
            "filepath": tmp_file, "symbol_name": "foo",
            "new_code": "def foo():\n    return 2\n",
        })]),
        ("text", "改完了。"),
    ]
    try:
        full, fake = _drive(monkeypatch, turns)
        assert fake.calls == len(turns), (
            f"edit_symbol 真写了盘, 不该被多问一轮, 实际 calls={fake.calls}"
        )
        assert "[DONE]" in full
        _new_content = Path(tmp_file).read_text()
        assert "return 2" in _new_content and "return 1" not in _new_content, (
            "edit_symbol 应该真的把磁盘上的代码从 return 1 改成了 return 2"
        )
    finally:
        Path(tmp_file).unlink(missing_ok=True)
        for _bak in (WORKSPACE / ".openclaw_checkpoints").glob(f"{Path(tmp_file).name}.*.bak"):
            _bak.unlink(missing_ok=True)
    print("[OK] 修复轮#1回归: 只调 edit_symbol 且真写了盘 → 收尾闸不触发")


# ── 修复轮 #1 永久回归 · B: write_file 执行失败不算"写过", 收尾闸照常触发 ──
def test_offline_write_file_failure_still_triggers_gate(monkeypatch):
    """write_file 缺 content 参数, 执行必然失败(handler 返回 "ERROR: ...", 磁盘什么都
    没写) → 收尾闸照常触发, 不许因为"调用过这个工具名"就被当成已写入。这是本轮最重要
    的回归: 判不出写入是否成功时必须按"没成功"处理, 宁可多提醒一次也不能哑火。"""
    tmp_file = str(ROOT.parent / "tests" / f"_exec_gate_probe_{uuid.uuid4().hex[:8]}.tmp")
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("write_file", {"filepath": tmp_file})]),  # 缺 content, 执行必然失败
        ("text", "写完了。"),                                   # 第 5 轮: 应被 gate 拦一次
        ("text", "确实没写, 现在放行。"),                        # 第 6 轮: gate 已注入过, 必须放行
    ]
    try:
        full, fake = _drive(monkeypatch, turns)
        assert fake.calls == len(turns), (
            "失败的写调用不该被当成'已写入', 收尾闸该触发时不能哑火, "
            f"实际 calls={fake.calls} (预期 {len(turns)})"
        )
        assert "[DONE]" in full
        assert not Path(tmp_file).exists(), "write_file 执行失败, 磁盘上不该出现这个文件"
    finally:
        Path(tmp_file).unlink(missing_ok=True)
    print("[OK] 修复轮#1回归: write_file 执行失败(什么都没写) → 收尾闸照常触发, 不哑火")


# ── 修复轮 #2 永久回归 · 真实生产 bug: apply_blocks 全部块失败, 不带 ERROR 前缀 ──
def test_offline_apply_blocks_all_fail_still_triggers_gate(monkeypatch):
    """apply_blocks 全部块失败 (目标文件不存在, 磁盘零改动, 真实 handler 未 mock) → 收尾闸
    照常触发。TESTER 修复轮#2 挖到的真实生产 bug: h_apply_blocks 全失败时返回
    "Applied 0/N blocks\\nFailed N blocks:..." 不带 ERROR: 前缀, 旧判据
    `not result.startswith("ERROR")` 会把它误判成"写成功", 导致收尾闸/[READONLY] 哑火。"""
    _missing = f"_exec_gate_probe_missing_{uuid.uuid4().hex[:8]}.py"
    _blocks = f"{_missing}\n<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE"
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("apply_blocks", {"blocks": _blocks})]),
        ("text", "改完了。"),               # 第 5 轮: apply_blocks 全失败, 应被 gate 拦一次
        ("text", "确实没改, 现在放行。"),     # 第 6 轮: gate 已注入过, 必须放行
    ]
    full, fake = _drive(monkeypatch, turns)
    assert fake.calls == len(turns), (
        "apply_blocks 全部块失败(磁盘零改动) 不该被算成写入成功, 收尾闸该触发时不能哑火, "
        f"实际 calls={fake.calls} (预期 {len(turns)})"
    )
    assert "[DONE]" in full
    print("[OK] 修复轮#2回归: apply_blocks 全部块失败(磁盘零改动) → 收尾闸照常触发, 不哑火")


def test_offline_apply_blocks_partial_success_never_triggers_gate(monkeypatch):
    """apply_blocks 部分成功 (2 块里 1 块真的改了盘) → 算写入成功, 收尾闸不触发, 不误伤
    (对齐 patch_file/apply_blocks 一贯的 "per-block atomic" 语义: 不是全部块都成功才算数)。"""
    tmp_file = str(ROOT.parent / "tests" / f"_exec_gate_probe_{uuid.uuid4().hex[:8]}.py")
    Path(tmp_file).write_text("def foo():\n    return 1\n")
    _missing = f"_exec_gate_probe_missing_{uuid.uuid4().hex[:8]}.py"
    _blocks = (
        f"{tmp_file}\n<<<<<<< SEARCH\nreturn 1\n=======\nreturn 2\n>>>>>>> REPLACE\n\n"
        f"{_missing}\n<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE"
    )
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("apply_blocks", {"blocks": _blocks})]),
        ("text", "改完了。"),
    ]
    try:
        full, fake = _drive(monkeypatch, turns)
        assert fake.calls == len(turns), "部分成功的 apply_blocks 不该被多问一轮"
        assert "[DONE]" in full
        assert "return 2" in Path(tmp_file).read_text()
    finally:
        Path(tmp_file).unlink(missing_ok=True)
    print("[OK] 修复轮#2回归: apply_blocks 部分成功(至少1块真写盘) → 不触发, 不误伤")


# ── 修复轮 #2 永久回归 · TESTER 报的 3 个脆弱场景 (不带 ERROR 前缀的失败/空返回) ──
def test_offline_write_file_empty_result_never_counts_as_success(monkeypatch):
    """write_file 返回空字符串 (既不是 ERROR: 开头, 也不是任何已知成功格式) → 判不准时
    按"没写成功"处理, 收尾闸照常触发。"""
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("write_file", {"filepath": "x.py", "content": "y = 1\n"})]),
        ("text", "写完了。"),
        ("text", "确实没写, 现在放行。"),
    ]
    full, fake = _drive_with_tool_result_override(monkeypatch, turns, "write_file", "")
    assert fake.calls == len(turns), (
        f"空返回不该被当成写入成功, 实际 calls={fake.calls} (预期 {len(turns)})"
    )
    assert "[DONE]" in full
    print("[OK] 修复轮#2回归: write_file 返回空字符串 → 不算成功, 收尾闸照常触发")


def test_offline_write_file_error_text_without_error_prefix_never_counts_as_success(monkeypatch):
    """write_file 返回 "Permission denied: ..." (含失败字样但不带 ERROR: 前缀) → 判不准
    时按"没写成功"处理, 收尾闸照常触发。"""
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("write_file", {"filepath": "/root/protected.py", "content": "y = 1\n"})]),
        ("text", "写完了。"),
        ("text", "确实没写, 现在放行。"),
    ]
    full, fake = _drive_with_tool_result_override(
        monkeypatch, turns, "write_file",
        "Permission denied: cannot write to /root/protected.py",
    )
    assert fake.calls == len(turns), (
        f"无 ERROR 前缀的失败文案不该被当成写入成功, 实际 calls={fake.calls} (预期 {len(turns)})"
    )
    assert "[DONE]" in full
    print("[OK] 修复轮#2回归: write_file 返回无 ERROR 前缀的失败文案 → 不算成功, 收尾闸照常触发")


def test_offline_write_file_warning_text_never_counts_as_success(monkeypatch):
    """write_file 返回 "WARNING: ... file NOT saved" → 判不准时按"没写成功"处理,
    收尾闸照常触发。"""
    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("write_file", {"filepath": "x.py", "content": "y = 1\n"})]),
        ("text", "写完了。"),
        ("text", "确实没写, 现在放行。"),
    ]
    full, fake = _drive_with_tool_result_override(
        monkeypatch, turns, "write_file",
        "WARNING: partial write, disk quota low, file NOT saved",
    )
    assert fake.calls == len(turns), (
        f"WARNING 文案不该被当成写入成功, 实际 calls={fake.calls} (预期 {len(turns)})"
    )
    assert "[DONE]" in full
    print("[OK] 修复轮#2回归: write_file 返回 WARNING 文案(未落盘) → 不算成功, 收尾闸照常触发")


def test_offline_task_done_evidence_hint_zero_write(monkeypatch):
    """离线复现 task_done 证据提示: 零写入时 tool 返回结果里带证据缺失提醒"""
    from tools.handlers.task_ops import h_task_create, _STORE
    from lib.session import get_history

    sid = "exec-gate-test-taskdone-zw-" + uuid.uuid4().hex[:8]
    asyncio.run(h_task_create(sid, {"title": "t"}))
    tid = list(_STORE[sid].keys())[0]

    turns = [
        ("tool", [("get_tree", {"path": "."})]),
        ("tool", [("task_done", {"id": tid, "note": "done"})]),
        ("text", "完成。"),
    ]
    try:
        _drive(monkeypatch, turns, session_id=sid)
        hist = get_history(sid)
        tool_msgs = [m for m in hist if m.get("role") == "tool"]
        assert any("SYSTEM-EXEC-GATE" in (m.get("content") or "") for m in tool_msgs), (
            "task_done 零写入时应该在返回结果里带证据缺失提醒"
        )
    finally:
        _STORE.pop(sid, None)
        import shutil
        from lib.config import SESSIONS_DISK
        shutil.rmtree(SESSIONS_DISK / sid, ignore_errors=True)
        (SESSIONS_DISK / f"{sid}.json").unlink(missing_ok=True)
    print("[OK] 离线复现: task_done 零写入 → 返回结果附带证据提示")


def test_offline_task_done_no_evidence_hint_when_written(monkeypatch):
    """离线复现 task_done 证据提示: 有写入时不追加"""
    from tools.handlers.task_ops import h_task_create, _STORE
    from lib.session import get_history

    sid = "exec-gate-test-taskdone-w-" + uuid.uuid4().hex[:8]
    asyncio.run(h_task_create(sid, {"title": "t"}))
    tid = list(_STORE[sid].keys())[0]
    tmp_file = str(ROOT.parent / "tests" / f"_exec_gate_probe_{uuid.uuid4().hex[:8]}.tmp")

    turns = [
        ("tool", [("write_file", {"filepath": tmp_file, "content": "x = 1\n"})]),
        ("tool", [("task_done", {"id": tid, "note": "done"})]),
        ("text", "完成。"),
    ]
    try:
        _drive(monkeypatch, turns, session_id=sid)
        hist = get_history(sid)
        tool_msgs = [m for m in hist if m.get("role") == "tool"]
        assert not any("SYSTEM-EXEC-GATE" in (m.get("content") or "") for m in tool_msgs), (
            "task_done 之前已经写过文件, 不该附加证据缺失提醒"
        )
    finally:
        Path(tmp_file).unlink(missing_ok=True)
        _STORE.pop(sid, None)
        import shutil
        from lib.config import SESSIONS_DISK
        shutil.rmtree(SESSIONS_DISK / sid, ignore_errors=True)
        (SESSIONS_DISK / f"{sid}.json").unlink(missing_ok=True)
    print("[OK] 离线复现: task_done 之前已写入 → 不追加证据提示")


if __name__ == "__main__":
    test_has_write_never_triggers()
    test_write_on_last_iter_before_gate_check_still_counts()
    test_zero_write_reaches_threshold_triggers_exactly_once()
    test_exactly_at_threshold_triggers_boundary()
    test_zero_write_below_threshold_never_triggers()
    test_pure_chat_zero_tool_calls_never_triggers()
    test_simple_qa_one_tool_call_never_triggers()
    test_triggered_then_continues_many_iters_no_duplicate_injection()
    test_no_deadlock_write_after_injection_stays_quiet()
    test_task_done_warns_when_no_write_at_all()
    test_task_done_no_warn_when_prior_iter_wrote()
    test_task_done_no_warn_when_same_iter_writes_too()
    test_gate_state_default_zero_cost()

    class _MP:
        """__main__ 直跑时没有 pytest fixture, 用最小 monkeypatch 替身"""
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in self._undo:
                setattr(obj, name, old)

    for fn in (
        test_offline_explore_then_final_zero_write_triggers_gate_and_continues,
        test_offline_gate_injected_then_still_no_write_does_not_deadlock,
        test_offline_gate_injected_then_writes_completes_normally,
        test_offline_simple_qa_never_triggers_gate,
        test_offline_pure_chat_never_triggers_gate,
        test_offline_task_that_already_writes_never_triggers_gate,
        test_offline_edit_symbol_only_real_write_never_triggers_gate,
        test_offline_write_file_failure_still_triggers_gate,
        test_offline_apply_blocks_all_fail_still_triggers_gate,
        test_offline_apply_blocks_partial_success_never_triggers_gate,
        test_offline_write_file_empty_result_never_counts_as_success,
        test_offline_write_file_error_text_without_error_prefix_never_counts_as_success,
        test_offline_write_file_warning_text_never_counts_as_success,
        test_offline_task_done_evidence_hint_zero_write,
        test_offline_task_done_no_evidence_hint_when_written,
    ):
        mp = _MP()
        try:
            fn(mp)
        finally:
            mp.undo()

    print("\n[PASS] EXEC-GATE (收尾闸, 规划→执行断链修复) suite")
