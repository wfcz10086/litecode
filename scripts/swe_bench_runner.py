#!/usr/bin/env python3
"""
swe_bench_runner.py — SWE-bench 50 题跑分对标 (P11 / P51)

P51 务实升级：
  - 真接 HuggingFace SWE-bench Verified dataset（lazy import）
  - --dry-run 模式：不真 clone，验证 manifest/scorer 链路
  - --report PATH：跑完输出 markdown 报告
  - 多层 fallback: HuggingFace → jsonl → MANIFEST 占位

真跑请用 litecodeext/tests/swe_bench.py（已有完整流程，需 docker + 大磁盘 + HF 访问）
"""
import sys, json, argparse, time
from pathlib import Path

ROOT = Path(__file__).parent.parent

# 占位 manifest (HuggingFace + jsonl 都不可用时的最终 fallback)
MANIFEST_50 = [
    {"id": f"swe_{i:02d}", "repo": "placeholder/repo",
     "issue": f"placeholder issue {i}", "difficulty": "medium"}
    for i in range(1, 51)
]


def load_manifest(subset: str = "verified", limit: int = 50,
                  jsonl_path: str = None) -> tuple[list, str]:
    """加载 SWE-bench manifest。
    返回 (instances, source) — source: 'huggingface' / 'jsonl' / 'placeholder'
    """
    # 1) 优先 HuggingFace
    try:
        from datasets import load_dataset
        name = "princeton-nlp/SWE-bench_Verified" if subset == "verified" \
               else "princeton-nlp/SWE-bench_Lite"
        ds = load_dataset(name, split="test")
        instances = []
        for i, row in enumerate(ds):
            if i >= limit:
                break
            instances.append(dict(row))
        if instances:
            return instances, "huggingface"
    except Exception as e:
        print(f"[swe_bench] HuggingFace load failed: {e}", file=sys.stderr)

    # 2) 本地 jsonl
    if jsonl_path:
        p = Path(jsonl_path)
        if p.exists():
            try:
                instances = []
                with p.open() as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        instances.append(json.loads(line))
                        if len(instances) >= limit:
                            break
                return instances, "jsonl"
            except Exception as e:
                print(f"[swe_bench] jsonl load failed: {e}", file=sys.stderr)

    # 3) 占位 fallback
    return MANIFEST_50[:limit], "placeholder"


def score_run(results: list) -> dict:
    """对结果打分"""
    n = len(results)
    if n == 0:
        return {"resolved": 0, "partial": 0, "failed": 0, "error": 0,
                "skipped": 0, "total": 0, "rate": 0.0}
    resolved = sum(1 for r in results if r.get("status") == "resolved")
    partial  = sum(1 for r in results if r.get("status") == "partial")
    failed   = sum(1 for r in results if r.get("status") == "failed")
    error    = sum(1 for r in results if r.get("status") == "error")
    skipped  = sum(1 for r in results if r.get("status") == "skipped")
    return {"resolved": resolved, "partial": partial, "failed": failed,
            "error": error, "skipped": skipped, "total": n,
            "rate": round(resolved / n * 100, 1)}


def run_dry(instances: list) -> list:
    """dry-run 模式：所有 instance 标 skipped"""
    return [{"id": inst.get("id") or inst.get("instance_id") or f"inst_{i}",
             "status": "skipped",
             "reason": "dry-run"} for i, inst in enumerate(instances)]


def write_report(path: Path, instances: list, results: list,
                 source: str, score: dict, dry_run: bool = False) -> None:
    """生成 markdown 报告"""
    path.parent.mkdir(parents=True, exist_ok=True)
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# SWE-bench Verified 跑分报告 (P51)",
        "",
        f"- 时间: {ts}",
        f"- Manifest source: **{source}**",
        f"- Mode: **{'dry-run' if dry_run else 'real'}**",
        f"- Instances: {score['total']}",
        f"- **Resolved: {score['resolved']}/{score['total']} ({score['rate']}%)**",
        f"- Partial: {score['partial']}  Failed: {score['failed']}  "
        f"Error: {score['error']}  Skipped: {score['skipped']}",
        "",
        "## 状态说明",
        "",
    ]
    if source == "placeholder":
        lines += [
            "- HuggingFace dataset 不可用（网络/datasets 库未装），fallback 到 MANIFEST 占位。",
            "- 安装真 dataset：`pip install datasets --break-system-packages`",
        ]
    elif dry_run:
        lines += [
            "- dry-run 模式：未真 clone/checkout/patch，所有 instance 标 skipped。",
            "- 真跑请用 `litecodeext/tests/swe_bench.py`（需 docker + 大磁盘 + HF 访问）。",
        ]
    else:
        lines += ["- 真跑模式：见下方 instance 明细。"]

    lines += [
        "",
        "## 已知限制",
        "",
        "- 真跑 50 题需要 ~30+ GB 磁盘（每仓库 clone 几百 MB）",
        "- 推荐 docker 模式避免环境污染",
        "- VM 历史尝试 1 题真跑失败（clone/checkout）—— 见 reports/swe_bench_real_1.md",
        "",
        "## Instance 列表",
        "",
        "| # | Instance | Status |",
        "|---|----------|--------|",
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | `{r['id']}` | {r['status']} |")
    path.write_text("\n".join(lines), encoding="utf-8")


def benchmark_baseline() -> dict:
    """SWE-bench Verified 已知 baseline (2025 数据)"""
    return {
        "OpenHands_Sonnet": 50.4,
        "SWE-agent_Sonnet": 33.6,
        "Aider_Sonnet": 26.3,
        "LiteCode (target)": "TBD",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subset", choices=["verified", "lite"], default="verified")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--jsonl", default=None, help="fallback 本地 jsonl 路径")
    ap.add_argument("--dry-run", action="store_true", help="不真 clone, 验证链路")
    ap.add_argument("--report", default=None, help="输出 markdown 报告路径")
    ap.add_argument("--output", default=None, help="(旧参数兼容) 同 --report")
    args = ap.parse_args()

    instances, source = load_manifest(args.subset, args.limit, args.jsonl)
    print(f"[swe_bench] loaded {len(instances)} instances from {source}")

    if args.dry_run:
        results = run_dry(instances)
    else:
        print("[swe_bench] real 跑请用 litecodeext/tests/swe_bench.py")
        print("[swe_bench] 此脚本作为骨架/dry-run 用途，自动转 dry-run")
        results = run_dry(instances)

    score = score_run(results)
    print(json.dumps(score, ensure_ascii=False, indent=2))

    report_path = args.report or args.output
    if report_path:
        write_report(Path(report_path), instances, results, source, score,
                     dry_run=args.dry_run)
        print(f"[swe_bench] report → {report_path}")


if __name__ == "__main__":
    main()
