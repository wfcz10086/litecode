"""MemoryManager.save: 同一段落里完全相同的条目不重复写入。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "litecodeext" / "core"))

from core.memory.manager import _section_has_entry  # noqa: E402

DOC = "# L1\n\n## User Intent\n- 用中文回答\n- 先给结论\n\n## Vault\n- token=abc\n"


def test_existing_entry_detected():
    assert _section_has_entry(DOC, "## User Intent", "- 用中文回答")


def test_entry_in_other_section_not_counted():
    assert not _section_has_entry(DOC, "## User Intent", "- token=abc")


def test_last_section_and_missing_heading():
    assert _section_has_entry(DOC, "## Vault", "- token=abc")
    assert not _section_has_entry(DOC, "## Completed Work", "- 用中文回答")


def test_new_entry_not_detected():
    assert not _section_has_entry(DOC, "## User Intent", "- 别用表格")
