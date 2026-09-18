#!/usr/bin/env python3
# PLAN-FIRST GATE 修复轮 #2 (编排者决策: 停止修补文本判定, 改判轨迹信号):
# 两轮文本判定实测都被 TESTER 打穿 (轮1: 否定窗口结构性漏洞; 轮2: 疑问/否定两级结构修好了
# 又出"转述第三方"/"动作词作名词"两类新误伤) -- 用正则做意图分类没有收敛点。
# 新方案不判文本, 只观察行为: agent 已经连续 N 轮真在调工具却从没调过 task_create,
# 就是在做复杂任务却没拆解, 这是观测到的事实不是对文本的猜测。误伤结构性为零 --
# "你好"/"跑下测试" 这类简单消息根本走不到 N 轮工具调用就结束了。
#
# 覆盖:
#   1. BatchWriteState.plan_gate_tick() 状态机本身 (litecode_server.py 主循环直接调这个方法,
#      见 agent_state.py -- 单测这个方法就是单测真实生产逻辑, 不是重新实现一遍再测)
#   2. 5 类轨迹场景: 简单任务不触发 / 恰好触发1次 / 触发后不重复 / 中途 task_create 则不触发 /
#      不死锁(触发后任务仍能正常跑完)
#   3. tests/test_web_ui_sys_inject_filter.py (问题 B 修复) 不在本文件范围内, 原样保留不动
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))
from lib.agent_state import BatchWriteState  # noqa: E402


THRESHOLD = 5  # 与 litecode_server.py 里的 _AGT.get("plan_gate_tool_iters", 5) 默认值一致


def _run_iters(s, n, task_create_at=None, threshold=THRESHOLD):
    """模拟主循环连续 n 轮"有工具调用的迭代", 每轮调一次 plan_gate_tick。
    task_create_at: 第几轮(1-based)调了 task_create, None 表示全程没调。
    返回: 每轮 tick() 的返回值列表 (True 表示那一轮注入了提醒)。"""
    injected_at = []
    for i in range(1, n + 1):
        created_now = (i == task_create_at)
        if s.plan_gate_tick(created_now, threshold=threshold):
            injected_at.append(i)
    return injected_at


# ── 场景 1: 少量工具调用就结束的简单任务 → 不触发 ──
def test_simple_task_few_iters_never_triggers():
    s = BatchWriteState()
    injected = _run_iters(s, 2)  # 例如 "跑下测试" 只需要 1-2 次 execute_shell 就结束
    assert injected == []
    assert s.plan_gate_injected is False
    print("[OK] 少量工具调用 (2轮) 就结束的简单任务不触发")


def test_simple_task_zero_iters_never_triggers():
    s = BatchWriteState()
    # "你好" 这类问答全程不调工具, plan_gate_tick 从未被调用
    assert s.plan_gate_tool_call_iters == 0
    assert s.plan_gate_injected is False
    print("[OK] 全程零工具调用 (纯问答) 不触发, 默认状态即安全")


# ── 场景 2: 超过阈值且从未 task_create → 恰好触发 1 次 ──
def test_exceeds_threshold_without_task_create_triggers_exactly_once():
    s = BatchWriteState()
    injected = _run_iters(s, 8)  # 8 轮全跑工具, 阈值 5, 全程不调 task_create
    assert injected == [THRESHOLD], f"应恰好在第 {THRESHOLD} 轮触发一次, 实际: {injected}"
    assert s.plan_gate_injected is True
    assert s.plan_gate_task_created is False
    print(f"[OK] 超过阈值({THRESHOLD})且从未 task_create → 恰好第 {THRESHOLD} 轮触发 1 次")


def test_exactly_at_threshold_triggers():
    s = BatchWriteState()
    injected = _run_iters(s, THRESHOLD)  # 正好等于阈值
    assert injected == [THRESHOLD]
    print(f"[OK] 恰好达到阈值({THRESHOLD})边界即触发, 不多不少")


def test_one_below_threshold_never_triggers():
    s = BatchWriteState()
    injected = _run_iters(s, THRESHOLD - 1)
    assert injected == []
    assert s.plan_gate_injected is False
    print(f"[OK] 差 1 轮未达阈值 ({THRESHOLD - 1}) 不触发, 边界不早退")


# ── 场景 3: 超过阈值但中途调过 task_create → 不触发 ──
def test_task_create_before_threshold_prevents_trigger():
    s = BatchWriteState()
    injected = _run_iters(s, 8, task_create_at=3)  # 第 3 轮调了 task_create, 之后又跑了 5 轮
    assert injected == []
    assert s.plan_gate_task_created is True
    assert s.plan_gate_injected is False
    print("[OK] 阈值前调过 task_create → 永久豁免, 后续再跑多少轮都不触发")


