"""会话按项目归组: 绑定语义 + 项目上下文块 + 每轮注入去重。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

from core import project_sessions as ps  # noqa: E402
from core.projects import ProjectStore, load_project_memory  # noqa: E402


def _store(tmp_path):
    return ProjectStore(tmp_path)


def test_session_belongs_to_one_project(tmp_path):
    st = _store(tmp_path)
    a = st.create("A").project_id
    b = st.create("B").project_id
    assert ps.bind(st, a, "web-1") == 1
    assert ps.bind(st, a, "web-1") == 1          # 重复绑不重复记
    ps.bind(st, b, "web-1")                      # 挪到 B
    assert ps.sessions_of(st, a) == [] and ps.sessions_of(st, b) == ["web-1"]
    assert ps.session_map(st) == {"web-1": b}
    assert ps.unbind(st, "web-1") and ps.project_of(st, "web-1") is None
    assert not ps.unbind(st, "web-1")


def test_archived_project_releases_sessions(tmp_path):
    st = _store(tmp_path)
    a = st.create("A").project_id
    ps.bind(st, a, "web-1")
    st.delete(a)                                 # 软删 = archived
    assert ps.session_map(st) == {}


def test_bind_unknown_project_raises(tmp_path):
    st = _store(tmp_path)
    try:
        ps.bind(st, "nope", "web-1")
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_context_block_contents_and_fingerprint(tmp_path):
    st = _store(tmp_path)
    m = st.create("小说A", project_type="novel")
    assert ps.context_block(st, m) == ""         # 什么都没填 → 不注入
    ps.write_instructions(st, m.project_id, "全程用第一人称")
    m = st.update(m.project_id, workspace="/data/novel")
    assert m
    block = ps.context_block(st, m)
    head = block.split("\n", 1)[0]
    assert head.startswith("[Project: 小说A · ") and "第一人称" in block and "/data/novel" in block
    ps.write_instructions(st, m.project_id, "全程用第三人称")
    assert ps.context_block(st, m).split("\n", 1)[0] != head   # 改了内容 → 指纹变


def test_untouched_templates_not_injected_but_decisions_are(tmp_path):
    st = _store(tmp_path)
    m = st.create("代码", project_type="code")
    assert load_project_memory(st, m.project_id) == ""          # 4 个空模板都跳过
    (st.project_dir(m.project_id) / "PROJECT_DECISIONS.md").write_text("# PROJECT_DECISIONS\n\n用 PostgreSQL")
    assert "PostgreSQL" in load_project_memory(st, m.project_id)


def test_turn_source_injects_once_per_session(tmp_path, monkeypatch):
    from lib import turn_context as tc
    from lib import turn_sources
    monkeypatch.setattr(turn_sources, "WORKSPACE", tmp_path)
    st = _store(tmp_path)
    pid = st.create("P").project_id
    ps.write_instructions(st, pid, "回答都用英文")
    inp = tc.TurnInput("hi", "web-9", [])
    assert turn_sources.project(inp) == ""                       # 未绑定
    ps.bind(st, pid, "web-9")
    first = turn_sources.project(inp)
    assert "回答都用英文" in first
    hist = [{"role": "user", "content": "hi", tc.KEY: first}]
    assert turn_sources.project(tc.TurnInput("again", "web-9", hist)) == ""   # 已给过
    ps.write_instructions(st, pid, "回答都用日文")
    assert "日文" in turn_sources.project(tc.TurnInput("again", "web-9", hist))
    assert "project" in tc.sources()
