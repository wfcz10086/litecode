#!/usr/bin/env python3
"""
test_historic_failures.py — P35-c 历史失败案例永久回归测试

把自治迭代过程中遇到的真实失败/缺口转为永久回归 case，
防止旧 bug 复发或修复被无意中删除。

数据源: tests/regression/historic_failures.jsonl
每条 case 含 id/error_type/summary/fix_hint/check/fixed_at。

check 类型:
- grep_present: 文件中必须含 pattern
- grep_absent: 文件中不允许含 pattern
- grep_count_eq: pattern 命中数应符合预期
- file_exists: 文件必须存在
- todo: 已知 pre-existing 问题, 仅记录, 不真跑（移交后续）

跑: python3 tests/regression/test_historic_failures.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
JSONL = Path(__file__).resolve().parent / "historic_failures.jsonl"


def _load_cases() -> list[dict]:
    cases = []
    if not JSONL.exists():
        return cases
    for line in JSONL.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"  WARN 跳过无效行: {e}", file=sys.stderr)
    return cases


def _check_grep_present(check: dict) -> tuple[bool, str]:
    path = ROOT / check["path"]
    pattern = check["pattern"]
    if not path.exists():
        # 路径可能是目录，递归 grep
        if (ROOT / check["path"].rstrip("/")).is_dir():
            cmd = ["grep", "-rE", pattern, str(ROOT / check["path"])]
            r = subprocess.run(cmd, capture_output=True, text=True)
            return (r.returncode == 0, f"目录 grep: {r.stdout[:80]}")
        return False, f"路径不存在: {path}"
    if path.is_dir():
        cmd = ["grep", "-rE", pattern, str(path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return (r.returncode == 0, f"目录 grep: {r.stdout[:80]}")
    text = path.read_text(errors="replace")
    if re.search(pattern, text):
        return True, "命中"
    return False, f"未找到 pattern: {pattern[:60]}"


def _check_grep_absent(check: dict) -> tuple[bool, str]:
    path = ROOT / check["path"]
    pattern = check["pattern"]
    if not path.exists():
        return True, "路径不存在 → 视为 absent OK"
    if path.is_dir():
        cmd = ["grep", "-rE", pattern, str(path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return False, f"不应存在但找到: {r.stdout[:80]}"
        return True, "目录 grep 无命中"
    text = path.read_text(errors="replace")
    if re.search(pattern, text):
        return False, f"不应存在但找到: {pattern[:60]}"
    return True, "未找到 (符合预期)"


def _check_grep_absent_in_py(check: dict) -> tuple[bool, str]:
    """grep_absent 但仅扫描 .py 文件（排除 .jsonl/.md/.txt 等数据/文档）。"""
    path = ROOT / check["path"]
    pattern = check["pattern"]
    if not path.exists():
        return True, "路径不存在 → 视为 absent OK"
    if path.is_dir():
        cmd = ["grep", "-rE", "--include=*.py", pattern, str(path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return False, f"不应存在但找到 (.py): {r.stdout[:80]}"
        return True, "目录 .py grep 无命中"
    if path.suffix != ".py":
        return True, f"非 .py 文件 → 跳过"
    text = path.read_text(errors="replace")
    if re.search(pattern, text):
        return False, f".py 中不应存在但找到: {pattern[:60]}"
    return True, "未找到 (符合预期)"


def _check_grep_count_eq(check: dict) -> tuple[bool, str]:
    path = ROOT / check["path"]
    pattern = check["pattern"]
    if not path.exists():
        return False, f"路径不存在: {path}"
    text = path.read_text(errors="replace")
    matches = re.findall(pattern, text)
    expected = check.get("count", 1)
    if len(matches) >= expected:
        return True, f"命中 {len(matches)} 次 (≥ {expected})"
    return False, f"命中 {len(matches)} 次，期望 ≥ {expected}"


def _check_file_exists(check: dict) -> tuple[bool, str]:
    path = ROOT / check["path"]
    return (path.exists(), f"file: {path}")


def _check_todo(check: dict) -> tuple[bool, str]:
    note = check.get("note", "")
    moved = check.get("moved_to", "")
    return True, f"⚠️ TODO 已记录, 移交 {moved}: {note}"


CHECKERS = {
    "grep_present": _check_grep_present,
    "grep_absent": _check_grep_absent,
    "grep_absent_in_py": _check_grep_absent_in_py,
    "grep_count_eq": _check_grep_count_eq,
    "file_exists": _check_file_exists,
    "todo": _check_todo,
}


def main():
    cases = _load_cases()
    if not cases:
        print("ERROR: 没有 historic_failures.jsonl 数据")
        sys.exit(1)

    pass_count = 0
    fail_count = 0
    todo_count = 0
    print(f"=== P35-c 历史失败案例永久回归 ({len(cases)} cases) ===\n")
    for case in cases:
        cid = case.get("id", "?")
        et = case.get("error_type", "?")
        summary = case.get("summary", "")[:60]
        check = case.get("check", {})
        ctype = check.get("type", "")

        if ctype not in CHECKERS:
            print(f"  ❌ {cid} [{et}]: 未知 check type: {ctype}")
            fail_count += 1
            continue

        try:
            ok, msg = CHECKERS[ctype](check)
        except Exception as e:
            ok, msg = False, f"check 异常: {type(e).__name__}: {e}"

        if ctype == "todo":
            print(f"  ⚠️  {cid} [{et}] (TODO)")
            print(f"      {summary}")
            print(f"      {msg}")
            todo_count += 1
        elif ok:
            print(f"  ✅ {cid} [{et}]")
            print(f"      {summary}")
            print(f"      check: {msg}")
            pass_count += 1
        else:
            print(f"  ❌ {cid} [{et}] — 修复疑似回退！")
            print(f"      summary: {summary}")
            print(f"      fix_hint: {case.get('fix_hint', '')[:80]}")
            print(f"      fixed_at: {case.get('fixed_at', '')}")
            print(f"      check 失败: {msg}")
            fail_count += 1

    total = pass_count + fail_count
    print(f"\nResults: {pass_count}/{total} PASS ({todo_count} TODO 移交后续)")
    if fail_count > 0:
        print(f"\n❌ {fail_count} 个历史 case 检查失败 — 可能修复被回退或文件被删")
    sys.exit(0 if fail_count == 0 else 1)


if __name__ == "__main__":
    main()
