"""桌面/远程操作类回合: 收尾不重复落盘, 写代码那套 PLAN/EXEC/READONLY 闸不误触发。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib.agent_state import BatchWriteState


def _imported_turn():
    # 模拟 server 历史重导后的形态: 本轮逐条 assistant (带 _tool_results), 最后一条是最终答案
    return [
        {'role': 'user', 'content': '打开 qq 看工作群'},
        {'role': 'assistant', 'content': '', '_tool_results': [], 'tools': ['windows_desktop: ...']},
        {'role': 'assistant', 'content': '开始菜单已打开。', '_tool_results': []},
        {'role': 'assistant', 'content': '✓ 已进入群, 最新消息如下。', '_tool_results': []},
    ]


def test_reimported_turn_not_appended_again_but_keeps_screenshots():
    import web_ui
    msgs = _imported_turn()
    steps = [{'id': 's1', 'ok': True, 'image': '/x/a.png'}]
    collected = {'text': '\n\n开始菜单已打开。\n\n✓ 已进入群, 最新消息如下。', 'desktop_steps': steps,
                 'usage': {'total_tokens': 9}}
    assert web_ui._turn_already_imported(msgs, collected) is True
    assert msgs[-1]['desktop_steps'] == steps and msgs[-1]['usage'] == {'total_tokens': 9}


def test_fresh_web_turn_still_appended():
    import web_ui
    msgs = [{'role': 'user', 'content': 'hi'}]
    assert web_ui._turn_already_imported(msgs, {'text': '你好'}) is False
    # 上一轮是重导的, 本轮最终答案不在其中 → 照常追加
    msgs = _imported_turn() + [{'role': 'user', 'content': '再看下微信'}]
    assert web_ui._turn_already_imported(msgs, {'text': '微信已打开'}) is False


def test_action_tools_exempt_plan_and_exec_gates():
    s = BatchWriteState(action_tool_calls=1)
    assert not any(s.plan_gate_tick(False) for _ in range(10))
    assert s.exec_gate_tick(10) is False


def test_coding_turn_gates_unchanged():
    s = BatchWriteState()
    assert [s.plan_gate_tick(False) for _ in range(5)][-1] is True
    assert s.exec_gate_tick(5) is True


def test_action_tool_classification():
    import litecode_server as srv
    for n in ('windows_desktop', 'ssh_exec', 'docker_remote_ps', 'browser_act', 'desktop_click'):
        assert srv._is_action_tool(n)
    for n in ('read_file', 'write_file', 'browser_read', 'web_search', 'execute_shell'):
        assert not srv._is_action_tool(n)
