import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))

from lib.dag_schema import validate_dag_json, dag_to_dict, save_dag, load_dag


def test_schema_accepts_breakpoint_bool():
    d = {"steps": [{"id":"s1","task":"t","breakpoint":True}]}
    ok, errs = validate_dag_json(d)
    assert ok, errs


def test_schema_rejects_breakpoint_string():
    d = {"steps": [{"id":"s1","task":"t","breakpoint":"yes"}]}
    ok, errs = validate_dag_json(d)
    assert not ok
    assert any("breakpoint" in e for e in errs)


def test_schema_breakpoint_optional():
    d = {"steps": [{"id":"s1","task":"t"}]}
    ok, errs = validate_dag_json(d)
    assert ok, errs


def test_dag_to_dict_outputs_breakpoint():
    class FakeStep:
        id = "s1"; task = "t"; agent_type = "coder"; label = ""
        depends_on = []; timeout = 300; max_retries = 2
        context = ""; critical = True; retry_strategy = ""
        breakpoint = True
    class FakePlan:
        steps = [FakeStep()]
    d = dag_to_dict(FakePlan())
    assert d["steps"][0]["breakpoint"] is True


def test_save_load_roundtrip_preserves_breakpoint(tmp_path):
    plan = {"steps": [{"id":"s1","task":"t","breakpoint":True},
                      {"id":"s2","task":"t2","breakpoint":False,"depends_on":["s1"]}]}
    save_dag(tmp_path, "test_dag", plan)
    loaded = load_dag(tmp_path, "test_dag")
    assert loaded["steps"][0]["breakpoint"] is True
    assert loaded["steps"][1]["breakpoint"] is False


def test_bg_run_prefill_breakpoints_logic():
    """模拟 _bg_run 入口的预填逻辑"""
    steps = [
        {"id":"s1","task":"t","breakpoint":True},
        {"id":"s2","task":"t2"},
        {"id":"s3","task":"t3","breakpoint":False},
        {"id":"s4","task":"t4","breakpoint":True},
    ]
    bp = {s.get("id") for s in steps if s.get("breakpoint")}
    assert bp == {"s1", "s4"}
