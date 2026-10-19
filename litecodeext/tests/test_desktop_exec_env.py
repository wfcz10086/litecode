"""desktop_exec 后台模式的显示环境前缀不能吃掉命令开头的 shell 变量赋值。
2026-10-19: `env DISPLAY=:99 ... B=url; for ...` 让 $B 为空, 生图轮询空转 17 分钟。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import tools.bg as BG  # noqa: E402

SRC = (ROOT / "core" / "tool_dispatch.py").read_text(encoding="utf-8")


def _prefix():
    m = re.search(r'cmd_with_display = f"(.+?)\{cmd\}"', SRC)
    assert m
    return m.group(1).encode().decode("unicode_escape")


def test_leading_assignment_survives_display_prefix():
    cmd = 'B=http://example.invalid; X=1; echo "B=$B X=$X DISPLAY=$DISPLAY"'
    out = subprocess.run(BG._as_bash(_prefix() + cmd), shell=True, capture_output=True, text=True).stdout
    assert "B=http://example.invalid X=1 DISPLAY=:99" in out


def test_old_env_prefix_really_broke_it():
    out = subprocess.run(BG._as_bash('env DISPLAY=:99 B=x; echo "B=[$B]"'), shell=True,
                         capture_output=True, text=True).stdout
    assert "B=[]" in out


def test_desktop_control_bg_keeps_shell_vars(tmp_path, monkeypatch):
    """lib/desktop_control.exec(bg=True) 原来用 list2cmdline (Windows 引号) 包命令, $B 被外层 sh 提前展开成空"""
    import time
    from lib import desktop_control as dc
    out = tmp_path / "o.txt"
    r = dc.exec(f'B=hello; echo "[$B]" > {out}', bg=True)
    assert r["ok"]
    for _ in range(50):
        if out.exists() and out.read_text().strip():
            break
        time.sleep(0.1)
    assert out.read_text().strip() == "[hello]"
