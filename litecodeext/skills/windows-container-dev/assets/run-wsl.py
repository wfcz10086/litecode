#!/usr/bin/env python3
"""由 LiteCode 调用的 WSL 执行入口，复用正式 SSH handler 和已存别名。"""
import argparse
import asyncio
import contextlib
import io
from pathlib import Path
import re
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alias', required=True)
    parser.add_argument('--distro', required=True)
    parser.add_argument('--user', default='')
    parser.add_argument('--file', required=True, type=Path, help='控制端上的 UTF-8 Bash 文件')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--session', default='windows-container-dev')
    args = parser.parse_args()
    if not 1 <= args.timeout <= 1800:
        parser.error('timeout 必须介于 1 和 1800 秒之间')
    root = next((p for p in Path(__file__).resolve().parents if (p / 'core/tools/handlers/remote.py').is_file()), None)
    if root is None:
        parser.error('请从 LiteCode 安装目录中的技能调用此入口')
    sys.path[:0] = [str(root), str(root / 'core')]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from core.tools.handlers.remote import h_ssh_exec
    result, _ = asyncio.run(h_ssh_exec(args.session, {
        'alias': args.alias, 'shell': 'wsl', 'wsl_distro': args.distro,
        'wsl_user': args.user, 'cmd': args.file.read_text(encoding='utf-8-sig'), 'timeout': args.timeout,
    }))
    print(result)
    match = re.search(r'^\[ssh .*\] rc=(-?\d+)$', result, re.M)
    if not match:
        return 1
    code = int(match.group(1))
    return code if 0 <= code <= 255 else 1


if __name__ == '__main__':
    raise SystemExit(main())
