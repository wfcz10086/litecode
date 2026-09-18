#!/usr/bin/env python3
"""
analyze_telemetry.py — 离线分析 LiteCode 埋点 (P12-d)

读 WORKSPACE/telemetry/{skills,subagent,tools}.jsonl
出统计报告 (text / md / json)
"""
import argparse, json, sys, time, statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def parse_since(s):
    """'7d' / '24h' / '30m' / '0' -> 秒数 (0 表示全量)"""
    s = s.strip()
    if s == '0':
        return 0
    units = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}
    if s[-1] in units:
        try:
            return int(s[:-1]) * units[s[-1]]
        except ValueError:
            pass
    try:
        return int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"无法解析时间窗口: {s!r}")


def load_jsonl(path, since_ts):
    if not path.exists():
        return None
    rows = []
    for line in path.read_text(errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
            if r.get('ts', 0) >= since_ts:
                rows.append(r)
        except Exception:
            continue
    return rows


def _pct(n, total):
    return (n / total * 100) if total else 0


def analyze_skills(rows, top_n):
    if rows is None:
        return None
    total = len(rows)
    gated = sum(1 for r in rows if r.get('gated'))
    hit = sum(1 for r in rows if r.get('matched'))
    skill_counter = Counter()
    for r in rows:
        for m in r.get('matched', []):
            skill_counter[m.get('name', '?')] += 1
    return {
        'total': total,
        'hit': hit,
        'hit_rate': _pct(hit, total),
        'gated': gated,
        'gated_rate': _pct(gated, total),
        'top_skills': skill_counter.most_common(top_n),
    }


def analyze_subagent(rows, top_n):
    if rows is None:
        return None
    total = len(rows)
    ok = sum(1 for r in rows if r.get('status') == 'ok')
    timeout = sum(1 for r in rows if r.get('status') == 'timeout')
    error = sum(1 for r in rows if r.get('status') == 'error')

    durations_ms = [r['duration_ms'] for r in rows if isinstance(r.get('duration_ms'), (int, float))]
    avg_ms = statistics.mean(durations_ms) if durations_ms else None
    p95_ms = None
    if durations_ms:
        sorted_d = sorted(durations_ms)
        idx = max(0, int(len(sorted_d) * 0.95) - 1)
        p95_ms = sorted_d[idx]

    by_type = defaultdict(lambda: {'count': 0, 'ok': 0, 'timeout': 0, 'error': 0, 'durations_ms': []})
    for r in rows:
        at = r.get('agent_type', 'unknown')
        by_type[at]['count'] += 1
        status = r.get('status', '')
        if status in ('ok', 'timeout', 'error'):
            by_type[at][status] += 1
        if isinstance(r.get('duration_ms'), (int, float)):
            by_type[at]['durations_ms'].append(r['duration_ms'])

    type_summary = []
    for at, d in sorted(by_type.items(), key=lambda x: -x[1]['count'])[:top_n]:
        avg = statistics.mean(d['durations_ms']) if d['durations_ms'] else None
        type_summary.append({
            'agent_type': at,
            'count': d['count'],
            'ok': d['ok'],
            'timeout': d['timeout'],
            'error': d['error'],
            'avg_ms': avg,
        })

    return {
        'total': total,
        'ok': ok,
        'timeout': timeout,
        'error': error,
        'timeout_rate': _pct(timeout, total),
        'error_rate': _pct(error, total),
        'avg_ms': avg_ms,
        'p95_ms': p95_ms,
        'by_type': type_summary,
    }


def analyze_tools(rows, top_n):
    if rows is None:
        return None
    total = len(rows)
    errors_total = sum(1 for r in rows if r.get('error'))

    by_tool = defaultdict(lambda: {'count': 0, 'errors': 0, 'durations_ms': []})
    error_counter = Counter()
    for r in rows:
        tn = r.get('tool_name', 'unknown')
        by_tool[tn]['count'] += 1
        if r.get('error'):
            by_tool[tn]['errors'] += 1
            err_str = str(r['error'])[:80]
            error_counter[err_str] += 1
        if isinstance(r.get('duration_ms'), (int, float)):
            by_tool[tn]['durations_ms'].append(r['duration_ms'])

    tool_summary = []
    for tn, d in sorted(by_tool.items(), key=lambda x: -x[1]['count'])[:top_n]:
        avg = statistics.mean(d['durations_ms']) if d['durations_ms'] else None
        tool_summary.append({
            'tool_name': tn,
            'count': d['count'],
            'errors': d['errors'],
            'avg_ms': avg,
        })

    return {
        'total': total,
        'errors': errors_total,
        'error_rate': _pct(errors_total, total),
        'top_tools': tool_summary,
        'top_errors': error_counter.most_common(top_n),
    }


def _fmt_ms(ms):
    if ms is None:
        return 'n/a'
    if ms >= 1000:
        return f'{ms/1000:.1f}s'
    return f'{ms:.0f}ms'


def render_text(report):
    lines = []
    lines.append('=== LiteCode Telemetry Report ===')
    since_ts = report.get('since_ts', 0)
    window = report.get('window', '?')
    if since_ts:
        dt = datetime.fromtimestamp(since_ts).strftime('%Y-%m-%d %H:%M')
        lines.append(f'Window: last {window} (since {dt})')
    else:
        lines.append('Window: all time')
    lines.append(f'Workspace: {report.get("workspace", "?")}')
    lines.append('')

    sk = report.get('skills')
    lines.append('-- Skills --')
    if sk is None:
        lines.append('  (no data)')
    else:
        lines.append(f'  total calls         : {sk["total"]}')
        lines.append(f'  hit rate            : {sk["hit_rate"]:.1f}% ({sk["hit"]} with match)')
        lines.append(f'  gated (skipped)     : {sk["gated"]} ({sk["gated_rate"]:.1f}%)')
        if sk['top_skills']:
            lines.append(f'  top {len(sk["top_skills"])} skill triggers:')
            for name, cnt in sk['top_skills']:
                lines.append(f'    {name:<28} {cnt}')
        else:
            lines.append('  top skill triggers  : (none)')
    lines.append('')

    sa = report.get('subagent')
    lines.append('-- Subagent --')
    if sa is None:
        lines.append('  (no data)')
    else:
        lines.append(f'  total calls         : {sa["total"]}')
        lines.append(
            f'  ok / timeout / error: {sa["ok"]} / {sa["timeout"]} / {sa["error"]}'
            f' ({sa["timeout_rate"]:.1f}% timeout rate)'
        )
        lines.append(f'  avg duration        : {_fmt_ms(sa["avg_ms"])}')
        lines.append(f'  p95 duration        : {_fmt_ms(sa["p95_ms"])}')
        if sa['by_type']:
            lines.append('  by agent_type:')
            for d in sa['by_type']:
                lines.append(
                    f'    {d["agent_type"]:<14} {d["count"]:>4}'
                    f'  avg {_fmt_ms(d["avg_ms"]):<8}'
                    f'  ok {d["ok"]}  timeout {d["timeout"]}  error {d["error"]}'
                )
    lines.append('')

    tl = report.get('tools')
    lines.append('-- Tools --')
    if tl is None:
        lines.append('  (no data)')
    else:
        lines.append(f'  total calls         : {tl["total"]}')
        lines.append(f'  error rate          : {tl["error_rate"]:.1f}% ({tl["errors"]} errors)')
        if tl['top_tools']:
            lines.append(f'  top {len(tl["top_tools"])} tools:')
            for d in tl['top_tools']:
                lines.append(
                    f'    {d["tool_name"]:<20} {d["count"]:>5}'
                    f'  avg {_fmt_ms(d["avg_ms"]):<9}'
                    f'  err {d["errors"]}'
                )
        if tl['top_errors']:
            lines.append('  top errors:')
            for msg, cnt in tl['top_errors']:
                lines.append(f'    {msg:<48} {cnt}')
    return '\n'.join(lines)


def render_md(report):
    lines = []
    lines.append('# LiteCode Telemetry Report')
    since_ts = report.get('since_ts', 0)
    window = report.get('window', '?')
    if since_ts:
        dt = datetime.fromtimestamp(since_ts).strftime('%Y-%m-%d %H:%M')
        lines.append(f'**Window:** last {window} (since {dt})')
    else:
        lines.append('**Window:** all time')
    lines.append(f'**Workspace:** `{report.get("workspace", "?")}`')
    lines.append('')

    sk = report.get('skills')
    lines.append('## Skills')
    if sk is None:
        lines.append('_(no data)_')
    else:
        lines.append(f'- total calls: **{sk["total"]}**')
        lines.append(f'- hit rate: **{sk["hit_rate"]:.1f}%** ({sk["hit"]} with match)')
        lines.append(f'- gated: {sk["gated"]} ({sk["gated_rate"]:.1f}%)')
        if sk['top_skills']:
            lines.append('')
            lines.append('| Skill | Triggers |')
            lines.append('|---|---|')
            for name, cnt in sk['top_skills']:
                lines.append(f'| {name} | {cnt} |')
    lines.append('')

    sa = report.get('subagent')
    lines.append('## Subagent')
    if sa is None:
        lines.append('_(no data)_')
    else:
        lines.append(f'- total calls: **{sa["total"]}**')
        lines.append(
            f'- ok / timeout / error: {sa["ok"]} / {sa["timeout"]} / {sa["error"]}'
            f' ({sa["timeout_rate"]:.1f}% timeout rate)'
        )
        lines.append(f'- avg duration: {_fmt_ms(sa["avg_ms"])}')
        lines.append(f'- p95 duration: {_fmt_ms(sa["p95_ms"])}')
        if sa['by_type']:
            lines.append('')
            lines.append('| agent_type | count | avg | ok | timeout | error |')
            lines.append('|---|---|---|---|---|---|')
            for d in sa['by_type']:
                lines.append(
                    f'| {d["agent_type"]} | {d["count"]} | {_fmt_ms(d["avg_ms"])}'
                    f' | {d["ok"]} | {d["timeout"]} | {d["error"]} |'
                )
    lines.append('')

    tl = report.get('tools')
    lines.append('## Tools')
    if tl is None:
        lines.append('_(no data)_')
    else:
        lines.append(f'- total calls: **{tl["total"]}**')
        lines.append(f'- error rate: **{tl["error_rate"]:.1f}%** ({tl["errors"]} errors)')
        if tl['top_tools']:
            lines.append('')
            lines.append('| tool | count | avg duration | errors |')
            lines.append('|---|---|---|---|')
            for d in tl['top_tools']:
                lines.append(
                    f'| {d["tool_name"]} | {d["count"]} | {_fmt_ms(d["avg_ms"])} | {d["errors"]} |'
                )
        if tl['top_errors']:
            lines.append('')
            lines.append('### Top Errors')
            lines.append('| error | count |')
            lines.append('|---|---|')
            for msg, cnt in tl['top_errors']:
                lines.append(f'| {msg} | {cnt} |')
    return '\n'.join(lines)


def render_json(report):
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)


