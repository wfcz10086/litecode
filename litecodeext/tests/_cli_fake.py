"""litecli 离线测试共用桩: 替换 lib.cli.common._req, 按 (method, path) 返回预置响应并记录调用。"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from click.testing import CliRunner  # noqa: E402

import litecli  # noqa: E402
from lib.cli import common  # noqa: E402


class Resp:
    def __init__(self, data, status=200):
        self.status_code = status
        self._data = data
        self.text = json.dumps(data, ensure_ascii=False)

    def json(self):
        return self._data


class Fake:
    """routes: {(METHOD, path): data | (status, data)}; 未配置的路由返回 404。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, path, json_body=None, params=None, timeout=30, stream=False):
        self.calls.append((method, path, json_body, params))
        hit = self.routes.get((method, path))
        if hit is None:
            return Resp({'detail': 'not found'}, 404)
        if isinstance(hit, tuple):
            return Resp(hit[1], hit[0])
        return Resp(hit)

    def methods(self):
        return [(m, p) for m, p, _, _ in self.calls]


@pytest.fixture
def fake(monkeypatch):
    def make(routes):
        f = Fake(routes)
        monkeypatch.setattr(common, '_req', f)
        return f
    os.environ.pop('LITECLI_OUTPUT', None)
    yield make
    os.environ.pop('LITECLI_OUTPUT', None)  # 根命令 --json 直接写 os.environ, 测完复位


def run(*args, input=None):
    os.environ.pop('LITECLI_OUTPUT', None)  # 真实 CLI 每次是新进程, 模拟时不让上一次 --json 残留
    return CliRunner().invoke(litecli.cli, list(args), input=input)
