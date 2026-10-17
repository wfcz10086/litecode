"""Web 路由插拔: routers/<名>_router.py 自动发现, 按 ORDER 保持原注册顺序 (路径重叠时先注册者生效)。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]


def test_all_router_modules_discovered_static_last():
    import routers
    from routers import static_router, chat_router
    found = routers.discover()
    modules = sorted(p.stem for p in (ROOT / 'routers').glob('*_router.py') if p.stem != 'auth_router')
    assert len(found) == len(modules)
    assert found[-1] is static_router.router                # 兜底静态资源必须最后
    assert chat_router.router in found


def test_new_router_defaults_before_static(tmp_path, monkeypatch):
    import routers
    pkg = tmp_path / 'routers'
    pkg.mkdir()
    (pkg / '__init__.py').write_text((ROOT / 'routers' / '__init__.py').read_text())
    for name, order in (('zzz_router', None), ('static_router', 999), ('aaa_router', 10)):
        body = 'from fastapi import APIRouter\nrouter = APIRouter()\n'
        if order is not None:
            body += f'ORDER = {order}\n'
        (pkg / f'{name}.py').write_text(body + f'NAME = "{name}"\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    for m in [k for k in sys.modules if k == 'routers' or k.startswith('routers.')]:
        monkeypatch.delitem(sys.modules, m)
    import routers as fresh
    got = fresh.discover()
    mods = [sys.modules[f'routers.{n}'] for n in ('aaa_router', 'zzz_router', 'static_router')]
    assert got == [m.router for m in mods]               # 显式 ORDER 在前, 默认 500 居中, 999 最后
    assert routers is not fresh
