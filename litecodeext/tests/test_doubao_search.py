"""火山豆包搜索适配: 请求体/鉴权/结果映射/错误处理 (mock httpx, 不打真实接口)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
import doubao_search as dbs  # noqa: E402


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.content = b"x"
        self.text = str(data)

    def json(self):
        return self._data


def test_request_and_mapping(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout, headers):
        seen.update(url=url, body=json, headers=headers)
        return _Resp(200, {"Result": {"ResultCount": 2, "WebResults": [
            {"Title": "今日A股收盘", "Url": "https://a.cn/1", "Summary": "沪指涨 1%", "Snippet": "短",
             "SiteName": "新浪财经", "PublishTime": "2026-10-22T15:05:00+08:00"},
            {"Title": "", "Url": ""},
        ]}})

    monkeypatch.setattr(dbs.httpx, "post", fake_post)
    r = dbs.doubao_search("A股 今天行情", "k-123", top_k=5)
    assert seen["url"] == dbs.DOUBAO_SEARCH_URL
    assert seen["headers"]["Authorization"] == "Bearer k-123"
    assert seen["body"]["Query"] == "A股 今天行情" and seen["body"]["SearchType"] == "web"
    assert seen["body"]["Count"] == 5 and seen["body"]["Filter"]["NeedContent"] is False
    assert r["ok"] and len(r["results"]) == 1
    x = r["results"][0]
    assert x["snippet"] == "沪指涨 1%" and x["published"] == "2026-10-22 15:05"
    assert "新浪财经" in r["message"] and "https://a.cn/1" in r["message"]


def test_http_error_and_empty(monkeypatch):
    monkeypatch.setattr(dbs.httpx, "post", lambda *a, **k: _Resp(401, {"ResponseMetadata": {"Error": {"Message": "invalid key"}}}))
    r = dbs.doubao_search("x", "bad")
    assert not r["ok"] and "401" in r["message"] and "invalid key" in r["message"]
    monkeypatch.setattr(dbs.httpx, "post", lambda *a, **k: _Resp(200, {"Result": {"WebResults": []}}))
    assert not dbs.doubao_search("x", "k")["ok"]


def test_api_key_sources(monkeypatch):
    monkeypatch.delenv("DOUBAO_SEARCH_API_KEY", raising=False)
    assert dbs.api_key({}) == "" and dbs.api_key(None) == ""
    assert dbs.api_key({"doubao_api_key": " c "}) == "c"
    monkeypatch.setenv("DOUBAO_SEARCH_API_KEY", "e")
    assert dbs.api_key({"doubao_api_key": "c"}) == "e"
