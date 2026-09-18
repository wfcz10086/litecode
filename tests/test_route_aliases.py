"""P52: 验证新旧路径都能命中同一 handler"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))


def _client():
    """构造 fastapi TestClient (litecode_server 启动需要 _init_tool_dispatch 已跑过)"""
    from fastapi.testclient import TestClient
    import litecode_server  # 顶层 init 已调
    return TestClient(litecode_server.app)


def test_v1_memory_get_path_registered():
    routes = [r.path for r in _client().app.routes if hasattr(r, "path")]
    assert "/v1/memory/{sid}" in routes
    assert "/memory/{sid}" in routes  # alias 仍在


def test_v1_stats_path_registered():
    routes = [r.path for r in _client().app.routes if hasattr(r, "path")]
    assert "/v1/stats" in routes
    assert "/stats" in routes


def test_v1_clawinfo_registered():
    routes = [r.path for r in _client().app.routes if hasattr(r, "path")]
    assert "/v1/clawinfo" in routes
    assert "/clawinfo" in routes


def test_v1_questions_registered():
    routes = [r.path for r in _client().app.routes if hasattr(r, "path")]
    assert "/v1/questions" in routes
    assert "/questions" in routes


def test_health_no_v1_prefix():
    """/health 是 HTTP 标准约定，应保留无前缀"""
    routes = [r.path for r in _client().app.routes if hasattr(r, "path")]
    assert "/health" in routes
    # 不强制 /v1/health 不存在（无所谓）


def test_aliases_return_same_status():
    """新旧路径调用应返回相同 status code（说明指向同 handler）"""
    c = _client()
    # 未授权场景：两个路径都应返回 401（同 handler 都过 _require_auth）
    # 或 200/4xx 都行，关键是相同
    r1 = c.get("/v1/stats")
    r2 = c.get("/stats")
    assert r1.status_code == r2.status_code
