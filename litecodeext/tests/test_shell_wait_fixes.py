"""视频任务日志里的工具问题 (2026-10-19, 62 分钟里约 32 分钟在空等):
1. `until ! pgrep -f "run.py --from check"` 匹配到等待命令自己, 永远等到超时 → 自动改写成 [r]un.py
2. 前台 `sleep 45; tail` 没写 timeout, 默认 30s 把 sleep 杀了 → 按 sleep 总时长推超时
3. 后台任务结束没信号 → 日志末尾写 [BG-EXIT code=N], 起任务时回显等待命令
4. list_bg 把一小时前就跑完的进程照样列成"在跑" → 标已结束 + 退出码并移出登记
"""
import asyncio
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from core.executor_v4 import fix_self_match, infer_timeout  # noqa: E402
import tools.bg as BG  # noqa: E402


def test_self_match_rewrite_really_stops_self_match():
    cmd = 'pgrep -f "run.py --from check" >/dev/null && echo SELF || echo NONE'
    raw = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True).stdout.strip()
    assert raw == "SELF"                                   # 原写法: 没有 run.py 在跑也匹配到自己
    fixed, note = fix_self_match(cmd)
    assert '"[r]un.py --from check"' in fixed and note
    out = subprocess.run(["bash", "-c", fixed], capture_output=True, text=True).stdout.strip()
    assert out == "NONE"


def test_self_match_variants():
    assert fix_self_match("pgrep -fa 'render.py|run.py'")[0] == "pgrep -fa '[r]ender.py|[r]un.py'"
    assert fix_self_match("pkill -f serve.py")[0] == "pkill -f '[s]erve.py'"
    for keep in ('pgrep -f "[r]un.py"', "pgrep python3", "ps aux | grep -v grep"):
        assert fix_self_match(keep) == (keep, "")


def test_sleep_aware_timeout():
    assert infer_timeout("sleep 45; tail -25 run.log") == 75
    assert infer_timeout("sleep 700") == 600
    assert infer_timeout("ls") == 30
    assert infer_timeout("sleep 170 && tail x", 200) == 200   # 显式 timeout 不动


def test_bg_exit_marker_and_wait_hint(tmp_path):
    log = tmp_path / "bg.log"
    wrapped = BG._as_bash("echo hi; exit_with() { return 3; }; exit_with &", mark_exit=True)
    subprocess.run(f"{wrapped} > {log} 2>&1", shell=True)
    assert BG.bg_exit_code(log) == 3
    assert "hi" in log.read_text()
    log2 = tmp_path / "ok.log"
    subprocess.run(f"{BG._as_bash('true', mark_exit=True)} > {log2} 2>&1", shell=True)
    assert BG.bg_exit_code(log2) == 0
    assert BG.bg_exit_code(tmp_path / "missing.log") is None
    assert "BG-EXIT" in BG.wait_hint(log) and "pgrep" in BG.wait_hint(log)
    assert BG._as_bash("echo x") == "bash -c 'echo x'"      # 默认不加标记 (keep_alive / health_url 服务)


def test_list_bg_marks_finished(tmp_path, monkeypatch):
    from core.tools.handlers import bg_ops
    log = tmp_path / "done.log"
    log.write_text("render ok\n[BG-EXIT code=0]\n")
    p = subprocess.Popen(["true"])
    p.wait()
    sid = "test-listbg"
    monkeypatch.setattr(BG, "_persist_bg_state", lambda: None)     # 别写真工作区的 .bg_procs.json
    BG._register_bg(str(p.pid), "python3 run.py", log, sid=sid)
    out, _ = asyncio.run(bg_ops.h_list_bg(sid, {}))
    assert "已结束 exit=0" in out
    assert str(p.pid) not in BG.list_bg_for_session(sid)     # 已移出登记


def test_probe_uses_real_port_and_short_negative_cache(monkeypatch):
    """原来一律 curl https://host (443): http://x:54321 能通也判不可达, 还永久缓存"""
    import socket
    import core.executor_v4 as E
    from core.executor_v4 import extract_host, extract_scheme, probe_host
    cmd = "curl -s http://your-host-a.example:54321/api/images/657"
    assert extract_host(cmd) == "your-host-a.example:54321" and extract_scheme(cmd) == "http"
    seen = []

    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_conn(addr, timeout=3):
        seen.append(addr)
        if addr[1] == 54321:
            return _C()
        raise OSError("refused")
    monkeypatch.setattr(socket, "create_connection", fake_conn)
    E._NET_PROBE_CACHE.clear()
    assert probe_host("your-host-a.example:54321", scheme="http") is True
    assert seen[-1] == ("your-host-a.example", 54321)
    assert probe_host("example.org", scheme="http") is False and seen[-1] == ("example.org", 80)
    t = E._NET_PROBE_CACHE["example.org:80"][1]
    E._NET_PROBE_CACHE["example.org:80"] = (False, t - 31)             # 失败缓存 30s 后重新探
    probe_host("example.org", scheme="http")
    assert len([a for a in seen if a == ("example.org", 80)]) == 2
