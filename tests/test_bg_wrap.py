"""core/tools/bg._as_bash: 后台命令统一交给 bash -c, 剥掉调用方会重复加的 nohup / 末尾 &。"""
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "litecodeext"), str(ROOT / "litecodeext" / "core")]

from core.tools.bg import _as_bash  # noqa: E402


def _inner(wrapped):
    parts = shlex.split(wrapped)
    assert parts[:2] == ["bash", "-c"] and len(parts) == 3
    return parts[2]


def test_compound_command_kept_whole():
    assert _inner(_as_bash("cd /tmp && python3 -m http.server 8000")) == "cd /tmp && python3 -m http.server 8000"


def test_strips_trailing_ampersand_and_nohup():
    assert _inner(_as_bash("nohup python3 app.py &")) == "python3 app.py"
    assert _inner(_as_bash("python3 app.py & ")) == "python3 app.py"


def test_keeps_trailing_and_and():
    assert _inner(_as_bash("make &&")) == "make &&"


def test_quotes_survive_shell_roundtrip():
    cmd = """cd /tmp && echo "it's ok" > /dev/null && printf '%s' "a b\""""
    out = subprocess.run(_as_bash(cmd), shell=True, capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout == "a b"
