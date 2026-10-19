"""[2026-10-19] 核心 + Windows SSH/WSL 审计修复的离线回归 (不连远端, 不需要 pwsh)。

覆盖: PowerShell using/param 头部上提、‘’ 弯引号路径/WSL 参数、WSL 非严格解码与超时、
临时目录探测/连接预算、NativeCommandError 提示、模型副本清理不改落盘证据、
回读闸 alias 归一化、循环封禁提示脱敏。
"""
import asyncio
import base64
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from core.tools.handlers import remote  # noqa: E402
from lib import windows_ssh as ws  # noqa: E402
from lib.agent import loop_budget, readback_gate as rb  # noqa: E402
from lib.agent import subagent_operator  # noqa: E402
from plugins.tools.guard import ToolLoopGuard, raw_display  # noqa: E402

CONN = {'host': 'example.test', 'port': 22, 'user': 'test', 'password': '',
        'key_file': '/test/key', 'jump': None}
PRELUDE = ws._POWERSHELL_PRELUDE


def _decode_ps(command: str) -> str:
    return base64.b64decode(command.split()[-1]).decode('utf-16le')


def _wsl_body(stdin: bytes) -> str:
    return base64.b64decode(stdin).decode('utf-8')


def _b64_literals(text: str) -> list:
    return [base64.b64decode(m).decode('utf-8') for m in re.findall(r"FromBase64String\('([^']*)'\)", text)]


# ── 3. using / param 头部必须在引导之前 ──

@pytest.mark.parametrize('script', [
    "Write-Output 1",
    "[Console]::Out.Write('x')",          # 类型字面量开头不是 attribute+param
    "[CmdletBinding()]\n$x = 1",          # 只有 attribute 没有 param, 不挪
    "$param = 1\nparam(1)",                # param 不是首句
])
def test_scripts_without_header_keep_prelude_first(script):
    assert ws.powershell_script(script) == PRELUDE + script


def test_using_statements_are_hoisted_above_prelude():
    script = "# 注释\n#requires -Version 5\nusing namespace System.Text\nusing module Foo; $sb = [StringBuilder]::new()\nexit 3"
    out = ws.powershell_script(script)
    head, _, rest = out.partition(PRELUDE)
    assert head.rstrip('\n') == "# 注释\n#requires -Version 5\nusing namespace System.Text\nusing module Foo;"
    assert rest == " $sb = [StringBuilder]::new()\nexit 3"
    assert out.replace(PRELUDE, '').replace('\n', '') == script.replace('\n', '')


def test_cmdletbinding_param_block_is_hoisted_with_tricky_defaults():
    header = ("<# 多行\n块注释 #>\n[CmdletBinding()]\n[OutputType([int])]\nparam(\n"
              "  [string]$Name = 'O’’Brien''s )',   # ) 注释里的括号\n"
              "  [string]$Q = \"a`\")\",\n  [int]$N = (1 + (2))\n)")
    body = "\nWrite-Output $Name\nexit $N"
    out = ws.powershell_script(header + body)
    assert out == header + '\n' + PRELUDE + body
    # 短 EncodedCommand 用同一份文本
    assert _decode_ps(ws.powershell_command(header + body)) == out


def test_unbalanced_param_is_left_alone():
    script = "param([string]$a = 'x'\nWrite-Output 1"
    assert ws.powershell_script(script) == PRELUDE + script


# ── 2. ‘’ 弯引号: 路径/参数一律 Base64, 不依赖引号转义 ──

@pytest.mark.parametrize('path', [
    "C:\\Users\\O’Brien\\AppData\\Local\\Temp\\LiteCodeSSH-1.ps1",
    "C:\\Users\\O‘Brien‚‛'x\\AppData\\Local\\Temp\\LiteCodeSSH-2.ps1",
    "C:\\Users\\a’); Remove-Item C:\\ -Recurse; (’\\t.ps1",
])
def test_file_bootstrap_path_with_curly_quotes_is_base64(path):
    text = _decode_ps(ws.powershell_file_command(path))
    assert _b64_literals(text) == [path]
    # 除 Base64 外没有任何用户可控字符进入脚本文本
    assert 'Brien' not in text and 'Remove-Item' not in text
    for q in ws._SQ[1:]:
        assert q not in text


def test_wsl_distro_and_user_with_curly_quotes_cannot_break_out():
    distro = "Ubuntu’; Start-Process calc; ’"
    command, stdin = ws.wsl_command('echo hi', distro, 'O’Brien')
    body = _wsl_body(stdin)
    assert 'calc' not in body and 'Brien' not in body
    args_line = next(line for line in body.splitlines() if '$info.Arguments' in line)
    (arguments,) = _b64_literals(args_line)
    assert arguments.startswith('--distribution "Ubuntu’; Start-Process calc; ’" --user O’Brien ')
    # 引导命令本身固定, 不含任何用户数据
    assert command == ws.wsl_command('echo other', 'X', '')[0]


# ── 1/4. WSL 解码不抛异常、按预算等待、总是 exit $exitCode ──

