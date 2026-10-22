"""大文件读取类工具回归 (2026-10-21): lib/large_file 行索引 / read_file 分段 / tail_log / search_code。

全部用本地临时文件, 不连任何服务。
"""
import asyncio
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core")]

import tool_dispatch as td  # noqa: E402,F401
from core.tools.handlers import REGISTRY  # noqa: E402
from lib import large_file as lf  # noqa: E402


def call(name, args):
    return asyncio.run(REGISTRY[name]("t-large", args))[0]


def _write_log(p: Path, n: int, width: int = 60) -> None:
    with open(p, "w") as f:
        for i in range(1, n + 1):
            f.write(f"L{i:07d} " + "x" * width + "\n")


# ───────── lib.large_file ─────────
def test_index_and_read_lines_match_reference(tmp_path, monkeypatch):
    """小块 (7 字节) 锚点下随机文件对拍: 行号、内容、超长行截断、末行无换行、\\r\\n。"""
    monkeypatch.setattr(lf, "CHUNK", 7)
    rnd = random.Random(20261021)
    for k in range(120):
        lines = ["".join(rnd.choice("ab中\r") for _ in range(rnd.randint(0, 25))) for _ in range(rnd.randint(0, 30))]
        data = "\n".join(lines) + rnd.choice(["", "\n"])
        p = tmp_path / f"f{k}.txt"
        p.write_text(data)
        exp = data.encode().split(b"\n")
        if data.endswith("\n"):
            exp = exp[:-1]
        if data == "":
            exp = []
        lf._cache.clear()
        assert lf.get_index(str(p)).total_lines == len(exp)
        for s in range(1, len(exp) + 2):
            got = lf.read_lines(str(p), s, 4, line_cap=10)
            want = []
            for a in range(s, min(s + 4, len(exp) + 1)):
                full = exp[a - 1]
                if len(full) > 10:
                    want.append((a, full[:10].decode("utf-8", "replace").rstrip("�"), len(full)))
                else:
                    full = full[:-1] if full.endswith(b"\r") else full
                    want.append((a, full.decode("utf-8", "replace"), len(full)))
            assert got == want


def test_index_incremental_on_append(tmp_path, monkeypatch):
    monkeypatch.setattr(lf, "_INCR_MIN", 0)
    p = tmp_path / "grow.log"
    _write_log(p, 1000)
    lf._cache.clear()
    assert lf.get_index(str(p)).total_lines == 1000
    calls = []
    orig = lf.build_index
    monkeypatch.setattr(lf, "build_index", lambda path, prev=None: (calls.append(prev is not None), orig(path, prev))[1])
    with open(p, "a") as f:
        f.write("tail-1\ntail-2")
    idx = lf.get_index(str(p))
    assert calls == [True] and idx.total_lines == 1002
    assert lf.read_lines(str(p), 1001, 5) == [(1001, "tail-1", 6), (1002, "tail-2", 6)]


def test_tail_backward_for_huge_file(tmp_path, monkeypatch):
    p = tmp_path / "huge.log"
    _write_log(p, 5000)
    lf._cache.clear()
    monkeypatch.setattr(lf, "TAIL_INDEX_LIMIT", 1000)      # 当作"超大且无索引"
    rows, idx = lf.tail_lines(str(p), 3)
    assert idx is None and [r[1][:8] for r in rows] == ["L0004998", "L0004999", "L0005000"]


# ───────── read_file ─────────
def test_read_file_small_unchanged_format(tmp_path):
    p = tmp_path / "s.py"
    p.write_text("a = 1\nb = 2\n")
    out = call("read_file", {"filepath": str(p)})
    assert out == "   1\ta = 1\n   2\tb = 2"


def test_read_file_over_5mb_segments_with_continuation(tmp_path):
    p = tmp_path / "big.log"
    _write_log(p, 100_000)                       # ≈7MB, 旧实现直接 ERROR
    assert p.stat().st_size > 5 * 1024 * 1024
    out = call("read_file", {"filepath": str(p)})
    assert "共 100000 行" in out and "本次第 1-200 行" in out and "还剩 99800 行" in out
    assert f"lines='201:400'" in out and "L0000200" in out and "L0000201" not in out
    assert len(out) < 25000
    deep = call("read_file", {"filepath": str(p), "lines": "88000:88010"})
    assert "88000\tL0088000" in deep and "88010\tL0088010" in deep and "还剩 11990 行" in deep
    last = call("read_file", {"filepath": str(p), "lines": "-3:"})
    assert "L0100000" in last and "L0099998" in last and "L0099997" not in last
    past = call("read_file", {"filepath": str(p), "lines": "200001:200010"})
    assert "超出末尾" in past


def test_read_file_output_budget_and_long_lines(tmp_path):
    p = tmp_path / "wide.txt"
    _write_log(p, 3000, width=300)
    out = call("read_file", {"filepath": str(p), "lines": "1:3000"})
    assert len(out) < 22000 and "因输出上限" in out
    stop = int(out.split("停在第 ")[1].split(" 行")[0])
    assert f"lines='{stop + 1}:" in out
    one = tmp_path / "min.json"
    one.write_text("{" + "\"k\":1," * 200_000 + "\"z\":0}")
    out = call("read_file", {"filepath": str(one)})
    assert "本行共" in out and len(out) < 4000


def test_read_file_binary_hints(tmp_path):
    x = tmp_path / "a.xlsx"
    x.write_bytes(b"PK\x03\x04")
    assert "pandas" in call("read_file", {"filepath": str(x)})
    b = tmp_path / "a.dat"
    b.write_bytes(b"\x00\x01" * 100)
    assert "binary" in call("read_file", {"filepath": str(b)})


def test_read_file_csv_hint(tmp_path):
    p = tmp_path / "d.csv"
    with open(p, "w") as f:
        f.write("id,v\n")
        for i in range(1000):
            f.write(f"{i},{i * 2}\n")
    assert "pandas" in call("read_file", {"filepath": str(p)})


# ───────── tail_log ─────────
def test_tail_log_header_and_caps(tmp_path):
    p = tmp_path / "app.log"
    _write_log(p, 50_000)
    with open(p, "a") as f:
        f.write("Z" * 50_000 + "\n")
    out = call("tail_log", {"path": str(p), "lines": 5})
    assert "共 50001 行" in out and "显示第 49997-50001 行" in out
    assert "本行共 50000" in out and "lines='49797:49996'" in out
    out = call("tail_log", {"path": str(p), "lines": 500})
    assert len(out) < 33000 and "丢了较早的" in out


# ───────── search_code ─────────
def test_search_code_streams_big_file_and_accepts_file_path(tmp_path):
    p = tmp_path / "big.log"
    _write_log(p, 200_000)
    with open(p, "a") as f:
        f.write("NEEDLE here\n")
    out = call("search_code", {"query": "NEEDLE", "path": str(tmp_path)})
    assert "big.log:200001: NEEDLE here" in out
    out = call("search_code", {"query": "NEEDLE", "path": str(p)})
    assert "200001" in out
