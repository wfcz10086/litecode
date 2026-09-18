"""web 会话刷新后思考内容 (reasoning) 不丢失的回归用例。

背景 (2026-09-04): 现象是 web 端一条会话, 前几条 AI 有推理步骤, 多问几轮后刷新页面,
推理步骤(思考内容)消失。根因: load_session 重导 server session 时, 用无 reasoning
的重建 (_convert_server_session) 覆盖了 web 缓存 —— 而 terminal 答案的 reasoning 本来
只在缓存里 (SSE 存的). _merge_cached_reasoning 负责重导时按内容前缀把老缓存的 reasoning
补回, 不让它被抹掉。

web_ui.py 顶层是重量级 FastAPI app, 直接 import 太重, 这里用 AST 只抽 _merge_cached_reasoning
在干净 namespace (注入 json) 里测。
"""
import ast
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_WEBUI = Path(__file__).resolve().parent.parent / "web_ui.py"


def _load_func(name: str):
    tree = ast.parse(_WEBUI.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {"json": json}
            exec(compile(mod, str(_WEBUI), "exec"), ns)
            return ns[name]
    raise AssertionError(f"未找到 {name}")


merge = _load_func("_merge_cached_reasoning")


class TestReasoningPersist(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "cache.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_cache(self, msgs):
        self.cache.write_text(json.dumps({"messages": msgs}), encoding="utf-8")

    # ── 核心: 重导的无 reasoning 消息, 从老缓存按内容前缀补回 ──
    def test_merges_reasoning_by_content(self):
        self._write_cache([
            {"role": "user", "content": "问题一"},
            {"role": "assistant", "content": "答案一的正文", "reasoning": "这是第一段思考推理"},
        ])
        web_d = {"messages": [
            {"role": "user", "content": "问题一"},
            {"role": "assistant", "content": "答案一的正文", "reasoning": ""},  # 重导后 reasoning 空
        ]}
        merge(self.cache, web_d)
        self.assertEqual(web_d["messages"][1]["reasoning"], "这是第一段思考推理",
                         "重导后应从缓存补回 reasoning")

    # ── 已有 reasoning 不被覆盖 ──
    def test_does_not_clobber_existing(self):
        self._write_cache([
            {"role": "assistant", "content": "答案", "reasoning": "老的"},
        ])
        web_d = {"messages": [
            {"role": "assistant", "content": "答案", "reasoning": "新的(来自server)"},
        ]}
        merge(self.cache, web_d)
        self.assertEqual(web_d["messages"][0]["reasoning"], "新的(来自server)",
                         "web_d 已有 reasoning 时不应被老缓存覆盖")

    # ── 内容对不上不乱补 ──
    def test_no_match_no_change(self):
        self._write_cache([
            {"role": "assistant", "content": "完全不同的答案", "reasoning": "某段思考"},
        ])
        web_d = {"messages": [
            {"role": "assistant", "content": "另一个答案", "reasoning": ""},
        ]}
        merge(self.cache, web_d)
        self.assertEqual(web_d["messages"][0]["reasoning"], "",
                         "内容对不上不该乱补 reasoning")

    # ── 缓存不存在 / 无 reasoning → 不报错, web_d 原样 ──
    def test_missing_cache_safe(self):
        web_d = {"messages": [{"role": "assistant", "content": "x", "reasoning": ""}]}
        merge(self.cache, web_d)  # cache 文件不存在
        self.assertEqual(web_d["messages"][0]["reasoning"], "")

    def test_corrupt_cache_safe(self):
        self.cache.write_text("{不是合法json", encoding="utf-8")
        web_d = {"messages": [{"role": "assistant", "content": "x", "reasoning": ""}]}
        merge(self.cache, web_d)  # 不抛
        self.assertEqual(web_d["messages"][0]["reasoning"], "")

    # ── 多轮: 只补对得上的那几条 ──
    def test_multi_turn_partial(self):
        self._write_cache([
            {"role": "assistant", "content": "答一", "reasoning": "思考一"},
            {"role": "assistant", "content": "答二", "reasoning": "思考二"},
        ])
        web_d = {"messages": [
            {"role": "assistant", "content": "答一", "reasoning": ""},
            {"role": "assistant", "content": "答二", "reasoning": "已有"},
            {"role": "assistant", "content": "答三(新)", "reasoning": ""},
        ]}
        merge(self.cache, web_d)
        self.assertEqual(web_d["messages"][0]["reasoning"], "思考一")   # 补回
        self.assertEqual(web_d["messages"][1]["reasoning"], "已有")     # 不覆盖
        self.assertEqual(web_d["messages"][2]["reasoning"], "")         # 无匹配


if __name__ == "__main__":
    unittest.main(verbosity=2)
