"""DAG 步间传值支持取 JSON 字段的回归用例 (#1)。

背景 (2026-09-05): 旧 _substitute_step_placeholders 只支持 ${step:id:output}(整段),
而 when 条件早支持 $step.x.json.path 取 JSON 字段 —— 两套不一致。本次给 tool_args
的占位符加 ${step:id:json:<jq路径>}, 复用 core/_when_eval._jq_get, 让前步出 JSON、
后步取某字段喂工具的数据管道能通。

在容器内跑 (需 core._when_eval, 且 helpers 调用时才 import 它):
  docker exec litecode python3 /opt/litecode/tests/test_step_json_substitute.py
"""
import sys
import unittest
from types import SimpleNamespace

# 直接把 helpers 当顶层模块 import, 绕开 core.orchestrator.__init__ → dag → blackboard 链
sys.path.insert(0, "/opt/litecode")                        # core._when_eval (call-time import)
sys.path.insert(0, "/opt/litecode/core/orchestrator")      # helpers 当顶层
try:
    import helpers  # type: ignore  # noqa: E402
except ImportError:
    sys.path.insert(0, "/opt/litecode/litecodeext")
    sys.path.insert(0, "/opt/litecode/litecodeext/core/orchestrator")
    import helpers  # type: ignore  # noqa: E402
sub = helpers._substitute_step_placeholders


def _pr(**kw):
    """构造 prior_results: sid -> 带 .output 的对象。"""
    return {sid: SimpleNamespace(output=out) for sid, out in kw.items()}


class TestStepJsonSubstitute(unittest.TestCase):
    PR = None

    def setUp(self):
        self.PR = _pr(
            s1='{"url": "http://x.com", "score": 9, "items": ["a", "b"], "meta": {"n": 42}}',
            s2="纯文本不是JSON",
        )

    # ── 单占位符: 取到原值 (标量/对象) ──
    def test_single_string_field(self):
        self.assertEqual(sub("${step:s1:json:url}", self.PR), "http://x.com")

    def test_single_int_field(self):
        self.assertEqual(sub("${step:s1:json:score}", self.PR), 9)

    def test_single_array_index(self):
        self.assertEqual(sub("${step:s1:json:items[0]}", self.PR), "a")

    def test_single_nested(self):
        self.assertEqual(sub("${step:s1:json:meta.n}", self.PR), 42)

    # ── 内联: 混在字符串里 → str 替换 ──
    def test_inline_field(self):
        self.assertEqual(sub("去 ${step:s1:json:url} 抓取", self.PR), "去 http://x.com 抓取")

    def test_inline_int_to_str(self):
        self.assertEqual(sub("分数=${step:s1:json:score}", self.PR), "分数=9")

    # ── 老的 :output 不回归 ──
    def test_output_still_whole(self):
        r = sub("${step:s1:output}", self.PR)
        # 整段是合法 JSON → auto-JSON 解析成 dict
        self.assertIsInstance(r, dict)
        self.assertEqual(r["score"], 9)

    def test_output_nonjson_str(self):
        self.assertEqual(sub("${step:s2:output}", self.PR), "纯文本不是JSON")

    # ── 错误兜底: 非 JSON / 缺步 / 缺字段 ──
    def test_nonjson_field_marker(self):
        self.assertIn("非 JSON", sub("${step:s2:json:url}", self.PR))

    def test_missing_step_marker(self):
        self.assertIn("no such step", sub("${step:ghost:json:url}", self.PR))

    # ── shell_quote: json 字段也转义 ──
    def test_shell_quote_field(self):
        out = _pr(s=' {"cmd": "rm -rf /; echo hi"} '.strip())
        r = sub("${step:s:json:cmd}", out, shell_quote=True)
        self.assertTrue(r.startswith("'") or "\\" in r, f"应被 shlex 转义: {r}")

    # ── dict/list 递归里也生效 ──
    def test_in_tool_args_dict(self):
        args = {"path": "${step:s1:json:url}", "n": "${step:s1:json:score}"}
        r = sub(args, self.PR)
        self.assertEqual(r["path"], "http://x.com")
        self.assertEqual(r["n"], 9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
