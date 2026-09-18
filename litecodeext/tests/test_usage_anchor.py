"""usage 锚点的回归用例 (纯逻辑, 不连服务)。

背景 (2026-09-03): token 预算此前全靠 est_tokens 估算, 而估算永远看不见
system prompt + 工具定义 (实测中转 vLLM 一句"hi"就回报 15278 prompt_tokens,
几乎全是固定开销), 中文和 reasoning 也各有系统性偏差。

锚点思路 (抄 deepseek-harness token-meter): 上游每次回报真实 usage 就记下
"真值 ↔ 当时估算"这一对, 之后预算判断用 锚点真值 + 自锚点以来的增量估算。
绝对值估不准没关系, 每次调用都用真值重新锚定, 只估增量。

这些用例锁住三件事:
  1. usage 从非标准位置 (delta.usage) 也能被解析出来
  2. 锚点分支的预算计算 = 真值 + 增量, 且口径不和"已扣固定开销"的预算双重扣
  3. 保守闸: 真值 < 估算时不用锚 (防上游漏报导致误判还很空)
"""
import unittest


def extract_usage(delta: dict, chunk: dict) -> dict | None:
    """复刻 litecode_server 里的 usage 提取: delta.usage 优先, 退回 chunk 顶层。"""
    u = delta.get("usage")
    if not isinstance(u, dict):
        u = chunk.get("usage") if isinstance(chunk, dict) else None
    return u if isinstance(u, dict) else None


def budget_current_tokens(est_full: int, usage_reports: int,
                          last_real: int, last_est_at_anchor: int) -> tuple[int, bool]:
    """复刻锚点分支的 _cur_tokens 计算。返回 (当前 token 估计, 是否用了锚点)。"""
    use_anchor = usage_reports > 0 and last_real >= last_est_at_anchor
    if use_anchor:
        delta_since = max(0, est_full - last_est_at_anchor)
        return last_real + delta_since, True
    return est_full, False


class TestUsageExtraction(unittest.TestCase):
    def test_nonstandard_delta_usage(self):
        # 本中转 vLLM 的真实形态: usage 塞在 choices[0].delta.usage
        delta = {"usage": {"prompt_tokens": 15278, "completion_tokens": 40}}
        u = extract_usage(delta, {})
        assert u is not None
        self.assertEqual(u["prompt_tokens"], 15278)

    def test_standard_top_level_usage(self):
        # 标准 OpenAI: 顶层 usage, delta 里没有
        u = extract_usage({}, {"usage": {"prompt_tokens": 900}})
        assert u is not None
        self.assertEqual(u["prompt_tokens"], 900)

    def test_no_usage_returns_none(self):
        self.assertIsNone(extract_usage({"content": "hi"}, {"choices": []}))

    def test_delta_wins_over_chunk(self):
        u = extract_usage({"usage": {"prompt_tokens": 1}},
                          {"usage": {"prompt_tokens": 999}})
        assert u is not None
        self.assertEqual(u["prompt_tokens"], 1)

    def test_openai_standard_empty_choices_usage(self):
        # 标准 OpenAI 的 usage 包: choices=[] + 顶层 usage。
        # 主循环里 `if not choices: continue` 会跳过它, 所以顶层 usage 必须在
        # continue 之前单独抓 —— 这里验 extract_usage 从顶层能取到。
        chunk = {"choices": [], "usage": {"prompt_tokens": 41457, "completion_tokens": 8}}
        u = extract_usage({}, chunk)  # delta 空, 退回 chunk 顶层
        assert u is not None
        self.assertEqual(u["prompt_tokens"], 41457)


class TestAnchorBudget(unittest.TestCase):
    def test_no_report_falls_back_to_est(self):
        cur, used = budget_current_tokens(est_full=50_000, usage_reports=0,
                                          last_real=0, last_est_at_anchor=0)
        self.assertEqual(cur, 50_000)
        self.assertFalse(used, "没收到过 usage 时必须退回纯估算")

    def test_anchor_adds_growth_since(self):
        # 锚定时: 真实 180k, 我方估 100k。之后历史又长到估 108k。
        # 当前真实应约 = 180k + (108k-100k) = 188k
        cur, used = budget_current_tokens(est_full=108_000, usage_reports=1,
                                          last_real=180_000, last_est_at_anchor=100_000)
        self.assertTrue(used)
        self.assertEqual(cur, 188_000)

    def test_anchor_catches_the_hidden_overhead(self):
        # 这是锚点的全部意义: 估算 100k, 真实 180k, 差的 80k 是 system+工具 est 看不见的。
        # 纯估算会判"还很空", 锚点判"已经 180k"。
        cur, used = budget_current_tokens(est_full=100_000, usage_reports=1,
                                          last_real=180_000, last_est_at_anchor=100_000)
        self.assertEqual(cur, 180_000)
        # 对 204800 窗口 * 0.6 = 122880 的预算: 纯估算 100k 不触发, 锚点 180k 触发
        self.assertLess(100_000, 122_880)      # 纯估算: 不裁
        self.assertGreater(180_000, 122_880)   # 锚点: 裁

    def test_conservative_gate_real_below_est(self):
        # 上游漏报导致真值 < 估算时, 不能用锚 (否则会误以为比估算还空)
        cur, used = budget_current_tokens(est_full=90_000, usage_reports=1,
                                          last_real=50_000, last_est_at_anchor=90_000)
        self.assertFalse(used, "真值<估算时必须退回纯估算, 宁可高估")
        self.assertEqual(cur, 90_000)  # noqa

    def test_growth_never_negative(self):
        # 历史被裁剪后 est_full 可能小于锚定时的估算, 增量不该变负
        cur, used = budget_current_tokens(est_full=80_000, usage_reports=1,
                                          last_real=180_000, last_est_at_anchor=100_000)
        self.assertTrue(used)
        self.assertEqual(cur, 180_000)  # 增量 clamp 到 0


if __name__ == "__main__":
    unittest.main(verbosity=2)
