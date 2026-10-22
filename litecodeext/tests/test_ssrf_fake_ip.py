"""本机代理 fake-ip / Teredo 映射的公网地址放行, 真内网照拦 (2026-10-22 用户桌面版 web_fetch 被误拦)。"""
import sys
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parent.parent), str(Path(__file__).resolve().parent.parent / "core")]
from plugins.tools.web_search import _is_ssrf_target  # noqa: E402


def test_fake_ip_allowed_internal_blocked():
    assert _is_ssrf_target("http://[2001::6817:7dbd]/") is None      # 104.23.125.189 的映射
    assert _is_ssrf_target("http://198.18.3.4/") is None              # Clash fake-ip
    for bad in ("http://[2001::a00:1]/", "http://10.0.0.1/", "http://127.0.0.1/", "http://169.254.169.254/"):
        assert _is_ssrf_target(bad)
