"""CLI 退出码/边界专属测试 (cli.py)。

覆盖 2026-09-05 修的 CLI 边界:
  - 空 / 纯空格 --query → exit 2 + 提示 (原先静默落到 stdin 分支 exit 0)
  - --query 连不上 server → exit 1 (原先无论成败都 exit 0, 脚本 `&&` 会误判成功)
  - --check 连不上 server → exit 1 (健康探针语义)

这些用例都不需要真实模型 (坏 server / 空输入是确定性的), 可离线跑。
用法: python3 tests/test_cli_exit_codes.py
"""
import subprocess
import sys
from pathlib import Path

CLI = str(Path(__file__).resolve().parent.parent / "cli.py")
DEAD = "http://127.0.0.1:59999"  # 无人监听的端口 → 必然连不上
P = {"pass": 0, "fail": 0}


def _run(args, timeout=30):
    r = subprocess.run([sys.executable, CLI, *args],
                       capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


def ck(name, cond, extra=""):
    ok = bool(cond)
    P["pass" if ok else "fail"] += 1
    print(f"  {'✓' if ok else '✗'} {name}" + (f"   {extra}" if not ok and extra else ""))


def main():
    print("=== CLI 退出码/边界 ===")

    rc, out = _run(["--query", ""])
    ck("空 --query → exit 2", rc == 2, f"rc={rc}")
    ck("空 --query 有提示文案", "空查询" in out or "empty query" in out, out[:80])

    rc, _ = _run(["--query", "   "])
    ck("纯空格 --query → exit 2", rc == 2, f"rc={rc}")

    rc, out = _run(["--query", "", "--output-format", "stream-json"])
    ck("空 --query stream-json → exit 2 + error json",
       rc == 2 and '"error"' in out, f"rc={rc} out={out[:80]}")

    rc, out = _run(["--query", "hi", "--server", DEAD])
    ck("坏 server + query → exit 1 (非 0)", rc == 1, f"rc={rc}")

    rc, _ = _run(["--check", "--server", DEAD])
    ck("坏 server + --check → exit 1", rc == 1, f"rc={rc}")

    n = P["pass"] + P["fail"]
    print(f"\n=== {P['pass']}/{n} 通过 ===")
    return 0 if P["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
