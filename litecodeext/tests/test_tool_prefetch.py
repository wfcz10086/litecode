"""只读工具预取: 只取批次开头连续的只读调用, 真并发, 参数不符不复用。"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.tool_args import parse_tool_args  # noqa: E402
from lib.tool_prefetch import Prefetcher, plan  # noqa: E402


def _tc(i, name, args=None, raw=None):
    return {"id": f"c{i}", "function": {"name": name, "arguments": raw if raw is not None else json.dumps(args or {})}}


def test_plan_takes_leading_read_only_prefix():
    calls = [_tc(1, "read_file", {"filepath": "a"}), _tc(2, "search_code", {"q": "x"}),
             _tc(3, "write_file", {"filepath": "a"}), _tc(4, "read_file", {"filepath": "a"})]
    assert [p[0] for p in plan(calls, parse_tool_args)] == ["c1", "c2"]


def test_plan_needs_at_least_two():
    assert plan([_tc(1, "read_file"), _tc(2, "execute_shell")], parse_tool_args) == []
    assert plan([_tc(1, "write_file"), _tc(2, "read_file"), _tc(3, "read_file")], parse_tool_args) == []


def test_plan_stops_at_bad_args():
    calls = [_tc(1, "read_file"), _tc(2, "read_file", raw="{bad"), _tc(3, "read_file")]
    assert plan(calls, parse_tool_args) == []


def test_runs_concurrently_and_take_returns_results():
    async def run(name, args):
        await asyncio.sleep(0.2)
        return f"{name}:{args.get('filepath')}", None

    async def main():
        calls = [_tc(i, "read_file", {"filepath": str(i)}) for i in range(3)]
        t0 = time.monotonic()
        p = Prefetcher.start(calls, parse_tool_args, run)
        assert len(p) == 3
        results = [await p.take(f"c{i}", {"filepath": str(i)}) for i in range(3)]
        return time.monotonic() - t0, results

    elapsed, results = asyncio.run(main())
    assert results == [("read_file:0", None), ("read_file:1", None), ("read_file:2", None)]
    assert elapsed < 0.45


def test_take_with_changed_args_falls_back():
    async def run(name, args):
        await asyncio.sleep(0.01)
        return "x", None

    async def main():
        p = Prefetcher.start([_tc(1, "read_file", {"filepath": "a"}), _tc(2, "git_status")],
                             parse_tool_args, run)
        assert p.take("c1", {"filepath": "b"}) is None
        assert p.take("nope", {}) is None
        p.cancel_rest()
        assert len(p) == 0

    asyncio.run(main())


def test_disabled_starts_nothing():
    async def main():
        return Prefetcher.start([_tc(1, "read_file"), _tc(2, "read_file")], parse_tool_args,
                                lambda n, a: asyncio.sleep(0), enabled=False)
    assert len(asyncio.run(main())) == 0
