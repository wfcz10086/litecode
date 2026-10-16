"""单行接入命令的编码、公钥限制与既有凭据保护。"""
import base64
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
from lib.windows_onboarding import build_command, prepare


def test_command_is_one_line_and_contains_only_public_key():
    command = build_command('param($PublicKey,$RemoteAddress)\nWrite-Output $PublicKey',
                            'ssh-ed25519 AAAA test', "192.168.1.1'quoted")
    assert '\n' not in command
    body = base64.b64decode(command.split()[-1]).decode('utf-16le')
    assert '-PublicKey \'ssh-ed25519 AAAA test\'' in body
    assert "'192.168.1.1''quoted'" in body
    with pytest.raises(ValueError):
        build_command('x', '-----BEGIN OPENSSH PRIVATE KEY-----')


def test_existing_alias_is_never_overwritten(tmp_path):
    with patch('core.tools.handlers.ssh_hosts._load', return_value={'win11': {}}), \
         patch('lib.windows_onboarding.subprocess.run') as run:
        with pytest.raises(ValueError, match='别名已存在'):
            prepare('win11', 'host', 'u', tmp_path, tmp_path / 'script')
        run.assert_not_called()
