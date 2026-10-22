"""P0-5 / P0-6: 桌面版升级回滚脚本、只监听本机、发布前测试门禁。

- litecode-apply-update / litecode-rollback 用 LITECODE_ROOT 把路径挪到临时目录真跑, 覆盖成功/包无效/切换失败/回滚失败
- 桌面版服务只监听 127.0.0.1 (LITECODE_BIND_HOST), 服务器版默认不变
- scripts/run_release_tests.sh 以退出码判定: 失败、超时 (124)、没收集到用例都必须非 0
"""
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WSL = ROOT / "packaging" / "windows" / "wsl"
APPLY = WSL / "litecode-apply-update"
ROLLBACK = WSL / "litecode-rollback"
sys.path.insert(0, str(ROOT / "litecodeext"))

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")


# ── 测试夹具: 假的子系统根目录 ──

def _fake_root(tmp_path, ver="1.0"):
    r = tmp_path / "root"
    app = r / "opt" / "litecode"
    (app / "skills" / "drafts").mkdir(parents=True)
    (app / "web_ui.py").write_text("old")
    (app / "litecode_server.py").write_text("old")
    (app / "config.json").write_text('{"user": "kept"}')
    (app / "skills" / "drafts" / "mine.md").write_text("draft")
    (r / "etc").mkdir()
    (r / "etc" / "litecode-version").write_text(ver + "\n")
    (r / "entrypoint.sh").write_text("#!/bin/bash\necho old\n")
    return r


def _pkg(tmp_path, complete=True, name="pkg.tar.gz"):
    src = tmp_path / ("src_" + name)
    (src / "litecode").mkdir(parents=True)
    (src / "litecode" / "web_ui.py").write_text("new")
    if complete:
        (src / "litecode" / "litecode_server.py").write_text("new")
    (src / "litecode" / "config.json").write_text('{"default": true}')
    (src / "entrypoint.sh").write_text("#!/bin/bash\necho new\n")
    out = tmp_path / name
    with tarfile.open(out, "w:gz") as t:
        t.add(src / "litecode", arcname="litecode")
        t.add(src / "entrypoint.sh", arcname="entrypoint.sh")
    return out


def _run(script, root, *args):
    env = dict(os.environ, LITECODE_ROOT=str(root))
    return subprocess.run(["bash", str(script), *map(str, args)], env=env, capture_output=True, text=True, timeout=60)


def _ver(root):
    return (root / "etc" / "litecode-version").read_text().strip()


# ── litecode-apply-update ──

def test_apply_update_success_keeps_config_and_prev(tmp_path):
    r = _fake_root(tmp_path)
    p = _run(APPLY, r, _pkg(tmp_path), "2.0")
    assert p.returncode == 0, p.stderr
    assert "UPDATED 2.0" in p.stdout
    app = r / "opt" / "litecode"
    assert (app / "web_ui.py").read_text() == "new"
    assert (app / "config.json").read_text() == '{"user": "kept"}'
    assert (app / "skills" / "drafts" / "mine.md").read_text() == "draft"
    assert _ver(r) == "2.0"
    assert (r / "opt" / "litecode.prev" / "web_ui.py").read_text() == "old"
    assert (r / "etc" / "litecode-version.prev").read_text().strip() == "1.0"
    assert "new" in (r / "entrypoint.sh").read_text()
    assert not (r / "opt" / "litecode.new").exists()


def test_apply_update_invalid_package_exit2_untouched(tmp_path):
    r = _fake_root(tmp_path)
    p = _run(APPLY, r, _pkg(tmp_path, complete=False), "2.0")
    assert p.returncode == 2
    assert "代码包不完整" in p.stderr
    assert (r / "opt" / "litecode" / "web_ui.py").read_text() == "old" and _ver(r) == "1.0"
    bad = tmp_path / "corrupt.tar.gz"
    bad.write_bytes(b"not a tarball")
    p = _run(APPLY, r, bad, "2.0")
    assert p.returncode == 2 and _ver(r) == "1.0"