def main():
    p = argparse.ArgumentParser(description='Analyze LiteCode telemetry')
    p.add_argument('--since', default='7d',
                   help='Time window: 7d / 24h / 30m / 0=all (default: 7d)')
    p.add_argument('--output', choices=['text', 'md', 'json'], default='text',
                   help='Output format (default: text)')
    p.add_argument('--workspace', default=None,
                   help='Workspace base dir (default: from lib.config / /tmp/openclaw_workspace)')
    p.add_argument('--top', type=int, default=10,
                   help='Top N items per category (default: 10)')
    args = p.parse_args()

    if args.workspace:
        ws = Path(args.workspace)
    else:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / 'litecodeext'))
            from lib.config import WORKSPACE as ws
        except Exception:
            ws = Path('/tmp/openclaw_workspace')

    tdir = ws / 'telemetry'
    since_sec = parse_since(args.since)
    since_ts = time.time() - since_sec if since_sec else 0

    skills_rep = analyze_skills(load_jsonl(tdir / 'skills.jsonl', since_ts), args.top)
    subagent_rep = analyze_subagent(load_jsonl(tdir / 'subagent.jsonl', since_ts), args.top)
    tools_rep = analyze_tools(load_jsonl(tdir / 'tools.jsonl', since_ts), args.top)

    report = {
        'window': args.since,
        'since_ts': since_ts,
        'workspace': str(ws),
        'skills': skills_rep,
        'subagent': subagent_rep,
        'tools': tools_rep,
    }

    if args.output == 'text':
        print(render_text(report))
    elif args.output == 'md':
        print(render_md(report))
    else:
        print(render_json(report))


if __name__ == '__main__':
    main()
