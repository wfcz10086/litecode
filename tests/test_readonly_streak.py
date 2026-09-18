#!/usr/bin/env python3
# READONLY-STREAK (2026-08-26 SWE-bench 实测: 连续只读不写 patch_lines=0 修复):
#   连续 N 轮只调只读工具零写入 -> 注入推进提醒 (不熔断), 有写入即清零, 提醒次数有上限。
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))
from lib.agent_state import BatchWriteState  # noqa: E402


def test_streak_reaches_threshold_triggers_reminder():
    s = BatchWriteState()
    fired = [s.readonly_tick(False, threshold=10, max_reminders=3) for _ in range(10)]
    # 只有第 10 次 (达阈值) 应该触发, 前 9 次不触发
    assert fired[:9] == [False] * 9
    assert fired[9] is True
    assert s.readonly_streak == 0, "触发后应重置计数, 避免刷屏"
    assert s.readonly_reminder_count == 1
    print("[OK] 连续 10 轮只读触发提醒且计数重置")


def test_write_resets_streak():
    s = BatchWriteState()
    for _ in range(9):
        s.readonly_tick(False)
    assert s.readonly_streak == 9
    fired = s.readonly_tick(True, threshold=10)  # 这轮有写入
    assert fired is False
    assert s.readonly_streak == 0, "有写入的一轮必须清零, 不能误伤正常干活的 agent"
    print("[OK] 写入轮正确清零, 不误伤正常干活的 agent")


def test_reminder_capped_no_spam():
    s = BatchWriteState()
    fire_count = 0
    for _ in range(1000):
        if s.readonly_tick(False, threshold=10, max_reminders=3):
            fire_count += 1
    assert fire_count == 3, f"提醒次数应封顶在 max_reminders=3, 实际 {fire_count}"
    assert s.readonly_reminder_count == 3
    print("[OK] 提醒次数有上限, 长期只读任务 (纯调研) 不被反复刷屏")


def test_research_only_task_not_spammed_before_threshold():
    # 纯调研类任务: 只读轮数不到阈值不该被提醒
    s = BatchWriteState()
    for _ in range(9):
        assert s.readonly_tick(False, threshold=10) is False
    print("[OK] 未达阈值前不提醒, 短调研任务不受影响")


if __name__ == "__main__":
    test_streak_reaches_threshold_triggers_reminder()
    test_write_resets_streak()
    test_reminder_capped_no_spam()
    test_research_only_task_not_spammed_before_threshold()
    print("\n[PASS] READONLY-STREAK suite")