def test_task_create_at_exact_threshold_iter_prevents_trigger():
    # 第 threshold 轮同一轮里"既是达到阈值的那一轮, 又调了 task_create" → 不该触发
    # (task_create_now 检查先于/等价于阈值检查生效, 调过就永久豁免)
    s = BatchWriteState()
    injected = _run_iters(s, THRESHOLD, task_create_at=THRESHOLD)
    assert injected == []
    assert s.plan_gate_task_created is True
    print("[OK] 恰好在达阈值那一轮才调 task_create, 仍算已豁免, 不触发")


# ── 场景 4: 触发后继续跑很多轮 → 不重复注入 ──
def test_triggered_then_continues_many_iters_no_duplicate_injection():
    s = BatchWriteState()
    injected = _run_iters(s, 30)  # 阈值 5, 一路跑到 30 轮全不调 task_create
    assert injected == [THRESHOLD], f"30 轮里应该只在第 {THRESHOLD} 轮触发过 1 次, 实际: {injected}"
    assert s.plan_gate_tool_call_iters == 30, "计数器本身仍应继续累加(不受注入影响), 只是不再重复提醒"
    print(f"[OK] 触发后继续跑 30 轮, 仅第 {THRESHOLD} 轮注入过 1 次, 不重复提醒")


def test_task_create_after_already_injected_stays_quiet():
    # 已经注入过一次提醒之后, 哪怕模型这才想起来调 task_create, 也不该有任何后续反常状态变化
    s = BatchWriteState()
    _run_iters(s, THRESHOLD)  # 先触发一次
    assert s.plan_gate_injected is True
    injected_after = _run_iters(s, 5, task_create_at=2)  # 触发后又跑 5 轮, 第 2 轮才调 task_create
    assert injected_after == []
    assert s.plan_gate_task_created is True
    print("[OK] 已注入后模型才调 task_create, 状态正常收敛, 无重复注入无异常")


# ── 场景 5: 不死锁 -- 触发只是"多塞一条消息", 不影响循环继续跑, 状态机本身不会卡死 ──
def test_no_deadlock_state_converges_and_stays_stable():
    s = BatchWriteState()
    injected = _run_iters(s, 50)  # 模拟模型"死不配合", 50 轮全不调 task_create
    assert injected == [THRESHOLD], "50 轮里只应该触发这一次, 之后必须保持沉默, 不能卡住循环"
    assert s.plan_gate_injected is True
    # 关键: plan_gate_tick 从未返回会导致循环 continue/return/break 的信号,
    # 调用方 (litecode_server.py) 只用它的返回值决定"要不要多塞一条 user 消息",
    # 从不用它决定要不要跳过/拦截 tool_call 的实际执行 -- 见 litecode_server.py 主循环走读。
    print("[OK] 50 轮死不配合模拟: 仅触发 1 次即永久沉默, 不会导致循环卡死")


def test_threshold_zero_edge_case_does_not_crash():
    # 阈值配置成 0 这种极端 fallback 场景不应该抛异常 (哪怕这不是推荐配置)
    s = BatchWriteState()
    injected = _run_iters(s, 3, threshold=0)
    assert injected == [1]  # 第一轮 (1 >= 0) 就触发, 之后 injected 已 True 不再重复
    print("[OK] 阈值边界 0 不抛异常, 且仍只触发 1 次")


def test_gate_state_default_zero_cost_for_simple_tasks():
    s = BatchWriteState()
    assert s.plan_gate_tool_call_iters == 0
    assert s.plan_gate_task_created is False
    assert s.plan_gate_injected is False
    print("[OK] BatchWriteState 默认全零, 简单任务路径零开销/零误伤 (无需任何词表/文本判定)")


if __name__ == "__main__":
    test_simple_task_few_iters_never_triggers()
    test_simple_task_zero_iters_never_triggers()
    test_exceeds_threshold_without_task_create_triggers_exactly_once()
    test_exactly_at_threshold_triggers()
    test_one_below_threshold_never_triggers()
    test_task_create_before_threshold_prevents_trigger()
    test_task_create_at_exact_threshold_iter_prevents_trigger()
    test_triggered_then_continues_many_iters_no_duplicate_injection()
    test_task_create_after_already_injected_stays_quiet()
    test_no_deadlock_state_converges_and_stays_stable()
    test_threshold_zero_edge_case_does_not_crash()
    test_gate_state_default_zero_cost_for_simple_tasks()
    print("\n[PASS] PLAN-FIRST-GATE (轨迹触发, 修复轮 #2) suite")
