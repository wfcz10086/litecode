#!/usr/bin/env python3
"""LiteCode entry: deploy and invoke the guarded new-VM lifecycle probe over its saved SSH alias."""
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', required=True)
    p.add_argument('--mode', choices=['Status', 'Crash', 'PrepareReboot', 'StartOffline'], required=True)
    p.add_argument('--run', required=True)
    p.add_argument('--node-id', required=True)
    a = p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,96}', a.session) or not re.fullmatch(r'[A-Za-z0-9-]{1,48}', a.run) or not re.fullmatch(r'[a-f0-9]{32}', a.node_id):
        p.error('Invalid session, run or node id')
    root = next(x for x in Path(__file__).resolve().parents if (x / 'core/tools/handlers/remote.py').is_file())
    sys.path[:0] = [str(root), str(root / 'core')]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from core.tools.handlers.remote import h_ssh_exec, h_ssh_write_file
        from lib.config import WORKSPACE
    records = []

    def call(handler, args):
        result, _ = asyncio.run(handler(a.session, {'alias': 'win11-devtest', **args}))
        records.append({'tool': handler.__name__, 'raw_result': result})
        if result.startswith('ERROR'):
            raise RuntimeError(result)
        return result

    report = {'session': a.session, 'run': a.run, 'mode': a.mode, 'started_at': time.time(), 'ok': False}
    try:
        text = call(h_ssh_exec, {'shell': 'powershell', 'timeout': 30, 'cmd':
            "if($env:COMPUTERNAME -ne 'WIN-JM2PTI2AMKB'){throw 'Wrong target'}; "
            f"Get-ScheduledTask -TaskName 'LiteCode-Node-{a.node_id}' | Out-Null; "
            "$p=Join-Path $env:ProgramData 'LiteCodeLifecycle'; New-Item -ItemType Directory -Force $p | Out-Null; "
            "$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
            "& icacls.exe $p /inheritance:r /grant:r ('*'+$sid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null; "
            "if($LASTEXITCODE -ne 0){throw 'Probe directory ACL failed'}; Write-Output 'TARGET_GUARD_OK'"})
        if not re.search(r'\] rc=0\n', text) or 'TARGET_GUARD_OK' not in text:
            raise RuntimeError('Target guard did not complete')
        call(h_ssh_write_file, {'path': r'C:\ProgramData\LiteCodeLifecycle\probe.ps1', 'backup': False,
                               'content': '\ufeff' + Path(__file__).with_name('lifecycle-probe.ps1').read_text()})
        text = call(h_ssh_exec, {'shell': 'powershell', 'timeout': 45, 'cmd':
            "& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File 'C:\\ProgramData\\LiteCodeLifecycle\\probe.ps1' "
            f'-Mode {a.mode} -RunId {a.run} -NodeId {a.node_id}; exit $LASTEXITCODE'})
        if re.search(r'\] rc=0\n', text) and '...(truncated)' not in text:
            report['result'] = json.loads(text.split('--- stdout ---\n', 1)[1].split('\n--- stderr ---', 1)[0].strip().lstrip('\ufeff'))
            report['ok'] = True
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    report.update(records=records, finished_at=time.time())
    path = Path(WORKSPACE) / 'uploads' / f'windows-lifecycle-{a.run}-{a.mode}-{uuid.uuid4().hex[:8]}.json'
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({'report_path': str(path), **report}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
