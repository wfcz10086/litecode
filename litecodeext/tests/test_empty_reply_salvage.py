"""空回复触顶时「content 通道永不为空」兜底的回归用例。

背景 (2026-09-04): litecode_server 连续空回复触顶放弃时, 旧逻辑只回一条干瘪的
工作摘要, 把答案被误路由进 reasoning 的情况 (content 空、reasoning 有实质内容,
实测 deep_thinking=false 会这样) 默默丢掉。_salvage_empty_reply 负责在放弃时把
reasoning 尾段取回当正文。

_salvage_empty_reply 是纯函数, 但 litecode_server 顶层有重量级初始化 (加载 config、
初始化 deep_search 等), 直接 import 太重/易碎。这里用 AST 只抽出这一个函数, 在干净
namespace 里 exec 后测试, 不触发任何模块级副作用。
"""
import ast
import unittest
from pathlib import Path

_SERVER = Path(__file__).resolve().parent.parent / "litecode_server.py"


def _load_func(name: str):
    tree = ast.parse(_SERVER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            mod = ast.Module(body=[node], type_ignores=[])
            ns: dict = {}
            exec(compile(mod, str(_SERVER), "exec"), ns)
            return ns[name]
    raise AssertionError(f"未在 {_SERVER.name} 找到函数 {name}")


salvage = _load_func("_salvage_empty_reply")


class TestEmptyReplySalvage(unittest.TestCase):
    # ── 有实质 reasoning → 必须取回当正文, 不能只给摘要 ──
    def test_salvages_reasoning_into_body(self):
        rz = "本卦是屯卦，卦辞说勿用有攸往，利建侯。意思是刚起步先别急着扩张。"
        out = salvage(3, ["execute_shell", "read_file"], rz)
        self.assertIn("屯卦", out, "reasoning 里的真答案必须出现在回传正文里")
        self.assertIn("取回", out)

    def test_iteration_count_shown(self):
        out = salvage(3, ["ls"], "some substantial reasoning content here 够长")
        self.assertIn("4 轮", out, "iteration+1 应显示为轮数")

    # ── reasoning 为空/太短 → 退回纯工作摘要, 不硬塞 ──
    def test_no_reasoning_falls_back_to_summary(self):
        out = salvage(5, ["web_search", "write_file"], "")
        self.assertNotIn("取回", out)
        self.assertIn("停止生成", out)
        self.assertIn("web_search", out)

    def test_too_short_reasoning_not_salvaged(self):
        out = salvage(2, [], "嗯")           # <20 字, 无实质
        self.assertNotIn("取回", out)
        self.assertIn("(无)", out)           # 无工具调用

    # ── 超长 reasoning 截尾 + 前置省略号 ──
    def test_long_reasoning_truncated_from_tail(self):
        rz = "A" * 100 + "结尾关键结论B" * 200   # 远超 max_salvage
        out = salvage(1, ["x"], rz, max_salvage=300)
        self.assertIn("结尾关键结论B", out, "应保留尾段 (结论通常在最后)")
        self.assertIn("…", out, "截断应有省略号标记")
        # 取回的正文段不应超过 max_salvage 太多
        self.assertLess(len(out), 300 + 400)

    # ── 工具列表只显示最近 6 个 ──
    def test_recent_tools_only(self):
        tools = [f"t{i}" for i in range(10)]
        out = salvage(9, tools, "")
        self.assertIn("t9", out)
        self.assertNotIn("t0", out)         # 最早的被省略


if __name__ == "__main__":
    unittest.main(verbosity=2)