def test_apply_update_switch_failure_restores_old_version(tmp_path):
    r = _fake_root(tmp_path)
    # 写版本号的临时文件位置被目录占住 → 切换最后一步失败, 必须恢复原版本并以 3 退出
    (r / "etc" / "litecode-version.tmp").mkdir()
    p = _run(APPLY, r, _pkg(tmp_path), "2.0")
    assert p.returncode == 3, (p.stdout, p.stderr)
    assert "APPLY_FAILED_RESTORED" in p.stderr
    assert (r / "opt" / "litecode" / "web_ui.py").read_text() == "old"
    assert "old" in (r / "entrypoint.sh").read_text()
    assert _ver(r) == "1.0"


def test_apply_then_rollback(tmp_path):
    r = _fake_root(tmp_path)
    assert _run(APPLY, r, _pkg(tmp_path), "2.0").returncode == 0
    (r / "opt" / "litecode" / "config.json").write_text('{"changed": "after update"}')
    p = _run(ROLLBACK, r)
    assert p.returncode == 0, p.stdout
    assert "ROLLED_BACK 1.0" in p.stdout
    app = r / "opt" / "litecode"
    assert (app / "web_ui.py").read_text() == "old"
    assert (app / "config.json").read_text() == '{"changed": "after update"}'
    assert "old" in (r / "entrypoint.sh").read_text()
    assert not (r / "opt" / "litecode.prev").exists()


def test_rollback_without_prev_reports_and_changes_nothing(tmp_path):
    r = _fake_root(tmp_path)
    p = _run(ROLLBACK, r)
    assert p.returncode == 1
    assert "ROLLBACK_FAILED" in p.stdout
    assert (r / "opt" / "litecode" / "web_ui.py").read_text() == "old"


@pytest.mark.skipif(os.geteuid() == 0, reason="root 不受目录权限限制")
def test_rollback_failure_reports_nonzero(tmp_path):
    r = _fake_root(tmp_path)
    assert _run(APPLY, r, _pkg(tmp_path), "2.0").returncode == 0
    opt = r / "opt"
    opt.chmod(0o555)   # 不能改名 → 回滚失败, 必须非 0 并说明
    try:
        p = _run(ROLLBACK, r)
    finally:
        opt.chmod(0o755)
    assert p.returncode == 4
    assert "ROLLBACK_FAILED" in p.stdout
    assert (r / "opt" / "litecode" / "web_ui.py").read_text() == "new"


def test_go_exit_codes_match_scripts():
    """托盘 (upgrade 包) 按退出码判定状态, 两边约定必须一致。"""
    go = (ROOT / "packaging" / "windows" / "upgrade" / "codeupdate.go").read_text()
    text = APPLY.read_text()
    for name, code in (("ApplyUnchanged", 2), ("ApplyRestored", 3), ("ApplyNotRestored", 4)):
        assert re.search(rf"{name}\s*=\s*{code}\b", go)
        assert re.search(rf"^#\s+{code}\s", text, re.M)
    wsl_go = (ROOT / "packaging" / "windows" / "wsl.go").read_text()
    assert re.search(r"exitAlreadyRunning\s*=\s*75\b", wsl_go)
    assert "exit 75" in (WSL / "litecode-wsl-start").read_text()


# ── 桌面版只监听 127.0.0.1 ──

def test_bind_host_env_overrides(monkeypatch):
    from lib.bind_host import bind_host
    monkeypatch.delenv("LITECODE_BIND_HOST", raising=False)
    assert bind_host() == "0.0.0.0"
    assert bind_host("10.0.0.5") == "10.0.0.5"
    monkeypatch.setenv("LITECODE_BIND_HOST", "127.0.0.1")
    assert bind_host() == "127.0.0.1"
    assert bind_host("0.0.0.0") == "127.0.0.1"
    monkeypatch.setenv("LITECODE_BIND_HOST", "  ")
    assert bind_host() == "0.0.0.0"


