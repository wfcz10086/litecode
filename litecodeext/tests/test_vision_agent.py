"""vision agent_type 的回归用例 (#3)。

背景 (2026-09-05): 新增 "vision" agent_type —— 看图+推理决策的 LLM 角色, allowed 含
vision_ocr, 配合决策节点做"看图→分支"。验证: dag_schema 接受 vision 步; subagent 配置
里 vision 角色确实开了 vision_ocr。
"""
import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, "/opt/litecode")
sys.path.insert(0, "/opt/litecode/litecodeext")

from lib.dag_schema import validate_dag_json  # noqa: E402

_SUBAGENT = None
for _p in ("/opt/litecode/lib/agent/subagent.py",
           "/opt/litecode/litecodeext/lib/agent/subagent.py"):
    if Path(_p).exists():
        _SUBAGENT = Path(_p); break


class TestVisionAgent(unittest.TestCase):
    # ── dag_schema 接受 vision agent_type ──
    def test_schema_accepts_vision(self):
        ok, err = validate_dag_json({"name": "t", "steps": [
            {"id": "shot", "agent_type": "shell", "task": "截图到 /tmp/x.png"},
            {"id": "see", "agent_type": "vision", "task": "看图判断是否报错页", "depends_on": ["shot"]},
        ]})
        self.assertTrue(ok, err)

    def test_schema_rejects_bogus(self):
        ok, _ = validate_dag_json({"name": "t", "steps": [
            {"id": "a", "agent_type": "nonsense", "task": "x"},
        ]})
        self.assertFalse(ok, "非法 agent_type 应被拒")

    # ── vision + 决策节点条件 组合能存 ──
    def test_vision_with_when(self):
        ok, err = validate_dag_json({"name": "t", "steps": [
            {"id": "see", "agent_type": "vision", "task": "OCR 发票, 输出 JSON 金额"},
            {"id": "big", "agent_type": "writer", "task": "大额审批",
             "when": [{"gt": ["$step.see.json.amount", 1000]}], "depends_on": ["see"]},
        ]})
        self.assertTrue(ok, err)

    # ── subagent 配置: vision 角色开了 vision_ocr ──
    def test_subagent_vision_allows_ocr(self):
        self.assertIsNotNone(_SUBAGENT, "找不到 subagent.py")
        src = _SUBAGENT.read_text(encoding="utf-8")
        # 定位 _SUBAGENT_CONFIGS 里的 vision 配置块 (简单断言: 存在 vision key 且其 allowed 含 vision_ocr)
        self.assertIn('"vision":', src, "subagent 缺 vision 角色")
        # vision 块到下一个顶层 key/结尾之间应含 vision_ocr
        i = src.index('"vision":')
        block = src[i:i + 600]
        self.assertIn("vision_ocr", block, "vision 角色的 allowed 未开 vision_ocr")


if __name__ == "__main__":
    unittest.main(verbosity=2)
