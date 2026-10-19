"""ssh_exec: 密钥登录 / 跳板 (ssh -J) / 主机别名存储位置。真连远程的验证在容器里手动跑过 (jumphost → win11)。"""
import asyncio
import os
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "core")]
os.environ["LITECODE_SSH_HOSTS"] = os.path.join(tempfile.mkdtemp(), "ssh_hosts.json")
from core.tools.handlers import remote, ssh_hosts  # noqa: E402


class TestSshKeyJump(unittest.TestCase):
    def test_auth_kwargs(self):
        self.assertEqual(remote._auth_kwargs("pw", ""), {"password": "pw"})
        kw = remote._auth_kwargs("", "~/.ssh/k")
        self.assertIsNone(kw["password"]); self.assertTrue(kw["key_filename"].endswith("/.ssh/k"))

    def test_needs_password_or_key(self):
        out = asyncio.run(remote.h_ssh_exec("t", {"host": "h", "cmd": "whoami"}))[0]
        self.assertIn("password 或 key_file", out)

    def test_jump_alias_resolution(self):
        captured = {}

        async def fake_ssh(host, port, user, password, cmd, timeout, key_file="", jump=None):
            captured.update(host=host, port=port, key_file=key_file, jump=jump)
            return 0, "ok", ""
        orig = remote._ssh
        remote._ssh = fake_ssh
        try:
            asyncio.run(ssh_hosts.h_ssh_save_host("t", {"alias": "hostA", "host": "a.example", "port": 35000,
                                                         "user": "u", "key_file": "/k/a"}))
            asyncio.run(ssh_hosts.h_ssh_save_host("t", {"alias": "vm", "host": "127.0.0.1", "port": 2222,
                                                         "user": "w", "key_file": "/k/w", "jump": "hostA"}))
            out = asyncio.run(remote.h_ssh_exec("t", {"alias": "vm", "cmd": "whoami"}))[0]
            self.assertIn("rc=0", out)
            self.assertEqual((captured["host"], captured["port"], captured["key_file"]), ("127.0.0.1", 2222, "/k/w"))
            self.assertEqual((captured["jump"]["host"], captured["jump"]["port"], captured["jump"]["key_file"]),
                             ("a.example", 35000, "/k/a"))
            bad = asyncio.run(remote.h_ssh_exec("t", {"host": "x", "key_file": "/k", "jump": "nope", "cmd": "id"}))[0]
            self.assertIn("没存过", bad)
        finally:
            remote._ssh = orig

    def test_file_and_docker_tools_share_conn(self):
        """ssh_read/write/str_replace/sync + docker_remote_* 都走 _conn: 别名 / 密钥 / 跳板一视同仁。"""
        seen = []

        def fake_connect(conn):
            seen.append(conn)
            raise RuntimeError("stop-here")
        orig = remote._connect
        remote._connect = fake_connect
        try:
            asyncio.run(ssh_hosts.h_ssh_save_host("t", {"alias": "hostB", "host": "b.example", "key_file": "/k/b"}))
            asyncio.run(ssh_hosts.h_ssh_save_host("t", {"alias": "vm2", "host": "127.0.0.1", "port": 2223,
                                                         "key_file": "/k/v", "jump": "hostB"}))
            for fn, a in ((remote.h_ssh_read_file, {"path": "/a"}), (remote.h_ssh_write_file, {"path": "/a", "content": "x"}),
                          (remote.h_ssh_str_replace, {"path": "/a", "old_str": "x"}),
                          (remote.h_ssh_sync_files, {"files": {"/a": "x"}})):
                out = asyncio.run(fn("t", {"alias": "vm2", **a}))[0]
                self.assertIn("stop-here", out)
            self.assertEqual(len(seen), 4)
            self.assertTrue(all(c["port"] == 2223 and c["key_file"] == "/k/v" and c["jump"]["host"] == "b.example" for c in seen))
            self.assertIn("需要 password 或 key_file", asyncio.run(remote.h_ssh_read_file("t", {"host": "h", "path": "/a"}))[0])
            self.assertIn("需要 host", asyncio.run(remote.h_docker_remote_ps("t", {}))[0])
        finally:
            remote._connect = orig

    def test_list_hosts_shows_jump_and_key(self):
        asyncio.run(ssh_hosts.h_ssh_save_host("t", {"alias": "lh1", "host": "h1", "key_file": "/k"}))
        asyncio.run(ssh_hosts.h_ssh_save_host("t", {"alias": "lh2", "host": "127.0.0.1", "key_file": "/k", "jump": "lh1"}))
        out = asyncio.run(ssh_hosts.h_ssh_list_hosts("t", {}))[0]
        self.assertIn("lh2: root@127.0.0.1:22 经跳板 lh1 [密钥]", out)

    def test_password_output_never_reveals_even_short_secrets(self):
        for secret in ("ZX-super-secret", "Q", "墨密"):
            out = asyncio.run(ssh_hosts.h_ssh_save_host("t", {
                "alias": "private", "host": "test.example", "password": secret,
            }))[0]
            listed = asyncio.run(ssh_hosts.h_ssh_list_hosts("t", {}))[0]
            self.assertIn("pwd=(saved)", out)
            self.assertIn("密码已保存", listed)
            for text in (out, listed):
                self.assertNotIn(secret, text)
                self.assertNotIn(secret[:2], text)
            self.assertEqual(ssh_hosts.get_ssh_creds("private")["password"], secret)

    def test_store_not_in_code_dir(self):
        self.assertNotIn("litecodeext", str(ssh_hosts._STORE_PATH).replace(tempfile.gettempdir(), ""))


if __name__ == "__main__":
    unittest.main()
