#!/usr/bin/env python3
"""scripts/prefix_breakdown.py — 拆解一次模型请求的固定前缀 (系统提示 + 工具定义) 各部分 token。

用法 (在 litecodeext/ 下):
  python3 scripts/prefix_breakdown.py                       # 默认样例消息, 表格输出
  python3 scripts/prefix_breakdown.py -m "写个素数脚本并运行"  # 指定用户消息
  python3 scripts/prefix_breakdown.py --json > a.json       # 机器可读 (改前/改后对比用)
  python3 scripts/prefix_breakdown.py --tools 30            # 列出最大的 30 个工具 schema
  python3 scripts/prefix_breakdown.py --all-tools           # 按"全部工具都发"算 (= 关掉按需暴露)

两种口径:
  est  = lib.config.est_tokens (中文 1 字 1 t, 其余 3 字符/t, 偏高估), 与 [USAGE-ANCHOR] 日志同口径;
  bpe  = tiktoken o200k_base (装了 tiktoken 才有), 接近 Qwen/GLM 这类 15 万词表 BPE 的真实计数。
只读: 不连模型、不写会话 (用一个不存在的会话 id, 会话模板走 workspace_template)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

from lib.config import est_tokens  # noqa: E402
from lib import prompt as P  # noqa: E402
from lib import skills as S  # noqa: E402
try:
    from lib import tool_groups as TG  # noqa: E402
except ImportError:   # 旧版本没有按需暴露 (拷到旧代码树里做改前对比时)
    TG = None
from lib.tools.defs import TOOL_DEFS  # noqa: E402

if not S._SKILL_INDEX:   # 服务进程由 core/tool_dispatch 建索引; 脚本里自己建
    S._build_skill_index()

DEFAULT_MSG = "写一个 python 脚本, 打印 100 以内的所有素数并运行验证"
_SID = "prefix-breakdown-nonexistent"

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("o200k_base")
except Exception:   # 没装 / 离线拿不到编码表
    _ENC = None


def bpe(text: str) -> int:
    return len(_ENC.encode(text, disallowed_special=())) if (_ENC and text) else 0


def _sections(md: str, level: str = "## ") -> list[tuple[str, str]]:
    """按二级标题切 markdown; 首段 (第一个标题前) 记作 '(开头)'。"""
    out, cur, buf = [], "(开头)", []
    for line in md.splitlines(keepends=True):
        if line.startswith(level):
            out.append((cur, "".join(buf)))
            cur, buf = line.strip()[len(level):][:40], [line]
        else:
            buf.append(line)
    out.append((cur, "".join(buf)))
    return [(k, v) for k, v in out if v.strip()]


def _row(name: str, text: str) -> tuple[str, int, int]:
    return (name, est_tokens(text), bpe(text))


def sent_tools(user_message: str, all_tools: bool = False) -> list:
    """首轮请求实际会发的工具 (按需暴露开启时 = 核心 + 用户消息触发的组)。"""
    if all_tools or TG is None or not TG.enabled():
        return TOOL_DEFS
    return TG.select(TOOL_DEFS, TG.detect_user(user_message))


def measure(user_message: str, all_tools: bool = False) -> dict:
    sys_prompt = P.build_request_system_prompt(user_message, session_id=_SID, include_dynamic=False)
    rows: list = []
    tpl = P.PROMPT_FILE.read_text(errors="replace") if P.PROMPT_FILE.exists() else ""
    for title, body in _sections(tpl):
        rows.append(_row(f"AGENT_PROMPT § {title}", body))
    rows.append(_row("{{ENV}} 环境探测", P._get_env_probe()))
    rows.append(_row("{{SKILLS}} 技能清单", S.load_skills_index()))
    if "{{SEARCH}}" in tpl:
        rows.append(_row("{{SEARCH}} prompts/SEARCH.md", P._load_prompt_md("SEARCH.md")))
    for name in ("RULES", "PLANNING", "CODING", "OUTPUT"):
        if "{{%s}}" % name in tpl:
            rows.append(_row(f"{{{{{name}}}}} prompts/{name}.md", P._load_prompt_md(f"{name}.md")))
    for chunk in P._load_session_context(_SID).split("\n\n---\n\n"):
        if chunk.strip():
            rows.append(_row(f"会话模板: {chunk.strip().splitlines()[0][:40]}", chunk))
    rows.append(_row("网络环境提示", P._network_env_hint()))
    rows.append(_row("superpowers 元规则", S._load_superpowers_core()))

    tools = sent_tools(user_message, all_tools)
    tools_json = json.dumps(tools, ensure_ascii=False)
    tool_rows = sorted((_row(t["function"]["name"], json.dumps(t, ensure_ascii=False)) for t in TOOL_DEFS),
                       key=lambda r: -r[1])
    sent_names = {t["function"]["name"] for t in tools}
    group_rows = []
    for g, spec in (TG.GROUPS.items() if TG else ()):
        defs = [t for t in TOOL_DEFS if t["function"]["name"] in spec["tools"]]
        group_rows.append(_row(f"组 {g} ({len(defs)} 个){' [本轮已发]' if spec['tools'][0] in sent_names else ''}",
                               json.dumps(defs, ensure_ascii=False)))
    return {
        "user_message": user_message,
        "system_prompt": {"est": est_tokens(sys_prompt), "bpe": bpe(sys_prompt)},
        "tool_defs_sent": {"est": est_tokens(tools_json), "bpe": bpe(tools_json), "count": len(tools)},
        "tool_defs_all": {"est": est_tokens(json.dumps(TOOL_DEFS, ensure_ascii=False)),
                          "bpe": bpe(json.dumps(TOOL_DEFS, ensure_ascii=False)), "count": len(TOOL_DEFS)},
        "fixed_total": {"est": est_tokens(sys_prompt) + est_tokens(tools_json),
                        "bpe": bpe(sys_prompt) + bpe(tools_json)},
        "system_parts": sorted(rows, key=lambda r: -r[1]),
        "tool_groups": group_rows,
        "tools": tool_rows,
        "dynamic": [_row("委派指令 (delegation)", P._detect_auto_delegation(user_message)),
                    _row("自动匹配技能 (skills)", P.matched_skills_block(user_message))],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-m", "--message", default=DEFAULT_MSG)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tools", type=int, default=20, help="列出最大的 N 个工具")
    ap.add_argument("--all-tools", action="store_true", help="按全部工具都发计算")
    a = ap.parse_args()
    r = measure(a.message, a.all_tools)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    f, s, t = r["fixed_total"], r["system_prompt"], r["tool_defs_sent"]
    print(f"用户消息: {r['user_message']}")
    print(f"固定前缀合计  est {f['est']} t / bpe {f['bpe']} t")
    print(f"  系统提示    est {s['est']} t / bpe {s['bpe']} t")
    print(f"  工具定义    est {t['est']} t / bpe {t['bpe']} t  (发送 {t['count']} 个,"
          f" 全量 {r['tool_defs_all']['count']} 个 = est {r['tool_defs_all']['est']} / bpe {r['tool_defs_all']['bpe']})\n")
    print(f"{'est':>6} {'bpe':>6}  系统提示组成 (按大小)")
    for k, e, b in r["system_parts"]:
        if e:
            print(f"{e:>6} {b:>6}  {k}")
    print(f"\n{'est':>6} {'bpe':>6}  低频工具组 (按需暴露)")
    for k, e, b in r["tool_groups"]:
        print(f"{e:>6} {b:>6}  {k}")
    if a.tools:
        print(f"\n{'est':>6} {'bpe':>6}  工具 Top {a.tools} (全量 TOOL_DEFS)")
        for k, e, b in r["tools"][: a.tools]:
            print(f"{e:>6} {b:>6}  {k}{'' if TG is None or k in TG.TOOL_TO_GROUP else '  [核心]'}")
    print(f"\n{'est':>6} {'bpe':>6}  动态上下文 (挂本轮 user 消息, 不计入固定前缀)")
    for k, e, b in r["dynamic"]:
        print(f"{e:>6} {b:>6}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
