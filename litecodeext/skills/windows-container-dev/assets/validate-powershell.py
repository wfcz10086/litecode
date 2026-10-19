#!/usr/bin/env python3
"""仅由 LiteCode 执行：固定新 Windows 的长 PowerShell 文件投递验收。"""
import argparse
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    args = parser.parse_args()
    root = next(p for p in Path(__file__).resolve().parents if (p / 'core/tools/handlers/remote.py').is_file())
    sys.path[:0] = [str(root), str(root / 'core')]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from core.tools.handlers.remote import h_ssh_exec
        from lib.config import WORKSPACE
        from lib.windows_ssh import powershell_command

    alias, computer = 'win-devtest', 'WIN-EXAMPLE01'
    run_id = uuid.uuid4().hex
    report = {'session_id': args.session, 'alias': alias, 'expected_computer': computer,
              'run_id': run_id, 'started_at': time.time(), 'overall_pass': False}

    def execute(script):
        text, _ = asyncio.run(h_ssh_exec(args.session, {
            'alias': alias, 'shell': 'powershell', 'cmd': script, 'timeout': 45,
        }))
        match = re.search(r'^\[ssh .*\] rc=(-?\d+)$', text, re.M)
        stdout = text.split('--- stdout ---\n', 1)[1].split('\n--- stderr ---', 1)[0].strip() if '--- stdout ---\n' in text else ''
        return {'actual_exit': int(match.group(1)) if match else None, 'stdout': stdout, 'raw_result': text}

    snapshot = (
        "if ($env:COMPUTERNAME -ne '" + computer + "') { throw 'Wrong Windows target' }; "
        "$names=@(Get-ChildItem -LiteralPath ([IO.Path]::GetTempPath()) "
        "-Filter 'LiteCodeSSH-*.ps1' -File | Select-Object -ExpandProperty Name); "
        "[Console]::Out.Write((@{computer=$env:COMPUTERNAME;files=$names} | ConvertTo-Json -Compress))"
    )

    def parse_snapshot(result):
        data = json.loads(result['stdout'])
        if result['actual_exit'] != 0 or data.get('computer') != computer or not isinstance(data.get('files'), list):
            raise ValueError('Windows identity or temporary-file snapshot was not verified')
        return data

    try:
        report['before'] = execute(snapshot)
        before = parse_snapshot(report['before'])  # 身份不符时不投递长脚本。
        marker = 'LITECODE-POWERSHELL-NATIVE-' + run_id + ' 中文🙂 "$HOME" `n && | %'
        script = '# ' + 'harmless-comment ' * 1000 + "\n[Console]::Out.WriteLine('" + marker + "')\nexit 17\n"
        report['encoded_command_chars'] = len(powershell_command(script))
        if report['encoded_command_chars'] <= 8191:
            raise ValueError('Native probe must exceed the Windows command-line limit')
        report['expected_exit'] = 17
        report['expected_stdout'] = marker
        report['probe'] = execute(script)
        report['after'] = execute(snapshot)
        after = parse_snapshot(report['after'])
        report['new_residual_files'] = sorted(set(after['files']) - set(before['files']))
        report['overall_pass'] = (report['probe']['actual_exit'] == 17
                                  and report['probe']['stdout'] == marker
                                  and not report['new_residual_files'])
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    report['finished_at'] = time.time()
    out = Path(WORKSPACE) / 'uploads' / ('windows-powershell-native-' + run_id + '.json')
    out.parent.mkdir(parents=True, exist_ok=True)
    report['report_path'] = str(out)
    with os.fdopen(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['overall_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
