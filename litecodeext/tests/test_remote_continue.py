"""[SYSTEM-CONTINUE] 远程操控回合"宣布下一步却没调工具"续跑闸 (2026-10-21 真机 E2E 两次半截收尾)。"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core'), str(ROOT / 'lib')]

from lib.agent import remote_continue as rc   # noqa: E402


def _turn(*names, user='在 win-hugebq 上用记事本写一段中文'):
    messages = [{'role': 'user', 'content': user}]
    for name in names:
        messages.append({'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': name, 'arguments': '{}'}}]})
        messages.append({'role': 'tool', 'content': '{"ok": true}'})
    return messages


def test_real_dangling_endings_are_detected():
    assert rc.dangling_intent('文件保存可能出了问题。\n\n让我用 `list_dir` 确认文件是否存在：')
    assert rc.dangling_intent('从截图来看, "+" 在标签栏右边。\n\n让我用坐标来点击这个按钮。')
    assert rc.dangling_intent('**下一步：点击 "Save" 按钮保存文件**')
    assert rc.dangling_intent('好的，内容已全选，现在输入正确的中文内容会替换掉全部内容：')


def test_real_final_answers_pass():
    assert not rc.dangling_intent('文件已保存并回读核对，内容完全一致。')
    assert not rc.dangling_intent('| 项目 | 结果 |\n|---|---|\n| 保存 | 成功 |\n\n以上就是全部结果。')
    assert not rc.dangling_intent('桌面已锁定，无法继续操作，需要你先解锁 Windows。')
    assert not rc.dangling_intent('')


def test_only_turns_with_remote_tools_and_at_most_twice():
    text = '让我再截图确认一下：'
    ctx = types.SimpleNamespace(messages=_turn('read_file'))
    assert rc.check(ctx, text) is None                       # 没用远程工具的回合不管
    ctx = types.SimpleNamespace(messages=_turn('windows_desktop', 'windows_system'))
    assert rc.check(ctx, text).startswith(rc.PREFIX)
    assert rc.check(ctx, text).startswith(rc.PREFIX)
    assert rc.check(ctx, text) is None                       # 每回合最多两次
    assert rc.check(types.SimpleNamespace(messages=_turn('ssh_exec')), '主机负载正常，总结如下。') is None


def test_previous_turn_tools_do_not_count():
    messages = _turn('windows_desktop') + [{'role': 'assistant', 'content': '完成'},
                                           {'role': 'user', 'content': '帮我写一首诗'}]
    assert rc.used_remote_tools(messages) is False
    messages.append({'role': 'user', 'content': '[SYSTEM-READBACK] 请回读'})   # 系统注入不算新回合起点
    assert rc.used_remote_tools(messages) is False


def test_new_msgs_of_this_turn_take_precedence():
    # ctx.messages 里夹着不以 "[" 开头的注入 user 消息也不影响: 以本回合 new_msgs 为准
    ctx = types.SimpleNamespace(messages=[{'role': 'user', 'content': '注意：以上是工具结果'}],
                                new_msgs=_turn('windows_desktop')[1:])
    assert rc.check(ctx, '**下一步**：在左侧导航栏中双击 lc_remote_test 文件夹。') is not None
    ctx = types.SimpleNamespace(messages=_turn('windows_desktop'), new_msgs=_turn('read_file')[1:])
    assert rc.check(ctx, '让我再看看：') is None


def test_english_plan_sentences_from_benchmark():
    assert rc.dangling_intent('Got the desktop. Let me click on the desktop background first, then launch Notepad.')
    assert rc.dangling_intent("Next, I'll open the Save As dialog:")
    assert rc.dangling_intent('I will now type the text into Notepad.')
    assert not rc.dangling_intent('Done. The file was saved and read back; contents match exactly.')


def _call(tool, **args):
    import json
    return {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': tool, 'arguments': json.dumps(args, ensure_ascii=False)}}]}


ASK = '在 win-hugebq 上用记事本输入一段中文，保存到 C:\\x\\a.txt，读回核对，完成后关闭记事本。桌面上的 LiteCode 窗口别关它。'


def test_checklist_flags_forgotten_close_once():
    turn = [{'role': 'user', 'content': ASK},
            _call('windows_desktop', action='launch_app', name='notepad'),
            _call('windows_desktop', action='type_text', text='你好'),
            _call('windows_desktop', action='hotkey', keys='ctrl+s'),
            _call('windows_system', action='inspect_file', path='C:\\x\\a.txt')]
    assert rc.missing_steps(ASK, turn) == ['关闭窗口']
    ctx = types.SimpleNamespace(messages=[], new_msgs=turn)
    text = rc.check(ctx, '文件已保存并回读核对，内容完全一致。')
    assert text.startswith(rc.CHECK_PREFIX) and '关闭窗口' in text
    assert rc.check(ctx, '文件已保存并回读核对，内容完全一致。') is None      # 只提醒一次


def test_checklist_passes_when_all_steps_have_evidence_and_respects_negation():
    turn = [{'role': 'user', 'content': ASK},
            _call('windows_desktop', action='launch_app', name='notepad'),
            _call('windows_desktop', action='click_element', text='保存(S)'),
            _call('windows_system', action='inspect_file', path='C:\\x\\a.txt'),
            _call('windows_desktop', action='close_window', title='a.txt - Notepad')]
    assert rc.missing_steps(ASK, turn) == []
    only_negated = '截个图看看桌面，别关闭任何窗口，也不要保存。'
    assert rc.missing_steps(only_negated, [_call('windows_desktop', action='screenshot')]) == []
    # 没用过桌面工具 (纯命令行/ssh) 的回合不做 GUI 核对
    assert rc.missing_steps(ASK, [_call('ssh_exec', alias='local', command='ls')]) == []