def test_wsl_script_uses_non_throwing_decoder_and_bounded_wait():
    body = _wsl_body(ws.wsl_command('true', 'Ubuntu', timeout=60)[1])
    assert 'UTF8Encoding($false, $true)' not in body
    assert 'UTF8Encoding($false, $false)' in body
    assert '$timeoutMs = 50000' in body                         # SSH 60s - 10s 余量
    assert '$process.WaitForExit($timeoutMs)' in body
    assert 'Stop-WslTree $process' in body and 'taskkill.exe /T /F' in body
    assert '$exitCode = 124' in body
    # 清理在 finally 里, 退出码在 finally 之后统一 exit
    assert body.index('Remove-Item -LiteralPath $job') < body.rindex('exit $exitCode')
    assert body.rstrip().endswith('exit $exitCode')
    # 输出解码失败也不丢退出码
    assert "try { [Console]::Out.Write((Read-WslText $outputFile)) } catch" in body


@pytest.mark.parametrize('timeout,expected', [(None, -1), (0, -1), (8, 5000), (900, 890000)])
def test_wsl_budget_from_ssh_timeout(timeout, expected):
    body = _wsl_body(ws.wsl_command('true', 'Ubuntu', timeout=timeout)[1])
    assert f'$timeoutMs = {expected}\n' in body


def test_handler_passes_timeout_to_wsl_carrier():
    with patch.object(remote, '_conn', return_value=(CONN, '')), \
         patch.object(remote, '_ssh', new=AsyncMock(return_value=(0, '', ''))) as ssh:
        asyncio.run(remote.h_ssh_exec('wsl', {'cmd': 'sleep 1', 'shell': 'wsl',
                                               'wsl_distro': 'Ubuntu', 'timeout': 300}))
    assert '$timeoutMs = 290000' in _wsl_body(ssh.call_args.kwargs['stdin_data'])
    assert ssh.call_args.args[5] == 300


# ── 5. 临时目录探测 30s 上限 + 命令预算从连接前起算 ──

def test_temp_probe_allows_slow_cold_vm():
    read = MagicMock(side_effect=[(0, 'C:\\Temp\\', ''), (0, 'ok', '')])
    with patch.object(remote, '_read_ssh_command', read):
        remote._run_powershell_file(MagicMock(), b'\xef\xbb\xbf# payload', 120)
    assert 29 < read.call_args_list[0].args[2] <= 30


def test_temp_probe_never_exceeds_remaining_budget():
    read = MagicMock(side_effect=[(0, 'C:\\Temp\\', ''), (0, 'ok', '')])
    with patch.object(remote, '_read_ssh_command', read):
        remote._run_powershell_file(MagicMock(), b'\xef\xbb\xbf# payload', 12)
    assert read.call_args_list[0].args[2] <= 12


def _slow_connect(seconds):
    clock = {'t': 1000.0}

    def connect(conn):
        clock['t'] += seconds
        return MagicMock(), None
    return clock, connect


def test_slow_connect_shrinks_command_budget_below_outer_deadline():
    clock, connect = _slow_connect(150)
    read = MagicMock(return_value=(0, 'ok', ''))
    with patch.object(remote, '_connect', side_effect=connect), \
         patch.object(remote, '_read_ssh_command', read), \
         patch.object(remote.time, 'monotonic', side_effect=lambda: clock['t']):
        remote._run_ssh('h', 22, 'u', '', 'cmd', 100)
    budget = read.call_args.args[2]
    assert budget < 100
    assert 150 + budget + 15 <= remote._hard_deadline(100)


def test_fast_connect_keeps_full_timeout():
    clock, connect = _slow_connect(2)
    read = MagicMock(return_value=(0, 'ok', ''))
    with patch.object(remote, '_connect', side_effect=connect), \
         patch.object(remote, '_read_ssh_command', read), \
         patch.object(remote.time, 'monotonic', side_effect=lambda: clock['t']):
        remote._run_ssh('h', 22, 'u', '', 'cmd', 100)
    assert read.call_args.args[2] == 100


def test_connect_that_ate_whole_budget_never_runs_command():
    clock, connect = _slow_connect(10_000)
    read = MagicMock()
    with patch.object(remote, '_connect', side_effect=connect), \
         patch.object(remote, '_read_ssh_command', read), \
         patch.object(remote.time, 'monotonic', side_effect=lambda: clock['t']), \
         pytest.raises(remote.HostNotReady, match='命令未执行'):
        remote._run_ssh('h', 22, 'u', '', 'cmd', 10)
    read.assert_not_called()


# ── 10. NativeCommandError 只在出错路径给提示 ──

@pytest.mark.parametrize('rc,stderr,shell,hinted', [
    (1, 'git : Cloning into x\n    + CategoryInfo : NotSpecified\n    + FullyQualifiedErrorId : NativeCommandError', 'powershell', True),
    (0, 'NativeCommandError', 'powershell', False),
    (1, 'Get-Service : Cannot find any service', 'powershell', False),
    (1, 'NativeCommandError', 'auto', False),
])
def test_native_command_error_hint(rc, stderr, shell, hinted):
    with patch.object(remote, '_conn', return_value=(CONN, '')), \
         patch.object(remote, '_ssh', new=AsyncMock(return_value=(rc, '', stderr))):
        text, _ = asyncio.run(remote.h_ssh_exec('ps', {'cmd': 'git clone x 2>&1', 'shell': shell}))
    assert ("$ErrorActionPreference='Continue'" in text) is hinted
    assert f'rc={rc}' in text


