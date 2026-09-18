#!/usr/bin/env python3
# S1-N phase 1 冒烟: ArtifactStore put/get/list/delete/purge.
import sys, pathlib, tempfile, os
ROOT = pathlib.Path(__file__).resolve().parents[1] / "litecodeext"
sys.path.insert(0, str(ROOT))
from plugins.artifacts.store import ArtifactStore, get_store, reset_store_for_test  # noqa: E402


def test_put_and_meta():
    with tempfile.TemporaryDirectory() as tmp:
        s = ArtifactStore(tmp)
        aid = s.put(owner="timer:daily-sync", kind="log",
                    data=b"tick tick tick", name="run-1.log",
                    meta={"exit_code": 0})
        assert len(aid) == 12, aid
        m = s.meta(aid)
        assert m and m.owner == "timer:daily-sync" and m.kind == "log"
        assert m.size == len(b"tick tick tick")
        assert m.tags["exit_code"] == 0
        assert s.blob(aid) == b"tick tick tick"
        print(f"[OK] put + meta + blob roundtrip; aid={aid}")


def test_owner_safe_path():
    with tempfile.TemporaryDirectory() as tmp:
        s = ArtifactStore(tmp)
        s.put(owner="plugin:cad_generate:job/../etc/passwd", kind="dxf",
              data=b"DUMMY", name="a.dxf")
        # 目录名必须被 sanitize, 不允许出现 ../
        for d in pathlib.Path(tmp).iterdir():
            assert ".." not in d.name and "/" not in d.name
        print(f"[OK] owner path traversal safe; got dir {[d.name for d in pathlib.Path(tmp).iterdir()]}")


def test_list_by_owner_and_all():
    with tempfile.TemporaryDirectory() as tmp:
        s = ArtifactStore(tmp)
        a = s.put(owner="timer:A", kind="log", data=b"x", name="1.log")
        b = s.put(owner="timer:A", kind="log", data=b"y", name="2.log")
        c = s.put(owner="bg:9999", kind="txt", data=b"z", name="stdout.txt")
        la = s.list_by_owner("timer:A")
        assert {r.aid for r in la} == {a, b}
        assert la[0].ts >= la[1].ts   # 时间降序
        all_ = s.list_all()
        assert {r.aid for r in all_} == {a, b, c}
        # prefix 过滤
        ts = s.list_all(prefix="timer:")
        assert {r.aid for r in ts} == {a, b}
        print(f"[OK] list_by_owner / list_all / prefix filter (owners={s.owners()})")


def test_delete_and_purge():
    with tempfile.TemporaryDirectory() as tmp:
        s = ArtifactStore(tmp)
        a = s.put(owner="plugin:pptx_render:job1", kind="pptx", data=b"P", name="deck.pptx")
        b = s.put(owner="plugin:pptx_render:job1", kind="log", data=b"L", name="job.log")
        assert s.delete(a)
        assert s.meta(a) is None
        assert s.meta(b) is not None
        n = s.purge_owner("plugin:pptx_render:job1")
        assert n >= 2   # bin + meta
        assert s.meta(b) is None
        print(f"[OK] delete single + purge_owner (purged {n} files)")


def test_global_singleton_env():
    reset_store_for_test()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["LITECODE_ARTIFACT_ROOT"] = tmp
        s1 = get_store()
        s2 = get_store()
        assert s1 is s2
        assert str(s1.root) == tmp
        del os.environ["LITECODE_ARTIFACT_ROOT"]
        reset_store_for_test()
        print("[OK] get_store singleton honors LITECODE_ARTIFACT_ROOT")


if __name__ == "__main__":
    test_put_and_meta()
    test_owner_safe_path()
    test_list_by_owner_and_all()
    test_delete_and_purge()
    test_global_singleton_env()
    print("\n[PASS] S1-N phase 1 artifact_store smoke")
