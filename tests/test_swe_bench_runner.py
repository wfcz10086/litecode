import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from swe_bench_runner import (
    load_manifest, score_run, run_dry, write_report, MANIFEST_50
)


def test_score_run_empty():
    s = score_run([])
    assert s["total"] == 0
    assert s["rate"] == 0.0


def test_score_run_mixed():
    results = [
        {"status": "resolved"}, {"status": "resolved"},
        {"status": "partial"},
        {"status": "failed"}, {"status": "failed"},
        {"status": "error"},
        {"status": "skipped"},
    ]
    s = score_run(results)
    assert s["total"] == 7
    assert s["resolved"] == 2
    assert s["partial"] == 1
    assert s["failed"] == 2
    assert s["error"] == 1
    assert s["skipped"] == 1
    assert s["rate"] == round(2 / 7 * 100, 1)


def test_load_manifest_fallback_to_placeholder(monkeypatch):
    """datasets 不可用 + 无 jsonl → 走 MANIFEST_50 占位"""
    import sys as _sys
    _orig = _sys.modules.pop("datasets", None)
    _sys.modules["datasets"] = None  # 让 from datasets import 抛
    try:
        instances, source = load_manifest("verified", 10, None)
        assert source == "placeholder"
        assert len(instances) == 10
        assert instances[0]["id"] == "swe_01"
    finally:
        if _orig is not None:
            _sys.modules["datasets"] = _orig
        else:
            _sys.modules.pop("datasets", None)


def test_dry_run_marks_all_skipped():
    instances = MANIFEST_50[:3]
    results = run_dry(instances)
    assert len(results) == 3
    assert all(r["status"] == "skipped" for r in results)
    assert all(r["reason"] == "dry-run" for r in results)


def test_write_report(tmp_path):
    instances = MANIFEST_50[:2]
    results = run_dry(instances)
    score = score_run(results)
    p = tmp_path / "rep.md"
    write_report(p, instances, results, source="placeholder",
                 score=score, dry_run=True)
    txt = p.read_text(encoding="utf-8")
    assert "SWE-bench Verified 跑分报告" in txt
    assert "dry-run" in txt
    assert "swe_01" in txt
