#!/usr/bin/env python3
"""只读检查 dockurr v6.06 已有系统盘的重建前提，不执行 Docker 操作。"""
import argparse
import json
import os
from pathlib import Path


def _same_contents(left, right):
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open('rb') as a, right.open('rb') as b:
        while True:
            chunk = a.read(8 * 1024 * 1024)
            if chunk != b.read(len(chunk)):
                return False
            if not chunk:
                return True


def _open_disk_processes(disk, proc_root):
    expected = disk.stat()
    users = set()
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            for descriptor in (process / 'fd').iterdir():
                try:
                    current = descriptor.stat()
                except FileNotFoundError:
                    continue
                if (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino):
                    users.add(process.name)
        except FileNotFoundError:
            continue  # 进程已退出
    return sorted(users)


def check_recreate_state(storage, snapshot, proc_root=Path('/proc')):
    storage, snapshot = Path(storage), Path(snapshot)
    errors = []
    disk = storage / 'data.img'
    if disk.is_symlink() or not disk.is_file() or disk.stat().st_size == 0:
        return {'ok': False, 'errors': ['未找到受支持的非空 data.img，拒绝按已有系统重建']}
    if not (storage / 'windows.boot').is_file():
        errors.append('系统盘存在但缺少 windows.boot；镜像可能将其当作未完成安装清理')
    try:
        users = _open_disk_processes(disk, Path(proc_root))
        if users:
            errors.append('系统盘仍被进程占用，必须正常关机后制作快照；PID=' + ','.join(users))
    except OSError:
        errors.append('无法完整检查 /proc，不能确认系统盘已停止使用')
    saved = snapshot / 'data.img'
    if saved.is_symlink() or not saved.is_file() or snapshot.resolve() == storage.resolve():
        errors.append('缺少独立的停机系统盘快照')
    elif (saved.stat().st_dev, saved.stat().st_ino) == (disk.stat().st_dev, disk.stat().st_ino):
        errors.append('快照与原盘是同一文件或硬链接，不能回退')
    if errors:
        return {'ok': False, 'errors': errors}
    for name in ['data.img', 'windows.vars', 'windows.base', 'windows.ver', 'windows.boot']:
        original, backup = storage / name, snapshot / name
        if not original.is_file():
            errors.append('缺少已有系统状态文件：' + name)
        elif backup.is_symlink() or not backup.is_file():
            errors.append('快照缺少状态文件：' + name)
        elif (original.stat().st_dev, original.stat().st_ino) == (backup.stat().st_dev, backup.stat().st_ino):
            errors.append('快照状态文件与原文件是硬链接：' + name)
        elif not _same_contents(original, backup):
            errors.append('快照内容与停机源文件不一致：' + name)
    try:
        if _open_disk_processes(disk, Path(proc_root)):
            errors.append('验证过程中系统盘重新被进程打开')
    except OSError:
        errors.append('验证结束时无法确认系统盘已停止使用')
    return {'ok': not errors, 'errors': errors,
            'scope': '仅验证文件和离线状态，不执行重建，也不保证随后操作的原子性'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage', required=True, type=Path)
    parser.add_argument('--snapshot', required=True, type=Path)
    args = parser.parse_args()
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        result = {'ok': False, 'errors': ['在 Linux 宿主用已有管理员权限运行，才能完整检查磁盘占用']}
    else:
        try:
            result = check_recreate_state(args.storage, args.snapshot)
        except OSError as error:
            result = {'ok': False, 'errors': ['检查无法完成：' + str(error)]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