# ── 6. 每轮清理只作用于模型副本 ──

def test_sanitize_model_copy_keeps_shared_evidence():
    asst_none = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "a1", "function": {"name": "read_file", "arguments": {"path": "x"}}},
        {"id": "dangling", "function": {"name": "read_file", "arguments": "{bad json"}}]}
    tool = {"role": "tool", "tool_call_id": "a1", "content": "result"}
    plain = {"role": "user", "content": "hi"}
    msgs = [{"role": "system", "content": "s"}, plain, asst_none, tool]
    raw = json.loads(json.dumps(msgs))
    new_msgs = msgs[1:]

    model = loop_budget.sanitize_model_copy(msgs)

    assert new_msgs == raw[1:] and msgs == raw          # 原对象一字不改
    m_asst = model[2]
    assert m_asst is not asst_none and m_asst["content"] == ""
    assert [tc["id"] for tc in m_asst["tool_calls"]] == ["a1"]
    assert m_asst["tool_calls"][0]["function"]["arguments"] == '{"path": "x"}'
    assert model[1] is plain and model[3] is tool       # 不需改写的消息不复制


# ── 7. 回读闸 alias 归一化 ──

def _call(i, name, args):
    return {"id": f"c{i}", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


@pytest.fixture
def gate(monkeypatch):
    agent = dict(rb.CFG.get("agent") or {})
    agent["readback_gate"] = True
    monkeypatch.setitem(rb.CFG, "agent", agent)
    monkeypatch.setitem(subagent_operator._SID_ALIAS, "subagent_operator_t1", "gpu-vm")
    return SimpleNamespace(session_id="subagent_operator_t1", readback=None)


def test_operator_empty_alias_change_matches_readback_with_alias(gate):
    calls = [_call(1, "windows_desktop", {"action": "hotkey", "keys": "ctrl+s"}),
             _call(2, "windows_system", {"alias": " GPU-VM ", "action": "inspect_file", "path": "a.xlsx"})]
    results = [{"tool_call_id": "c1", "content": json.dumps({"ok": True, "action": "hotkey"})},
               {"tool_call_id": "c2", "content": json.dumps({"ok": True, "alias": "gpu-vm"})}]
    rb.observe(gate, calls, results)
    assert gate.readback.pending_change is False
    assert rb.check(gate) is None


def test_server_filled_alias_in_result_is_used(gate):
    gate.session_id = "main-session"   # 非操作员, 无分配电脑
    calls = [_call(1, "windows_desktop", {"action": "hotkey", "keys": "ctrl+s"}),
             _call(2, "windows_system", {"alias": "Office-PC", "action": "inspect_file", "path": "a"})]
    results = [{"tool_call_id": "c1", "content": json.dumps({"ok": True, "alias": "office-pc"})},
               {"tool_call_id": "c2", "content": json.dumps({"ok": True})}]
    rb.observe(gate, calls, results)
    assert gate.readback.pending_change is False


def test_readback_on_other_computer_still_gates(gate):
    calls = [_call(1, "windows_desktop", {"alias": "gpu-vm", "action": "hotkey", "keys": "ctrl+s"}),
             _call(2, "windows_system", {"alias": "office-pc", "action": "inspect_file", "path": "a"})]
    results = [{"tool_call_id": "c1", "content": json.dumps({"ok": True, "alias": "gpu-vm"})},
               {"tool_call_id": "c2", "content": json.dumps({"ok": True, "alias": "office-pc"})}]
    rb.observe(gate, calls, results)
    assert rb.check(gate) == rb.INJECT_TEXT


# ── 8. 封禁提示脱敏 ──

def test_loop_block_message_redacts_secrets():
    args = {"host": "10.0.0.1", "password": "hunter2", "cmd": "ls",
            "jump": {"host": "j", "key": "PRIVKEY", "user_token": "tok-123"},
            "headers": [{"Authorization": "Bearer abc"}], "api_key": "sk-live", "key_file": "/k/id_rsa"}
    shown = raw_display("ssh_exec", args)
    for secret in ("hunter2", "PRIVKEY", "tok-123", "Bearer abc", "sk-live"):
        assert secret not in shown
    assert "10.0.0.1" in shown and "/k/id_rsa" in shown and '"password": "***"' in shown
    g = ToolLoopGuard(limit=3)
    warns = [g.check("ssh_exec", args)[1] for _ in range(3)]
    assert warns[-1] and "hunter2" not in warns[-1] and "***" in warns[-1]
    # 判重签名仍按原参数: 换密码不是同一调用
    assert g.check("ssh_exec", dict(args, password="other"))[0] is False
