"""真实 Windows 服务缺失可选描述配置时，不应破坏 sshd 状态查询。"""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.windows_system_actions import services


class Service:
    def __init__(self, name, display, failure=None):
        self._name, self._display, self.failure = name, display, failure

    def name(self):
        return self._name

    def display_name(self):
        if self.failure:
            raise self.failure
        return self._display

    def status(self):
        return 'running'

    def start_type(self):
        return 'automatic'

    def as_dict(self):
        raise OSError(2, 'originated from QueryServiceConfig2W')


def test_ssh_service_read_does_not_query_optional_description():
    fake = SimpleNamespace(Error=RuntimeError, win_service_iter=lambda: iter([
        Service('other', 'Unrelated service'), Service('sshd', 'OpenSSH SSH Server')]))
    with patch.dict(sys.modules, {'psutil': fake}):
        result = services({'name': 'SSH'})
    assert result['services'] == [{'name': 'sshd', 'display_name': 'OpenSSH SSH Server',
                                   'status': 'running', 'start_type': 'automatic'}]
    assert result['unavailable_count'] == 0


def test_native_and_psutil_errors_preserve_remaining_services():
    class AccessDenied(Exception):
        pass
    fake = SimpleNamespace(Error=AccessDenied, win_service_iter=lambda: iter([
        Service('gone', '', OSError(2, 'service disappeared')),
        Service('protected', '', AccessDenied()), Service('sshd', 'OpenSSH SSH Server')]))
    with patch.dict(sys.modules, {'psutil': fake}):
        result = services({})
    assert result['unavailable_count'] == 2
    assert result['count'] == 1 and result['services'][0]['name'] == 'sshd'
