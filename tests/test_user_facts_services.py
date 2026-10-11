"""core/memory/user_facts.extract_services: URL 不吞中文/Markdown, 结果去重。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "litecodeext"), str(ROOT / "litecodeext" / "core")]

from core.memory.user_facts import extract_services  # noqa: E402


def test_url_stops_at_markdown_and_cjk():
    msgs = [{"role": "assistant", "content": "**访问地址：http://localhost:48888/**（远程访问把 localhost 换成 IP）"}]
    assert extract_services(msgs) == ["http://localhost:48888/"]


def test_url_with_path_and_query_kept():
    msgs = [{"role": "tool", "content": "ok http://127.0.0.1:8000/api/v1?x=1&y=2 done"}]
    assert "http://127.0.0.1:8000/api/v1?x=1&y=2" in extract_services(msgs)


def test_duplicates_removed_in_order():
    msgs = [{"role": "tool", "content": "listening port 48888"},
            {"role": "tool", "content": "port 2508 and again port 48888"}]
    assert extract_services(msgs) == ["port 48888", "port 2508"]
