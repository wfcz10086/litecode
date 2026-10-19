"""重现缺少完成标记的真实事故，并验证停机快照条件。"""
import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/windows-container-dev/assets/check_vm_recreate.py'
spec = importlib.util.spec_from_file_location('vm_recreate_guard', SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.fixture
def state(tmp_path):
    source, backup, proc = (tmp_path / name for name in ['storage', 'snapshot', 'proc'])
    for directory in [source, backup, proc]:
        directory.mkdir()
    for name, data in {'data.img': b'partition-and-windows', 'windows.vars': b'uefi-state',
                       'windows.base': b'windows.custom.iso', 'windows.ver': b'win11',
                       'windows.boot': b''}.items():
        (source / name).write_bytes(data)
        (backup / name).write_bytes(data)
    return source, backup, proc


def test_real_incident_existing_disk_without_completion_marker_is_blocked(state):
    source, backup, proc = state
    (source / 'windows.boot').unlink()
    result = guard.check_recreate_state(source, backup, proc)
    assert not result['ok'] and any('未完成安装' in e for e in result['errors'])
    assert (source / 'data.img').read_bytes() == b'partition-and-windows'


def test_completed_offline_vm_with_matching_snapshot_is_allowed(state):
    assert guard.check_recreate_state(*state)['ok']


def test_paused_vm_still_holds_disk_and_is_blocked(state):
    source, backup, proc = state
    descriptors = proc / '123' / 'fd'
    descriptors.mkdir(parents=True)
    (descriptors / '8').symlink_to(source / 'data.img')
    result = guard.check_recreate_state(source, backup, proc)
    assert not result['ok'] and any('123' in e for e in result['errors'])


@pytest.mark.parametrize('kind', ['hardlink', 'symlink', 'missing', 'wrong_content'])
def test_snapshot_must_be_independent_and_match_actual_disk(state, kind):
    source, backup, proc = state
    disk = backup / 'data.img'
    disk.unlink()
    if kind == 'hardlink':
        os.link(source / 'data.img', disk)
    elif kind == 'symlink':
        disk.symlink_to(source / 'data.img')
    elif kind == 'wrong_content':
        disk.write_bytes(b'x' * (source / 'data.img').stat().st_size)
    assert not guard.check_recreate_state(source, backup, proc)['ok']


def test_missing_nvram_snapshot_blocks_recreate(state):
    source, backup, proc = state
    (backup / 'windows.vars').unlink()
    assert not guard.check_recreate_state(source, backup, proc)['ok']


def test_nvram_hardlink_is_not_a_recoverable_snapshot(state):
    source, backup, proc = state
    (backup / 'windows.vars').unlink()
    os.link(source / 'windows.vars', backup / 'windows.vars')
    assert not guard.check_recreate_state(source, backup, proc)['ok']