def test_servers_use_bind_host_and_desktop_sets_loopback():
    web = (ROOT / "litecodeext" / "web_ui.py").read_text()
    srv = (ROOT / "litecodeext" / "litecode_server.py").read_text()
    assert 'uvicorn.run(app, host="0.0.0.0"' not in web
    assert "bind_host()" in web and "bind_host(HOST)" in srv
    start = (WSL / "litecode-wsl-start").read_text()
    assert "LITECODE_BIND_HOST=${LITECODE_BIND_HOST:-127.0.0.1}" in start
    # 服务器版 docker 部署不设该变量 (行为不变)
    assert "LITECODE_BIND_HOST" not in (ROOT / "docker-compose.yml").read_text()
    assert "LITECODE_BIND_HOST=127.0.0.1" in (ROOT / "packaging" / "windows" / "wsl.go").read_text()


# ── P0-6 发布前测试门禁 ──

RELEASE_TESTS = ROOT / "scripts" / "run_release_tests.sh"


def _gate_root(tmp_path, test_body):
    r = tmp_path / "repo"
    (r / "litecodeext" / "tests").mkdir(parents=True)
    (r / "litecodeext" / "tests" / "test_x.py").write_text(test_body)
    (r / "tests").mkdir()
    (r / "tests" / "smoke.sh").write_text("exit 0\n")
    return r


def _gate(root, **env):
    e = dict(os.environ, PATH="/usr/bin:/bin", **env)   # 不含 go: 跳过托盘程序检查 (假仓库里也没有)
    e["PATH"] = os.path.dirname(sys.executable) + ":" + e["PATH"]
    return subprocess.run(["bash", str(RELEASE_TESTS), str(root)], env=e, capture_output=True, text=True, timeout=120)


def test_release_gate_passes_on_green(tmp_path):
    p = _gate(_gate_root(tmp_path, "def test_ok():\n    assert True\n"))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "全部测试通过" in p.stdout


def test_release_gate_fails_on_failed_test(tmp_path):
    p = _gate(_gate_root(tmp_path, "def test_bad():\n    assert False\n"))
    assert p.returncode == 1
    assert "litecodeext pytest" in p.stdout and "rc=1" in p.stdout


def test_release_gate_fails_on_timeout(tmp_path):
    p = _gate(_gate_root(tmp_path, "import time\ndef test_slow():\n    time.sleep(30)\n"), RELEASE_PYTEST_TIMEOUT="3")
    assert p.returncode == 1
    assert "超时" in p.stdout and "rc=124" in p.stdout


def test_release_gate_fails_when_nothing_collected(tmp_path):
    p = _gate(_gate_root(tmp_path, "x = 1\n"))
    assert p.returncode == 1 and "rc=5" in p.stdout


def test_release_gate_fails_on_smoke_failure(tmp_path):
    r = _gate_root(tmp_path, "def test_ok():\n    assert True\n")
    (r / "tests" / "smoke.sh").write_text("echo boom; exit 1\n")
    p = _gate(r)
    assert p.returncode == 1 and "tests/smoke.sh" in p.stdout


def test_release_sh_runs_gate_after_preflight():
    text = (ROOT / "packaging" / "release.sh").read_text()
    pre = text.index('step "0. 预检')
    only = text.index('PREFLIGHT_ONLY')
    gate = text.index("run_release_tests.sh\" \"$ROOT\"")
    first = text.index('step "1. ')
    assert pre < only < gate < first
    assert "--skip-tests" in text and "警告" in text
    assert re.search(r'run_release_tests\.sh" "\$ROOT" \|\| \{[^}]*exit 1', text)


def test_smoke_check13_uses_exit_code():
    text = (ROOT / "tests" / "smoke.sh").read_text()
    block = text[text.index("# ── 13."):text.index("# ── summary")]
    assert "rc=\\$?" in block and "timeout" in block and "124" in block
    assert "2>/dev/null \\\n    | grep" not in block
