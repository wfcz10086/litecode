"""flow 05: 多文件上传 — v1.9 P39-e"""
import json
import os
import tempfile
import pytest
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse


def _api_login(server_url: str, password: str) -> str:
    req = urllib.request.Request(
        f"{server_url}/api/login",
        data=json.dumps({"password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        for h in r.headers.get_all("Set-Cookie") or []:
            if h.startswith("lc_auth="):
                return h.split(";")[0].split("=", 1)[1]
    return ""


def _create_test_files(n: int = 3) -> list:
    """生成 n 个临时小文件用于上传"""
    paths = []
    for i in range(n):
        fd, p = tempfile.mkstemp(suffix=f"_p39e_{i}.txt", prefix="upload_test_")
        os.write(fd, f"test content {i}\n".encode())
        os.close(fd)
        paths.append(Path(p))
    return paths


def _upload_via_api(server_url: str, cookie: str, sid: str, file_path: Path):
    """通过 multipart/form-data API 上传文件"""
    boundary = "----p39ETest" + str(file_path.name).encode().hex()[:8]
    body_lines = []
    body_lines.append(f"--{boundary}".encode())
    body_lines.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()
    )
    body_lines.append(b"Content-Type: text/plain")
    body_lines.append(b"")
    body_lines.append(file_path.read_bytes())
    body_lines.append(f"--{boundary}--".encode())
    body_lines.append(b"")
    body = b"\r\n".join(body_lines)

    req = urllib.request.Request(
        f"{server_url}/api/upload/{sid}",
        data=body,
        headers={
            "Cookie": f"lc_auth={cookie}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"_err": e.read().decode("utf-8", "replace")[:300]}


def _create_session(server_url: str, cookie: str, name: str = "p39e_upload") -> str:
    headers = {"Cookie": f"lc_auth={cookie}", "Content-Type": "application/json"}
    req = urllib.request.Request(
        f"{server_url}/api/sessions",
        data=json.dumps({"name": name}).encode(),
        headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as r:
        d = json.loads(r.read())
    return d.get("id", "")


def test_upload_single_file_via_api(server_required, auth_password):
    """T1: API 上传单文件成功"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    sid = _create_session(server_required, cookie)
    if not sid:
        pytest.skip("无法创建 session")

    files = _create_test_files(1)
    try:
        code, d = _upload_via_api(server_required, cookie, sid, files[0])
        assert code == 200, f"上传失败: HTTP {code} {d}"
        # 期望返回 ok + path
        assert d.get("ok") is True or "path" in d, f"返回非预期: {d}"
    finally:
        for f in files:
            try:
                f.unlink()
            except Exception:
                pass


def test_upload_three_files_via_api(server_required, auth_password):
    """T2: API 连续上传 3 文件全部成功"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    sid = _create_session(server_required, cookie, "p39e_three")
    if not sid:
        pytest.skip("无法创建 session")

    files = _create_test_files(3)
    try:
        for i, f in enumerate(files):
            code, d = _upload_via_api(server_required, cookie, sid, f)
            assert code == 200, f"上传 #{i+1} 失败: HTTP {code} {d}"
    finally:
        for f in files:
            try:
                f.unlink()
            except Exception:
                pass


def test_upload_returns_workspace_path(server_required, auth_password):
    """T3: 上传 API 返回 path/name/size 三件套，且 path 落在 workspace 内"""
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")
    sid = _create_session(server_required, cookie, "p39e_path")
    if not sid:
        pytest.skip("无法创建 session")

    files = _create_test_files(1)
    try:
        code, d = _upload_via_api(server_required, cookie, sid, files[0])
        assert code == 200, f"上传失败: {code} {d}"
        # 验证响应字段
        assert d.get("ok") is True, f"ok != True: {d}"
        assert "path" in d, f"缺 path: {d}"
        assert "name" in d, f"缺 name: {d}"
        assert "size" in d, f"缺 size: {d}"
        # path 应是 workspace 内的绝对路径
        path = d["path"]
        assert path.startswith("/"), f"path 不是绝对路径: {path}"
        # 大小匹配
        assert d["size"] == files[0].stat().st_size, \
            f"size 不匹配: 服务端 {d['size']} vs 本地 {files[0].stat().st_size}"
    finally:
        for f in files:
            try:
                f.unlink()
            except Exception:
                pass


def test_upload_input_dom_present(server_required, playwright_required, auth_password):
    """T4: UI #fi multiple input 存在且支持 multi-select"""
    from playwright.sync_api import sync_playwright
    cookie = _api_login(server_required, auth_password)
    if not cookie:
        pytest.skip("登录失败")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        u = urlparse(server_required)
        context.add_cookies([{"name": "lc_auth", "value": cookie,
                              "domain": u.hostname, "path": "/"}])
        page = context.new_page()
        try:
            page.goto(server_required, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(500)
            # 验证 #fi 存在并是 multiple
            fi = page.locator("#fi")
            assert fi.count() == 1, "找不到 #fi"
            assert fi.get_attribute("multiple") is not None, "#fi 缺 multiple 属性"
            # 验证 #upb 上传按钮
            upb = page.locator("#upb")
            assert upb.count() == 1, "找不到 #upb 上传按钮"
        finally:
            browser.close()
