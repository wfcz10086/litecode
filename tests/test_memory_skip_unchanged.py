import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))


def _make_mgr(tmp_path):
    """构造一个最小化 MemoryManager 用于测试 _atomic_write_if_changed 方法"""
    from core.memory import MemoryManager
    mgr = MemoryManager(
        workspace=tmp_path,
        session_id="testsid",
        vllm_url="http://x",
        model_id="x",
    )
    return mgr


def test_first_write_actually_writes(tmp_path):
    mgr = _make_mgr(tmp_path)
    p = tmp_path / "f.md"
    wrote = mgr._atomic_write_if_changed(p, "hello")
    assert wrote is True
    assert p.read_text() == "hello"


def test_same_content_skipped(tmp_path):
    mgr = _make_mgr(tmp_path)
    p = tmp_path / "f.md"
    mgr._atomic_write_if_changed(p, "hello")
    mtime1 = p.stat().st_mtime_ns
    import time; time.sleep(0.01)
    wrote = mgr._atomic_write_if_changed(p, "hello")
    assert wrote is False
    mtime2 = p.stat().st_mtime_ns
    assert mtime1 == mtime2  # mtime 未变


def test_changed_content_writes(tmp_path):
    mgr = _make_mgr(tmp_path)
    p = tmp_path / "f.md"
    mgr._atomic_write_if_changed(p, "hello")
    wrote = mgr._atomic_write_if_changed(p, "world")
    assert wrote is True
    assert p.read_text() == "world"


def test_force_writes_even_if_same(tmp_path):
    mgr = _make_mgr(tmp_path)
    p = tmp_path / "f.md"
    mgr._atomic_write_if_changed(p, "hello")
    wrote = mgr._atomic_write_if_changed(p, "hello", force=True)
    assert wrote is True


def test_every_n_forces_full(tmp_path):
    mgr = _make_mgr(tmp_path)
    p = tmp_path / "f.md"
    mgr._atomic_write_if_changed(p, "hello")  # write #1
    # 18 次相同——全 skip
    for _ in range(18):
        assert mgr._atomic_write_if_changed(p, "hello") is False
    # 第 20 次（_write_count==20）—— 强制全量
    wrote = mgr._atomic_write_if_changed(p, "hello")
    assert wrote is True


def test_multi_path_independent_hashes(tmp_path):
    mgr = _make_mgr(tmp_path)
    p1 = tmp_path / "a.md"
    p2 = tmp_path / "b.md"
    mgr._atomic_write_if_changed(p1, "A")
    mgr._atomic_write_if_changed(p2, "B")
    # 各自重复写——都应跳过
    assert mgr._atomic_write_if_changed(p1, "A") is False
    assert mgr._atomic_write_if_changed(p2, "B") is False
