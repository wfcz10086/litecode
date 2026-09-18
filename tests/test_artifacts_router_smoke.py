#!/usr/bin/env python3
# S1-N phase 2 冒烟: /api/artifacts_v2/* endpoints (FastAPI TestClient).
import sys, os, pathlib, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

# 用临时目录当 artifact 根, 避免脏数据
_TMP = tempfile.mkdtemp(prefix="lc_art_test_")
os.environ["LITECODE_ARTIFACT_ROOT"] = _TMP

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from routers.artifacts_v2_router import router  # noqa: E402
from plugins.artifacts.store import get_store, reset_store_for_test  # noqa: E402


# 关闭鉴权 (require_auth 缺 header 会 401; 测试用一个绕过 override)
import routers.auth_router as _auth  # noqa: E402
_orig_require = _auth.require_auth
_auth.require_auth = lambda req: None   # 全绿

reset_store_for_test()
app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _seed():
    s = get_store()
    a1 = s.put(owner="timer:daily", kind="log", data=b"tick", name="run.log")
    a2 = s.put(owner="bg:1234", kind="txt", data=b"stdout line\n", name="out.txt")
    a3 = s.put(owner="plugin:cad_generate:abc", kind="dxf",
               data=b"DXF-BINARY", name="part.dxf", meta={"pages": 1})
    return a1, a2, a3


def test_list_all():
    a1, a2, a3 = _seed()
    r = client.get("/api/artifacts_v2")
    assert r.status_code == 200, r.text
    data = r.json()
    aids = {i["aid"] for i in data["items"]}
    assert {a1, a2, a3}.issubset(aids), aids
    print(f"[OK] GET /api/artifacts_v2 → {data['count']} items")


def test_filter_owner_and_prefix():
    r = client.get("/api/artifacts_v2", params={"owner": "timer:daily"})
    ownies = r.json()["items"]
    assert all(i["owner"] == "timer:daily" for i in ownies)
    r2 = client.get("/api/artifacts_v2", params={"prefix": "plugin:"})
    assert all(i["owner"].startswith("plugin:") for i in r2.json()["items"])
    print(f"[OK] owner={len(ownies)} + prefix=plugin: filter working")


def test_owners_endpoint():
    r = client.get("/api/artifacts_v2/owners")
    assert r.status_code == 200
    owners = r.json()["owners"]
    assert "timer:daily" in owners and "bg:1234" in owners
    print(f"[OK] /owners → {owners}")


def test_meta_and_blob():
    lst = client.get("/api/artifacts_v2").json()["items"]
    dxf = next(i for i in lst if i["kind"] == "dxf")
    aid = dxf["aid"]

    m = client.get(f"/api/artifacts_v2/meta/{aid}").json()
    assert m["name"] == "part.dxf" and m["tags"]["pages"] == 1

    blob = client.get(f"/api/artifacts_v2/blob/{aid}")
    assert blob.status_code == 200
    assert blob.content == b"DXF-BINARY"
    assert "application/dxf" in blob.headers["content-type"]

    d = client.get(f"/api/artifacts_v2/blob/{aid}", params={"download": 1})
    assert 'attachment; filename="part.dxf"' in d.headers.get("content-disposition", "")
    print("[OK] meta + blob (inline & attachment) both work")


def test_404_paths():
    assert client.get("/api/artifacts_v2/meta/xxxxxxxxxxxx").status_code == 404
    assert client.get("/api/artifacts_v2/blob/xxxxxxxxxxxx").status_code == 404
    print("[OK] unknown aid → 404")


def test_delete_and_purge():
    a1, a2, a3 = _seed()
    r = client.delete(f"/api/artifacts_v2/{a2}")
    assert r.status_code == 200 and r.json()["ok"]
    assert client.get(f"/api/artifacts_v2/meta/{a2}").status_code == 404

    r = client.delete("/api/artifacts_v2/owner/timer:daily")
    assert r.status_code == 200 and r.json()["purged_files"] > 0
    r2 = client.get("/api/artifacts_v2", params={"owner": "timer:daily"}).json()
    assert r2["count"] == 0
    print("[OK] DELETE single + DELETE owner (purge)")


if __name__ == "__main__":
    try:
        test_list_all()
        test_filter_owner_and_prefix()
        test_owners_endpoint()
        test_meta_and_blob()
        test_404_paths()
        test_delete_and_purge()
        print("\n[PASS] S1-N phase 2 artifacts router smoke")
    finally:
        _auth.require_auth = _orig_require
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)
