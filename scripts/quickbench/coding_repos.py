"""编码题夹具: 8 个迷你仓库 (源码 + tests/ + run_tests.py)。

每个仓库是 {相对路径: 文件内容}。测试是普通 assert 函数, 既能被宿主 pytest 收集,
也能被 run_tests.py 在没有 pytest 的容器里直接跑 (容器里没装 pytest, 不让 agent 去 pip 装)。
判分在宿主机 pytest 上跑, 并校验 tests/ 与 run_tests.py 的哈希没变。
"""
from __future__ import annotations

RUN_TESTS = '''"""极简测试运行器 (容器里没有 pytest)。用法: python3 run_tests.py"""
import importlib.util
import pathlib
import sys
import traceback

root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))
total = fails = 0
for f in sorted((root / "tests").glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(f.stem, f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in sorted(dir(mod)):
        fn = getattr(mod, name)
        if name.startswith("test_") and callable(fn):
            total += 1
            try:
                fn()
            except Exception:
                fails += 1
                print(f"FAIL {f.name}::{name}")
                traceback.print_exc(limit=3)
print(f"{total - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
'''

_HDR = "import os, sys\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"

REPOS: dict[str, dict] = {
    # 1) off-by-one ×2
    "c_offbyone": {
        "desc": "off-by-one (分页)",
        "files": {
            "pager.py": '''import math  # noqa: F401


def paginate(items, page, size):
    """Return the items on 1-based page `page` (size items per page). page < 1 or past the end -> []."""
    if page < 1:
        return []
    start = page * size
    return items[start:start + size]


def total_pages(n_items, size):
    """Number of pages needed to show n_items with `size` items per page (0 items -> 0 pages)."""
    return n_items // size
''',
            "tests/test_pager.py": _HDR + '''from pager import paginate, total_pages


def test_first_page():
    assert paginate(list(range(10)), 1, 3) == [0, 1, 2]


def test_last_page():
    assert paginate(list(range(10)), 4, 3) == [9]


def test_out_of_range():
    assert paginate(list(range(10)), 5, 3) == []
    assert paginate(list(range(10)), 0, 3) == []


def test_total_pages():
    assert total_pages(10, 3) == 4
    assert total_pages(9, 3) == 3
    assert total_pages(0, 3) == 0
''',
        },
    },
    # 2) wrong condition
    "c_condition": {
        "desc": "条件写错 (闰年/投票)",
        "files": {
            "rules.py": '''def is_leap(year):
    """Gregorian leap year."""
    return year % 4 == 0 and (year % 100 != 0 and year % 400 == 0)


def can_vote(age, is_citizen):
    """Voting requires being at least 18 AND a citizen."""
    return age >= 18 or is_citizen
''',
            "tests/test_rules.py": _HDR + '''from rules import is_leap, can_vote


def test_leap():
    assert is_leap(2024)
    assert is_leap(2000)
    assert not is_leap(1900)
    assert not is_leap(2023)


def test_vote():
    assert can_vote(18, True)
    assert not can_vote(17, True)
    assert not can_vote(30, False)
''',
        },
    },
    # 3) missing edge case
    "c_edgecase": {
        "desc": "缺边界 (空列表/偶数中位数)",
        "files": {
            "stats.py": '''def mean(xs):
    """Arithmetic mean. Empty input -> ValueError("empty")."""
    return sum(xs) / len(xs)


def median(xs):
    """Median; for even length it is the average of the two middle values. Empty -> ValueError("empty")."""
    s = sorted(xs)
    return s[len(s) // 2]
''',
            "tests/test_stats.py": _HDR + '''from stats import mean, median


def _raises_value_error(fn, arg):
    try:
        fn(arg)
    except ValueError:
        return True
    except Exception:
        return False
    return False


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5


def test_median_odd():
    assert median([3, 1, 2]) == 2


def test_median_even():
    assert median([4, 1, 3, 2]) == 2.5


def test_empty():
    assert _raises_value_error(mean, [])
    assert _raises_value_error(median, [])
''',
        },
    },
    # 4) wrong API use
    "c_api": {
        "desc": "API 误用 (rstrip/split)",
        "files": {
            "textutil.py": '''def strip_ext(filename):
    """Remove a trailing '.txt' extension if present: 'data.txt' -> 'data', 'notes' -> 'notes'."""
    return filename.rstrip(".txt")


def count_words(text):
    """Number of whitespace-separated words (any run of spaces/tabs/newlines is one separator)."""
    if not text:
        return 0
    return len(text.split(" "))
''',
            "tests/test_textutil.py": _HDR + '''from textutil import strip_ext, count_words


def test_strip_ext():
    assert strip_ext("data.txt") == "data"
    assert strip_ext("dataset.txt") == "dataset"
    assert strip_ext("context") == "context"
    assert strip_ext("a.txt.bak") == "a.txt.bak"


def test_count_words():
    assert count_words("one two three") == 3
    assert count_words("  a   b\\nc\\t d  ") == 4
    assert count_words("") == 0
    assert count_words("   ") == 0
''',
        },
    },
    # 5) bug spanning two files
    "c_twofile": {
        "desc": "跨两个文件的 bug (舍入+数量)",
        "files": {
            "money.py": '''def to_cents(amount):
    """Convert a float amount in dollars to integer cents, rounding to the nearest cent."""
    return int(amount * 100)
''',
            "invoice.py": '''from money import to_cents


def line_total(item):
    """item = {'price': float dollars, 'qty': int}; returns cents for the whole line."""
    return to_cents(item["price"])


def invoice_total(items):
    """Total in cents."""
    return sum(line_total(i) for i in items)
''',
            "tests/test_invoice.py": _HDR + '''from money import to_cents
from invoice import invoice_total


def test_to_cents():
    assert to_cents(0.29) == 29
    assert to_cents(19.99) == 1999
    assert to_cents(1.005 + 0.0001) == 101


def test_invoice_total():
    items = [{"price": 0.29, "qty": 3}, {"price": 1.10, "qty": 2}, {"price": 4.35, "qty": 1}]
    assert invoice_total(items) == 87 + 220 + 435
''',
        },
    },
    # 6) performance bug
    "c_perf": {
        "desc": "性能 bug (O(n^2) 去重)",
        "files": {
            "dedupe.py": '''def dedupe(seq):
    """Return the items of seq with duplicates removed, keeping first-occurrence order.
    Items are hashable. Must handle 100k items well under a second."""
    result = []
    for x in seq:
        if x not in result:
            result.append(x)
    return result
''',
            "tests/test_dedupe.py": _HDR + '''import time
from dedupe import dedupe


def test_order():
    assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]
    assert dedupe([]) == []


def test_fast_enough():
    data = list(range(15000)) * 2
    t = time.perf_counter()
    out = dedupe(data)
    elapsed = time.perf_counter() - t
    assert out == list(range(15000))
    assert elapsed < 0.3, f"too slow: {elapsed:.2f}s"
''',
        },
    },
    # 7) parsing bug
    "c_parse": {
        "desc": "解析 bug (CSV 引号/尾空字段)",
        "files": {
            "csvline.py": '''def parse_csv_line(line):
    """Split one CSV line (RFC 4180 subset):
    - fields are separated by commas
    - a field may be wrapped in double quotes; inside quotes commas are literal
    - inside a quoted field, a doubled quote "" stands for one literal quote
    - an empty trailing field is kept: 'a,b,' -> ['a', 'b', '']
    """
    fields, cur, inq = [], "", False
    for ch in line:
        if ch == '"':
            inq = not inq
        elif ch == "," and not inq:
            fields.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        fields.append(cur)
    return fields
''',
            "tests/test_csvline.py": _HDR + '''from csvline import parse_csv_line


def test_simple():
    assert parse_csv_line("a,b,c") == ["a", "b", "c"]


def test_quoted_comma():
    assert parse_csv_line('a,"b,c",d') == ["a", "b,c", "d"]


def test_doubled_quote():
    assert parse_csv_line('"say ""hi""",x') == ['say "hi"', "x"]


def test_trailing_empty():
    assert parse_csv_line("a,b,") == ["a", "b", ""]
    assert parse_csv_line(",") == ["", ""]
''',
        },
    },
    # 8) small feature from docstring spec
    "c_feature": {
        "desc": "按 docstring 实现小功能 (slugify)",
        "files": {
            "slug.py": '''def slugify(text, max_length=None):
    """Turn text into a URL slug.

    Rules:
    1. Accented Latin letters are folded to ASCII ("Café" -> "cafe"); other non-ASCII chars are dropped.
    2. Lowercase everything.
    3. Every run of characters that are not [a-z0-9] becomes a single "-".
    4. No leading or trailing "-".
    5. If max_length is given and the slug is longer, cut it to at most max_length characters
       at a "-" boundary (drop the partial last word); never end with "-". If the first word alone
       is longer than max_length, hard-cut the first word to max_length.
    """
    raise NotImplementedError
''',
            "tests/test_slug.py": _HDR + '''from slug import slugify


def test_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_accents():
    assert slugify("Café au Lait") == "cafe-au-lait"
    assert slugify("Ünïcödé 中文 test") == "unicode-test"


def test_runs_and_edges():
    assert slugify("  --Multiple   spaces__and--dashes--  ") == "multiple-spaces-and-dashes"
    assert slugify("!!!") == ""


def test_max_length():
    assert slugify("the quick brown fox", max_length=12) == "the-quick"
    assert slugify("the quick brown fox", max_length=9) == "the-quick"
    assert slugify("supercalifragilistic", max_length=5) == "super"
    assert slugify("short", max_length=50) == "short"
''',
        },
    },
}
