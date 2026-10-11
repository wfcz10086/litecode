"""lib/tool_args.parse_tool_args: 可恢复的坏参数就地恢复, 不可恢复的给出可行动错误。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))

from lib.tool_args import parse_tool_args  # noqa: E402


def test_normal_object():
    assert parse_tool_args('{"filepath": "a.py", "content": "x"}', "write_file") == (
        {"filepath": "a.py", "content": "x"}, None)


def test_empty_and_none_mean_no_args():
    assert parse_tool_args("", "t") == ({}, None)
    assert parse_tool_args(None, "t") == ({}, None)
    assert parse_tool_args("   ", "t") == ({}, None)


def test_dict_passthrough():
    assert parse_tool_args({"a": 1}) == ({"a": 1}, None)


def test_code_fence_recovered():
    raw = '```json\n{"command": "ls -la"}\n```'
    assert parse_tool_args(raw, "execute_shell") == ({"command": "ls -la"}, None)


def test_double_encoded_recovered():
    raw = json.dumps(json.dumps({"query": "pelican"}))
    assert parse_tool_args(raw, "web_search") == ({"query": "pelican"}, None)


def test_broken_json_gives_actionable_error():
    raw = '{"filepath": "index.html", "content": "<svg width="200">"}'
    args, err = parse_tool_args(raw, "write_file")
    assert args == {}
    assert err.startswith("ERROR: 工具 write_file 的参数不是合法 JSON")
    assert "本次未执行" in err and "第 " in err and "\\n" in err


def test_truncated_json_error_mentions_position():
    args, err = parse_tool_args('{"command": "echo hi', "execute_shell")
    assert args == {} and "execute_shell" in err and "字符附近" in err


def test_non_object_rejected():
    args, err = parse_tool_args('["a", "b"]', "read_file")
    assert args == {} and "必须是 JSON 对象" in err and "list" in err
