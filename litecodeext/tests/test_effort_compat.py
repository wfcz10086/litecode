"""reasoning_effort 兼容映射的回归用例。

背景 (2026-09-03): 各模型对"思考强度"的控制方式不一。官方 vLLM 新版支持
reasoning_effort: low/medium/high, 但**本自建 qwen3.8 收下但忽略它**
(实测 none/low/high 四个值全 HTTP 200, 而思考量乱序: none=940字 low=1338 high=780 —
 完全不按 effort 分级)。所以真控制仍靠 chat_template_kwargs + max_tokens+=budget。

兼容做法: vllm 分支两个都发 —— budget 兜底当前上游, reasoning_effort 给认它的上游预留。
这些用例锁住: 默认映射与前端 🧠 chip 档位对齐；可选模型映射同时兼容自动和显式强度。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_LIB = str(Path(__file__).resolve().parent.parent / "lib")
sys.path.insert(0, _LIB)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import thinking_adapter as ta  # noqa: E402


class _FakeLive(dict):
    """冒充 lib.config._LIVE, 让 _live_cfg 读到我们要的 budget/enable。"""


def _inject(budget: int, enabled: bool = True, backend="vllm", explicit_effort=None,
            effort_map=None, force_disable=False):
    """在受控的 _LIVE 下跑 inject_params, 返回 payload。"""
    ta._active = None                       # 清缓存, 强制现读
    orig = ta._get_loaded_live
    ta._get_loaded_live = lambda: _FakeLive(
        enable_thinking=enabled, thinking_budget=budget,
        backend_type=backend, deployment="vllm",
        reasoning_effort_map=effort_map or {},
    )
    try:
        payload = {"model": "qwen3.8-flash-next-local",
                   "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8192}
        if explicit_effort is not None:
            payload["reasoning_effort"] = explicit_effort
        return ta.inject_params(payload, force_disable=force_disable)
    finally:
        ta._get_loaded_live = orig
        ta._active = None


class TestEffortCompat(unittest.TestCase):
    # ── budget → effort 映射, 对齐前端档位 (低2048/中4096/高16384) ──
    def test_low_budget_maps_low(self):
        p = _inject(2048)
        self.assertEqual(p["reasoning_effort"], "low")

    def test_mid_budget_maps_medium(self):
        p = _inject(4096)
        self.assertEqual(p["reasoning_effort"], "medium")

    def test_high_budget_maps_high(self):
        p = _inject(16384)
        self.assertEqual(p["reasoning_effort"], "high")

    def test_boundary_8000_is_medium(self):
        self.assertEqual(_inject(8000)["reasoning_effort"], "medium")

    def test_boundary_8001_is_high(self):
        self.assertEqual(_inject(8001)["reasoning_effort"], "high")

    # ── 兼容: budget 机制必须仍在 (真控制靠它) ──
    def test_budget_mechanism_still_present(self):
        p = _inject(4096)
        self.assertIn("chat_template_kwargs", p)
        self.assertTrue(p["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(p["max_tokens"], 8192 + 4096, "max_tokens 必须仍 += budget")

    # ── 关闭思考时不发 effort ──
    def test_disabled_no_effort(self):
        p = _inject(4096, enabled=False)
        self.assertNotIn("reasoning_effort", p)
        self.assertFalse(p["chat_template_kwargs"]["enable_thinking"])

    # ── 调用方显式传的 effort 不被覆盖 ──
    def test_explicit_effort_preserved(self):
        p = _inject(2048, explicit_effort="high")
        self.assertEqual(p["reasoning_effort"], "high",
                         "显式传的 effort 优先, 不被 budget 映射覆盖")


class TestModelEffortMap(unittest.TestCase):
    def test_only_configured_auto_effort_changes(self):
        for budget, expected in ((2048, "low"), (4096, "medium"), (16384, "xhigh")):
            with self.subTest(budget=budget):
                payload = _inject(budget, effort_map={"high": "xhigh"})
                self.assertEqual(payload["reasoning_effort"], expected)
                self.assertEqual(payload["max_tokens"], 8192 + budget)
                self.assertTrue(payload["chat_template_kwargs"]["enable_thinking"])

    def test_explicit_effort_uses_model_names_instead_of_budget(self):
        for explicit, expected in (("high", "xhigh"), ("medium", "medium"), ("xhigh", "xhigh")):
            with self.subTest(explicit=explicit):
                payload = _inject(2048, explicit_effort=explicit, effort_map={"high": "xhigh"})
                self.assertEqual(payload["reasoning_effort"], expected)

    def test_force_disable_does_not_add_effort_or_budget(self):
        payload = _inject(16384, effort_map={"high": "xhigh"}, force_disable=True)
        self.assertNotIn("reasoning_effort", payload)
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(payload["max_tokens"], 8192)

    def test_cached_init_and_update_without_live_config(self):
        with patch.object(ta, "_get_loaded_live", return_value=None), patch.object(ta, "_active", None):
            effort_map = {"high": "xhigh"}
            model = {"backend_type": "vllm", "enable_thinking": True, "thinking_budget": 16384,
                     "reasoning_effort_map": effort_map}
            ta.init(model)
            effort_map["high"] = "medium"  # init 保存独立副本。
            self.assertEqual(ta.inject_params({})["reasoning_effort"], "xhigh")
            ta.update({k: v for k, v in model.items() if k != "reasoning_effort_map"})
            self.assertEqual(ta.get().reasoning_effort_map, {})
            self.assertEqual(ta.inject_params({})["reasoning_effort"], "high")

    def test_init_syncs_map_but_live_changes_take_priority(self):
        live = _FakeLive()
        with patch.object(ta, "_get_loaded_live", return_value=live), patch.object(ta, "_active", None):
            ta.init({"backend_type": "vllm", "enable_thinking": True, "thinking_budget": 16384,
                     "reasoning_effort_map": {"high": "xhigh"}})
            self.assertEqual(live["reasoning_effort_map"], {"high": "xhigh"})
            self.assertEqual(ta.inject_params({})["reasoning_effort"], "xhigh")
            live["reasoning_effort_map"] = {}
            self.assertEqual(ta.inject_params({})["reasoning_effort"], "high")
            live["reasoning_effort_map"] = {"high": "xhigh"}
            ta._active = None  # _LIVE 回填缓存路径也保留模型映射。
            self.assertEqual(ta.inject_params({})["reasoning_effort"], "xhigh")
            self.assertEqual(ta.get().reasoning_effort_map, {"high": "xhigh"})

    def test_map_does_not_change_other_backend(self):
        with patch.object(ta, "_get_loaded_live", return_value=None), patch.object(ta, "_active", None):
            ta.init({"backend_type": "openai", "enable_thinking": True,
                     "reasoning_effort_map": {"high": "xhigh"}})
            self.assertEqual(ta.inject_params({"reasoning_effort": "high"}),
                             {"reasoning_effort": "high"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
