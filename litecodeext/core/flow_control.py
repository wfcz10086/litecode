"""
flow_control.py — Agent 循环流量控制器 (v1.0)
═══════════════════════════════════════════════
从 litecode_server.py 提取的三大流控:
  LOOP_BREAK     — 连续相同工具调用 → 强制策略切换
  SLIDING_WINDOW — 每 N 轮清理旧 tool result，防止 context 膨胀
  BATCH_REMIND   — 连续单 write_file → 提醒批量写入
"""

import logging
from typing import Optional

log = logging.getLogger("openclaw")


class FlowController:
    """
    Agent 循环的流量控制器，每个 session 一个实例。

    用法:
        fc = FlowController()
        for iteration in agent_loop:
            trimmed = fc.sliding_window(messages, iteration)
            # ... 执行工具 ...
            hint = fc.check_loop_break(tool_name, result)
            batch_hint = fc.check_batch_remind(tool_name)
    """

    def __init__(
        self,
        *,
        loop_threshold: int = 3,
        sw_interval: int = 10,
        sw_keep_recent: int = 6,
        batch_threshold: int = 3,
    ):
        self.loop_threshold = loop_threshold
        self.sw_interval = sw_interval
        self.sw_keep_recent = sw_keep_recent
        self.batch_threshold = batch_threshold
        self.reset()

    def reset(self):
        """新任务开始时重置状态"""
        self._consec_same = 0
        self._last_tool = ""
        self._last_result_hash = 0
        self._consec_same_result = 0  # [v1.0] 相同结果连续计数
        self._consec_writes = 0

    # ── LOOP_BREAK ────────────────────────────────────────────

    def check_loop_break(self, tool_name: str, result: str = "") -> Optional[str]:
        """
        检测死循环，两个维度：
        1. 连续调用同一工具 ≥ loop_threshold 次
        2. 同工具连续返回完全相同的结果 ≥ loop_threshold 次（即使参数不同也算卡死）
        返回注入提示或 None
        """
        result_hash = hash(result[:500]) if result else 0

        # ── 维度 1: 连续同工具 ──
        # [v1.0] 语义: 连续第 N 次调用同工具时返回 hint（而非第 N+1 次）
        if tool_name == self._last_tool:
            self._consec_same += 1
        else:
            self._consec_same = 1  # 初次调用也算 1 次，不是 0

        # ── 维度 2: 连续相同结果（v1.0 真正实现）──
        if tool_name == self._last_tool and result_hash == self._last_result_hash and result_hash != 0:
            self._consec_same_result += 1
        else:
            self._consec_same_result = 1

        self._last_tool = tool_name
        self._last_result_hash = result_hash

        if self._consec_same >= self.loop_threshold:
            self._consec_same = 0
            log.warning(f"  [LOOP_BREAK] forcing strategy change after "
                        f"{self.loop_threshold} consecutive loops")
            return (
                f"[LOOP_BREAK] 你已连续 {self.loop_threshold} 次调用 {tool_name}。"
                "请立即换一种方法或工具完成任务。如果任务已完成，直接输出结果。"
            )

        # [v1.0] 相同结果提前触发（2 次就够，因为相同结果更强的死循环信号）
        if self._consec_same_result >= 2:
            self._consec_same_result = 0
            log.warning(f"  [LOOP_BREAK] same result detected {self._consec_same_result+1}x from {tool_name}")
            return (
                f"[LOOP_BREAK] {tool_name} 连续返回完全相同的结果。"
                "说明当前路径已无新信息，请立即换工具/换参数/换思路。"
            )
        return None

    # ── SLIDING_WINDOW ────────────────────────────────────────

    def sliding_window(self, messages: list, iteration: int) -> int:
        """
        每 N 轮清理旧 tool result 的详细内容，防止 context 膨胀。
        返回清理的消息数量。
        """
        if iteration <= 0 or iteration % self.sw_interval != 0:
            return 0

        tool_indices = [
            i for i, m in enumerate(messages)
            if m.get("role") == "tool"
        ]
        if len(tool_indices) <= self.sw_keep_recent:
            return 0

        trimmed = 0
        for idx in tool_indices[:-self.sw_keep_recent]:
            content = messages[idx].get("content", "")
            if isinstance(content, str) and len(content) > 200:
                original_len = len(content)
                messages[idx]["content"] = (
                    f"[cleared: {original_len}c] {content[:80]}..."
                )
                trimmed += 1

        if trimmed > 0:
            log.info(f"  [SLIDING-WINDOW] trimmed {trimmed} old messages "
                     f"at iter {iteration}")
        return trimmed

    # ── BATCH_REMIND ──────────────────────────────────────────

    def check_batch_remind(self, tool_name: str) -> Optional[str]:
        """检测连续单文件 write_file，提醒批量写入"""
        if tool_name == "write_file":
            self._consec_writes += 1
        else:
            self._consec_writes = 0
            return None

        if self._consec_writes >= self.batch_threshold:
            self._consec_writes = 0
            log.info(f"  [BATCH] injected batch write reminder after "
                     f"{self.batch_threshold} consecutive single writes")
            return (
                "[BATCH] 你已连续多轮各写一个文件。"
                "请将剩余独立文件在同一轮批量写入，节省迭代次数。"
            )
        return None
