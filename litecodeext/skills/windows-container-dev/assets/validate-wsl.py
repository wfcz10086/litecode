#!/usr/bin/env python3
"""供 LiteCode 发起 WSL 执行入口的无副作用真机验收。"""
import argparse
import asyncio
import contextlib
import io
import json
from pathlib import Path
import re
import shlex
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alias', required=True)
    parser.add_argument('--distro', required=True)
    parser.add_argument('--expected-computer', required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    root = next(p for p in Path(__file__).resolve().parents if (p / 'core/tools/handlers/remote.py').is_file())
    sys.path[:0] = [str(root), str(root / 'core')]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from core.tools.handlers.remote import h_ssh_exec

    def execute(values):
        text, _ = asyncio.run(h_ssh_exec(args.session, {'alias': args.alias, 'timeout': 45, **values}))
        match = re.search(r'^\[ssh .*\] rc=(-?\d+)$', text, re.M)
        return int(match.group(1)) if match else None, text

    hostname = args.expected_computer.replace("'", "''")
    report = {'session_id': args.session, 'alias': args.alias, 'distro': args.distro,
              'expected_computer': args.expected_computer, 'started_at': time.time(),
              'entrypoint': 'windows-container-dev.validate-wsl', 'cases': [], 'overall_pass': False}

    def save():
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.out.with_name(args.out.name + '.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(args.out)

    code, result = execute({'shell': 'powershell', 'cmd':
        "if ($env:COMPUTERNAME -ne '" + hostname + "') { throw 'Wrong Windows target' }; "
        "[Console]::Write('LITECODE-WINDOWS-IDENTITY:'+$env:COMPUTERNAME)"})
    report['identity'] = {'passed': code == 0 and 'LITECODE-WINDOWS-IDENTITY:' + args.expected_computer in result,
                          'result': result}
    save()
    if not report['identity']['passed']:
        print(json.dumps(report, ensure_ascii=False))
        return 1
    value = '中文🙂 "$HOME" `n && | %'
    cases = [
        ('unicode', "\ufeffprintf '%s\\n' " + shlex.quote(value) + '\r\n', 0, value, ''),
        ('stderr_warning', "printf 'WSL-WARNING-STDOUT\\n'; printf 'WSL-EXPECTED-WARNING\\n' >&2", 0, 'WSL-WARNING-STDOUT', 'WSL-EXPECTED-WARNING'),
        ('expected_failure', "printf 'WSL-FAILURE-STDOUT\\n'; printf 'WSL-EXPECTED-FAILURE\\n' >&2; exit 17", 17, 'WSL-FAILURE-STDOUT', 'WSL-EXPECTED-FAILURE'),
        ('long_script', '#' + 'x' * 100_000 + "\nprintf 'WSL-LONG-SCRIPT-OK\\n'", 0, 'WSL-LONG-SCRIPT-OK', ''),
        ('stdin_eof', "if read -r unexpected; then exit 9; fi\nprintf 'WSL-STDIN-EOF-OK\\n'", 0, 'WSL-STDIN-EOF-OK', ''),
    ]
    for name, script, expected, stdout_marker, stderr_marker in cases:
        code, result = execute({'shell': 'wsl', 'wsl_distro': args.distro, 'cmd': script})
        stdout = result.split('--- stdout ---\n', 1)[-1].split('\n--- stderr ---', 1)[0] if '--- stdout ---\n' in result else ''
        stderr = result.split('--- stderr ---\n', 1)[-1] if '--- stderr ---\n' in result else ''
        passed = code == expected and stdout_marker in stdout and (not stderr_marker or stderr_marker in stderr)
        report['cases'].append({'name': name, 'expected_exit': expected, 'actual_exit': code,
                                'passed': passed, 'result': result})
        save()
        print(json.dumps({'case': name, 'passed': passed, 'actual_exit': code}), flush=True)
    report.update(overall_pass=all(c['passed'] for c in report['cases']), finished_at=time.time())
    save()
    print(json.dumps({'overall_pass': report['overall_pass'], 'report': str(args.out)}, ensure_ascii=False))
    return 0 if report['overall_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
