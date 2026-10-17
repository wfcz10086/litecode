"""GET /api/thumb — 只服务工作区 uploads 内图片 (防穿越 / 软链 / 非 uploads), 磁盘缓存命中, ETag/304, 鉴权。"""
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from routers import thumb_router as tr
    from routers.state import S
    import plugins.auth.core as auth
    ws = tmp_path / 'ws'
    up = ws / 'uploads'
    (up / 'sub').mkdir(parents=True)
    monkeypatch.setattr(S, 'workspace', ws)
    monkeypatch.setattr(auth, '_AUTH_ENABLED', False)
    Image.new('RGB', (1280, 800), (200, 30, 30)).save(up / 'shot.png')
    Image.new('RGBA', (300, 200), (0, 0, 255, 128)).save(up / 'sub' / 'small.png')
    Image.new('RGB', (2000, 1000), (0, 200, 0)).save(up / 'photo.jpg', quality=90)
    (ws / 'secret.png').write_bytes((up / 'shot.png').read_bytes())       # 工作区内但不在 uploads
    (tmp_path / 'outside.png').write_bytes((up / 'shot.png').read_bytes())
    (up / 'notes.txt').write_text('hi')
    (up / 'broken.png').write_bytes(b'not a png')
    app = FastAPI()
    app.include_router(tr.router)
    return TestClient(app), ws, up, tmp_path


def _img(resp):
    from io import BytesIO
    return Image.open(BytesIO(resp.content))


def test_thumb_generates_small_webp_with_cache_headers(env):
    c, ws, up, _ = env
    r = c.get('/api/thumb', params={'path': str(up / 'shot.png'), 'w': 480})
    assert r.status_code == 200
    assert r.headers['content-type'] in ('image/webp', 'image/jpeg')
    assert 'max-age' in r.headers['cache-control'] and r.headers['etag']
    assert r.headers['x-thumb-cache'] == 'miss'
    im = _img(r)
    assert im.size == (480, 300)
    assert len(r.content) < (up / 'shot.png').stat().st_size


def test_relative_path_and_width_bucket_and_no_upscale(env):
    c, ws, up, _ = env
    r = c.get('/api/thumb', params={'path': 'sub/small.png', 'w': 470})   # 相对 uploads; 470 → 480 档
    assert r.status_code == 200 and _img(r).size == (300, 200)           # 小图不放大
    r = c.get('/api/thumb', params={'path': 'photo.jpg', 'w': 5000})     # 上限 1280
    assert r.status_code == 200 and _img(r).size == (1280, 640)
    r = c.get('/api/thumb', params={'path': 'photo.jpg', 'w': 1})
    assert r.status_code == 200 and _img(r).size[0] == 160


def test_cache_hit_and_etag_304(env):
    c, ws, up, _ = env
    p = {'path': str(up / 'shot.png'), 'w': 480}
    r1 = c.get('/api/thumb', params=p)
    files = list((ws / '.thumb_cache').iterdir())
    assert len(files) == 1
    mtime = files[0].stat().st_mtime_ns
    r2 = c.get('/api/thumb', params=p)
    assert r2.headers['x-thumb-cache'] == 'hit' and r2.content == r1.content
    assert files[0].stat().st_mtime_ns == mtime                           # 未重新生成
    r3 = c.get('/api/thumb', params=p, headers={'If-None-Match': r1.headers['etag']})
    assert r3.status_code == 304 and not r3.content


def test_cache_key_changes_when_source_changes(env):
    c, ws, up, _ = env
    p = {'path': str(up / 'shot.png'), 'w': 480}
    e1 = c.get('/api/thumb', params=p).headers['etag']
    Image.new('RGB', (640, 640), (1, 2, 3)).save(up / 'shot.png')
    os.utime(up / 'shot.png', (time.time() + 5, time.time() + 5))
    r = c.get('/api/thumb', params=p)
    assert r.headers['etag'] != e1 and r.headers['x-thumb-cache'] == 'miss' and _img(r).size == (480, 480)
    # 不同宽度 = 不同缓存项
    c.get('/api/thumb', params={**p, 'w': 160})
    assert len(list((ws / '.thumb_cache').iterdir())) == 3


@pytest.mark.parametrize('bad', [
    '../secret.png',                 # 相对穿越
    'sub/../../secret.png',
    '{ws}/secret.png',               # 工作区内但不在 uploads
    '{ws}/uploads/../secret.png',    # 绝对路径穿越
    '{tmp}/outside.png',
    '/etc/passwd',
    '{ws}/uploads',                  # 目录本身
])
def test_traversal_and_non_uploads_rejected(env, bad):
    c, ws, up, tmp = env
    r = c.get('/api/thumb', params={'path': bad.format(ws=ws, tmp=tmp), 'w': 480})
    assert r.status_code == 403


def test_symlink_escape_rejected(env):
    c, ws, up, tmp = env
    (up / 'link.png').symlink_to(tmp / 'outside.png')
    assert c.get('/api/thumb', params={'path': 'link.png'}).status_code == 403


def test_bad_inputs(env):
    c, ws, up, _ = env
    assert c.get('/api/thumb').status_code == 400
    assert c.get('/api/thumb', params={'path': 'nope.png'}).status_code == 404
    assert c.get('/api/thumb', params={'path': 'notes.txt'}).status_code == 415
    assert c.get('/api/thumb', params={'path': 'broken.png'}).status_code == 422
    assert c.get('/api/thumb', params={'path': 'shot.png', 'w': 'abc'}).status_code == 422


def test_auth_same_as_media(env, monkeypatch):
    c, ws, up, _ = env
    import plugins.auth.core as auth
    monkeypatch.setattr(auth, '_AUTH_ENABLED', True)
    monkeypatch.setattr(auth, '_valid_tokens', {'tok': time.time() + 100})
    assert c.get('/api/thumb', params={'path': 'shot.png'}).status_code == 401
    # 未登录时不泄露文件存在性 / 路径校验结果
    assert c.get('/api/thumb', params={'path': '../secret.png'}).status_code == 401
    c.cookies.set('lc_auth', 'tok')
    assert c.get('/api/thumb', params={'path': 'shot.png'}).status_code == 200


def test_router_is_auto_discovered():
    from routers import discover
    paths = {getattr(r, 'path', '') for router in discover() for r in router.routes}
    assert '/api/thumb' in paths


def test_evict_cache_keeps_under_cap(tmp_path):
    """缓存超上限时按最旧淘汰, 降到上限 90% 以下; 未超上限不动。"""
    import os, time
    from routers.thumb_router import evict_cache
    files = []
    for i in range(10):
        f = tmp_path / f"t{i}.webp"
        f.write_bytes(b"x" * 1000)
        t = time.time() - (100 - i)            # i 越小越旧
        os.utime(f, (t, t))
        files.append(f)
    evict_cache(tmp_path, cap=20000)           # 总 10000 < 20000, 不淘汰
    assert all(f.exists() for f in files)
    evict_cache(tmp_path, cap=5000)            # 超上限: 降到 4500 以下 → 只留最新的 4 个
    kept = sorted(f.name for f in tmp_path.iterdir())
    assert kept == ["t6.webp", "t7.webp", "t8.webp", "t9.webp"]
