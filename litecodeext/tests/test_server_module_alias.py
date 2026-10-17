"""以脚本方式启动 litecode_server 时, 再 import litecode_server 必须拿到同一个模块 (不重复初始化)。

2026-10-16: gateway/_deps._srv() 每次 import litecode_server, 而服务以 `python3 litecode_server.py`
启动 (__main__), 于是得到第二份副本: 顶层初始化重跑, 模型热切换写到副本上, 主循环读不到。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_script_start_and_import_share_one_module():
    probe = (
        "import sys, runpy, uvicorn\n"
        "uvicorn.run = lambda *a, **k: None\n"          # 不真的起服务
        f"sys.path[:0] = [{str(ROOT)!r}, {str(ROOT / 'core')!r}]\n"
        f"g = runpy.run_path({str(ROOT / 'litecode_server.py')!r}, run_name='__main__')\n"
        "import litecode_server\n"
        "print('SAME' if litecode_server.agent_stream is g['agent_stream'] else 'COPY')\n"
    )
    out = subprocess.run([sys.executable, '-c', probe], capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert out.stdout.strip().splitlines()[-1] == 'SAME', out.stdout[-2000:] + out.stderr[-2000:]
