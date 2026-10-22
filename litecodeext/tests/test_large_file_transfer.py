"""大文件传输回归 (2026-10-21): Web 上传流式落盘与上限 / 下载 Range / 文本预览只读头 /
SFTP 原子写 + sha256 校验 + 断点续传 / CLI 下载流式 + .part。

不连任何服务: FastAPI TestClient + 临时目录; SFTP 用本地目录模拟 (语义同 OpenSSH: rename 不覆盖)。
"""
import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "core"), str(Path(__file__).resolve().parent)]

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from lib import sftp_transfer as st  # noqa: E402


# ───────────────────────── Web 上传 / 下载 / 预览 ─────────────────────────
@pytest.fixture
def web(tmp_path, monkeypatch):
    from plugins.auth import core as auth
    from routers import upload_router, workspace_router
    from routers.state import S
    import lib.rate_limit as rl
    monkeypatch.setattr(auth, "_AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "_valid_tokens", {"goodtok": time.time() + 3600})
    monkeypatch.setattr(rl, "rate_check", lambda *a, **k: None)
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(S, "workspace", ws, raising=False)
    monkeypatch.setattr(S, "cfg", {"web_ui": {"max_upload_mb": 1}}, raising=False)
    monkeypatch.setattr(workspace_router, "_WX_SAFE_DIRS", [ws])
    app = FastAPI()
    app.include_router(upload_router.router)
    app.include_router(workspace_router.router)
    return TestClient(app, cookies={"lc_auth": "goodtok"}), ws


def test_upload_streams_to_disk_with_sha256(web):
    c, ws = web
    data = os.urandom(700 * 1024)
    r = c.post("/api/upload/s1", files={"file": ("../../etc/报告.bin", data)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] and d["name"] == "报告.bin" and d["size"] == len(data)
    assert d["sha256"] == hashlib.sha256(data).hexdigest()
    assert Path(d["path"]).read_bytes() == data and Path(d["path"]).parent == ws / "uploads"
    r2 = c.post("/api/upload/s1", files={"file": ("报告.bin", b"x")})       # 同名不覆盖
    assert r2.json()["name"] != "报告.bin" and Path(d["path"]).read_bytes() == data
    assert not list((ws / "uploads").glob(".upload-*"))


def test_upload_over_limit_by_content_length_rejected_early(web):
    c, ws = web
    r = c.post("/api/upload/s1", files={"file": ("big.bin", b"\0" * (1024 * 1024 + 100 * 1024))})
    assert r.status_code == 413
    d = r.json()
    assert "上限 1MB" in d["error"] and d["detail"] == d["error"]          # 前端读 error, CLI 读 detail
    assert not any((ws / "uploads").iterdir())


def test_upload_over_limit_chunked_aborts_and_cleans(web):
    c, ws = web
    boundary = "xyzBOUND"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"c.bin\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode()

    def gen():                                   # 没有 Content-Length, 边收边计数
        yield head
        for _ in range(40):
            yield b"\1" * 65536                  # 2.5MB > 1MB
        yield f"\r\n--{boundary}--\r\n".encode()

    r = c.post("/api/upload/s1", content=gen(),
               headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert r.status_code == 413 and "上限" in r.json()["error"]
    assert not any((ws / "uploads").iterdir())   # .part 半成品已删


def test_upload_rejects_non_multipart_and_missing_file(web):
    c, _ = web
    assert c.post("/api/upload/s1", content=b"abc", headers={"Content-Type": "text/plain"}).status_code == 400
    r = c.post("/api/upload/s1", data={"note": "x"}, files={"other": (None, "v")})
    assert r.status_code == 400 and "file" in r.json()["error"]


def test_upload_limits_endpoint_and_health_default(web):
    c, _ = web
    assert c.get("/api/upload_limits").json() == {"max_upload_mb": 1, "max_upload_bytes": 1048576}
    from routers.upload_router import max_upload_mb, safe_filename
    assert max_upload_mb({}) == 1024 and max_upload_mb({"web_ui": {"max_upload_mb": "x"}}) == 1024
    assert safe_filename("..") == "unnamed" and safe_filename("a\\b\\c.txt") == "c.txt"
    long = safe_filename("长" * 200 + ".tar.gz")
    assert long.endswith(".gz") and len(long.encode()) <= 200


def test_download_and_media_support_range(web):
    c, ws = web
    blob = bytes(range(256)) * 4096              # 1MB
    (ws / "d.bin").write_bytes(blob)
    r = c.get("/api/workspace/download?path=d.bin", headers={"Range": "bytes=1000-1999"})
    assert r.status_code == 206 and r.content == blob[1000:2000]
    assert r.headers["content-range"] == f"bytes 1000-1999/{len(blob)}" and r.headers["accept-ranges"] == "bytes"
    r = c.get(f"/api/media?path={ws / 'd.bin'}", headers={"Range": "bytes=-10"})
    assert r.status_code == 206 and r.content == blob[-10:]
    r = c.get("/api/workspace/download?path=d.bin")
    assert r.status_code == 200 and r.content == blob


def test_text_preview_reads_head_only(web, monkeypatch):
    c, ws = web
    p = ws / "huge.log"
    with open(p, "wb") as f:
        for _ in range(12):
            f.write(("中文日志行\n" * 60000).encode())     # ≈12MB > 旧 /api/preview 10MB 门槛
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("整读")))
    d = c.get("/api/workspace/preview?path=huge.log").json()
    assert d["type"] == "text" and d["truncated"] and len(d["content"]) < 200_000 and "仅预览前 512KB" in d["content"]
    assert "�" not in d["content"].split("\n\n... [")[0]
    d = c.get(f"/api/preview?path={p}").json()
    assert d["type"] == "text" and d["truncated"]


# ───────────────────────── SFTP: 原子写 / 校验 / 续传 ─────────────────────────
class LocalSftp:
    """用本地目录模拟 SFTP; rename 目标存在即失败 (同 OpenSSH SFTPv3), posix_rename 覆盖。"""

    def __init__(self, root: Path, corrupt: bool = False, fail_after: int | None = None):
        self.root, self.corrupt, self.fail_after = root, corrupt, fail_after

    def _p(self, p):
        return str(self.root / p.lstrip("/"))

    def stat(self, p):
        s = os.stat(self._p(p))
        return SimpleNamespace(st_size=s.st_size, st_mode=s.st_mode, st_uid=None, st_gid=None)

    def open(self, p, mode):
        f = open(self._p(p), mode)
        if "r" in mode or (not self.corrupt and self.fail_after is None):
            return f
        sftp = self

        class W:
            n = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                f.close()

            def write(self, b):
                if sftp.fail_after is not None and W.n + len(b) > sftp.fail_after:
                    f.write(b[: sftp.fail_after - W.n])
                    raise ConnectionResetError("网络断了")
                W.n += len(b)
                f.write(bytes([b[0] ^ 1]) + b[1:] if sftp.corrupt and b else b)

        return W()

    def rename(self, a, b):
        if os.path.exists(self._p(b)):
            raise IOError("Failure")
        os.rename(self._p(a), self._p(b))

    def posix_rename(self, a, b):
        os.replace(self._p(a), self._p(b))

    def remove(self, p):
        os.remove(self._p(p))

    def chmod(self, p, m):
        os.chmod(self._p(p), m)

    def chown(self, *a):
        pass

    def close(self):
        pass


def test_write_bytes_atomic_backup_and_mode(tmp_path):
    sftp = LocalSftp(tmp_path)
    t = tmp_path / "app.sh"
    t.write_text("old")
    os.chmod(t, 0o750)
    (tmp_path / "app.sh.bak").write_text("older")        # 旧 .bak 在: 原实现 rename 失败后静默覆盖
    r = st.write_bytes(sftp, "/app.sh", "新内容".encode())
    assert t.read_text() == "新内容" and (tmp_path / "app.sh.bak").read_text() == "old"
    assert (t.stat().st_mode & 0o777) == 0o750 and r["verify"] == "sftp-readback"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["app.sh", "app.sh.bak"]


def test_write_bytes_corruption_leaves_target_untouched(tmp_path):
    t = tmp_path / "conf.ini"
    t.write_text("keep")
    with pytest.raises(st.IntegrityError):
        st.write_bytes(LocalSftp(tmp_path, corrupt=True), "/conf.ini", b"new-content")
    assert t.read_text() == "keep" and sorted(x.name for x in tmp_path.iterdir()) == ["conf.ini"]


def test_remote_sha_prefers_remote_command(tmp_path):
    (tmp_path / "f").write_bytes(b"abc")
    want = hashlib.sha256(b"abc").hexdigest()
    got = st.remote_sha256(LocalSftp(tmp_path), "/f", lambda cmd: (0, f"{want}  /f\n", ""))
    assert got == (want, "sha256sum")
    got = st.remote_sha256(LocalSftp(tmp_path), "/f", lambda cmd: (127, "", "not found"))
    assert got == (want, "sftp-readback")


def test_upload_file_resume_after_interruption(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CHUNK", 64 * 1024)
    remote = tmp_path / "remote"
    remote.mkdir()
    src = tmp_path / "pkg.tgz"
    data = os.urandom(3 * 1024 * 1024 + 123)
    src.write_bytes(data)
    with pytest.raises(ConnectionResetError):
        st.upload_file(LocalSftp(remote, fail_after=1_200_000), str(src), "/pkg.tgz")
    assert not (remote / "pkg.tgz").exists() and (remote / "pkg.tgz.lcpart").stat().st_size == 1_200_000
    r = st.upload_file(LocalSftp(remote), str(src), "/pkg.tgz")
    assert r["resumed_from"] == 1_200_000 and r["sha256"] == hashlib.sha256(data).hexdigest()
    assert (remote / "pkg.tgz").read_bytes() == data and not (remote / "pkg.tgz.lcpart").exists()


def test_upload_file_bad_partial_restarts(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    src = tmp_path / "a.bin"
    data = os.urandom(200_000)
    src.write_bytes(data)
    (remote / "a.bin.lcpart").write_bytes(b"garbage" * 100)  # 不是本文件的前缀
    r = st.upload_file(LocalSftp(remote), str(src), "/a.bin")
    assert r["resumed_from"] == 0 and (remote / "a.bin").read_bytes() == data


def test_ssh_write_file_local_path_end_to_end(tmp_path):
    from core.tools.handlers import remote as rh
    remote = tmp_path / "remote"
    remote.mkdir()
    src = tmp_path / "dist.tgz"
    data = os.urandom(500_000)
    src.write_bytes(data)
    client = MagicMock()
    client.open_sftp.return_value = LocalSftp(remote)
    conn = {"host": "h", "port": 22, "user": "u", "password": "p", "key_file": "", "jump": None}
    with patch.object(rh, "_connect", return_value=(client, None)), patch.object(rh, "_conn", return_value=(conn, "")):
        out = asyncio.run(rh.h_ssh_write_file("s", {"path": "/dist.tgz", "local_path": str(src)}))[0]
        assert out.startswith("[sftp") and hashlib.sha256(data).hexdigest() in out, out
        assert (remote / "dist.tgz").read_bytes() == data
        both = asyncio.run(rh.h_ssh_write_file("s", {"path": "/x", "local_path": str(src), "content": "y"}))[0]
        assert "只能给一个" in both
        sync = asyncio.run(rh.h_ssh_sync_files("s", {"files": {"/a.txt": "hi", "/b.tgz": {"local_path": str(src)}}}))[0]
        assert sync.count("已校验") == 2 and (remote / "b.tgz").read_bytes() == data
    assert "密钥" in rh._check_local(os.path.expanduser("~/.ssh/id_rsa")) or "不存在" in rh._check_local(
        os.path.expanduser("~/.ssh/id_rsa"))
    sec = tmp_path / ".secrets"
    sec.mkdir()
    (sec / "k").write_text("x")
    assert "密钥" in rh._check_local(str(sec / "k"))
    assert st.transfer_timeout(0) == 60 and st.transfer_timeout(10 ** 13) == 7200


# ───────────────────────── CLI 下载: 流式 + .part ─────────────────────────
def test_cli_download_streams_and_rejects_truncated(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from lib.cli import common
    import litecli
    from test_cli_projects_memory import FakeResp, FakeServer

    class HResp(FakeResp):
        def __init__(self, content, length):
            super().__init__(200, None, content)
            self.headers = {"Content-Length": str(length)}

        def iter_content(self, n):
            for i in range(0, len(self.content), 4):
                yield self.content[i:i + 4]

    out = tmp_path / "o.bin"
    srv = FakeServer({("GET", "/api/workspace/download"): lambda call: HResp(b"0123456789", 10)})
    monkeypatch.setattr(common, "_session", srv.session)
    res = CliRunner().invoke(litecli.cli, ["ws", "download", "a/o.bin", "-o", str(out)])
    assert res.exit_code == 0, res.output
    assert out.read_bytes() == b"0123456789" and not (tmp_path / "o.bin.part").exists()
    bad = tmp_path / "bad.bin"
    srv = FakeServer({("GET", "/api/workspace/download"): lambda call: HResp(b"01234", 10)})
    monkeypatch.setattr(common, "_session", srv.session)
    res = CliRunner().invoke(litecli.cli, ["ws", "download", "bad.bin", "-o", str(bad)])
    assert res.exit_code != 0 and "不完整" in res.output
    assert not bad.exists() and not (tmp_path / "bad.bin.part").exists()
