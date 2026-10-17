#!/usr/bin/env python3
"""litecli.py — LiteCode CLI 兼容 web UI 功能 (task #14, 2026-05-19)

设计:
- 共享 router HTTP API (router 已拆好: /opt/litecode/routers/)
- click 子命令树跟 router schema 1:1 对齐
- session cookie 持久化到 ~/.litecode/cli_cookie.txt, login 一次后续命令自动用
- 默认 base url 走容器内 web_ui (http://localhost:18790), 可用 LITECODE_WEB_URL 覆盖

子命令:
  litecli login / logout / whoami
  litecli model list|use|test|add|rm
  litecli timer list|create|run|toggle|rm|history
  litecli sessions list|show|rm|export|truncate
  litecli memory <sid>
  litecli ws ls|cat|download
  litecli preview <path>
  litecli dag list|show|run|jobs|rm
  litecli wechat status|send|bots|qr
  litecli docker ps|logs|stats|inspect|restart|stop|start
  litecli plugins list|reload
  litecli projects list|show|create|rm
  litecli config get|set
  litecli stats
  litecli chat <message> [-s SID] [--new]
  litecli repl  (调用旧 cli.py REPL)
"""
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import click
import requests


# ── 配置 ─────────────────────────────────────────────────────
# [split 2026-10-16] 实现按命令分组拆到 lib/cli/ (容器里 lib/ 整目录挂载, litecli.py 单文件挂载);
# 这里只留: 路径注入 + 根 group/全局选项 + chat/repl + 入口. 所有原顶层名字在此 re-export.
BASE = Path(__file__).parent
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.cli import common as _common  # noqa: E402
from lib.cli.common import (  # noqa: E402,F401
    LITECODE_DIR,
    COOKIE_FILE,
    WEB_URL,
    SERVER_URL,
    _IS_TTY,
    _c,
    _err,
    _ok,
    _info,
    _save_cookie,
    _load_cookie,
    _session,
    _req,
    _r_json,
    _emit,
    _resolve_model_id,
    _resolve_model_ref,
)
from lib.cli import cmd_auth as _cmd_auth  # noqa: E402
from lib.cli.cmd_auth import (  # noqa: E402,F401
    login,
    logout,
    whoami,
)
from lib.cli.cmd_model import (  # noqa: E402,F401
    model,
    model_list,
    model_use,
    model_test,
    model_rm,
)
from lib.cli.cmd_timer import (  # noqa: E402,F401
    timer,
    timer_list,
    timer_create,
    timer_run,
    timer_toggle,
    timer_rm,
    timer_history,
)
from lib.cli import cmd_sessions as _cmd_sessions  # noqa: E402
from lib.cli.cmd_sessions import (  # noqa: E402,F401
    sessions,
    sessions_list,
    sessions_show,
    sessions_rm,
    sessions_export,
    sessions_truncate,
    sessions_abort,
    sessions_resume,
)
from lib.cli import cmd_ws as _cmd_ws  # noqa: E402
from lib.cli.cmd_ws import (  # noqa: E402,F401
    ws,
    ws_ls,
    ws_cat,
    ws_download,
    preview,
)
from lib.cli.cmd_dag import (  # noqa: E402,F401
    dag,
    dag_list,
    dag_show,
    dag_run,
    dag_rm,
    dag_jobs,
    dag_nl_gen,
    dag_nl_edit,
    dag_nl_rm,
    dag_nl_cron,
)
from lib.cli.cmd_bots import (  # noqa: E402,F401
    wechat,
    wechat_status,
    wechat_add,
    wechat_rm,
    wechat_relogin,
    wechat_send,
    wechat_bots,
    wecom,
    wecom_status,
    wecom_add,
    wecom_rm,
    wecom_relogin,
    wecom_send,
    wecom_bots,
    wecom_messages,
    wecom_contacts,
)
from lib.cli.cmd_docker import (  # noqa: E402,F401
    docker,
    docker_ps,
    docker_logs,
    docker_stats,
    docker_inspect,
    docker_restart,
    docker_stop,
    docker_start,
)
from lib.cli.cmd_plugins import (  # noqa: E402,F401
    plugins,
    plugins_list,
    plugins_reload,
)
from lib.cli.cmd_admin import (  # noqa: E402,F401
    projects,
    projects_list,
    projects_show,
    projects_create,
    projects_rm,
    config,
    config_get,
    config_set,
    memory,
    stats,
    health,
)


# ── click 根 + 全局选项 ──────────────────────────────────────
try:
    from lib.version import label as _vlabel
    _VERSION_LABEL = _vlabel()
except Exception:  # 单独拷走 litecli.py 时没有 lib/
    _VERSION_LABEL = "unknown"


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--json", "json_out", is_flag=True, help="输出 JSON 格式")
@click.option("--url", default=None, help=f"Web UI URL (默认 {WEB_URL})")
@click.version_option(_VERSION_LABEL, prog_name="LiteCode", message="%(prog)s %(version)s")
def cli(json_out: bool, url: Optional[str]):
    """LiteCode CLI — 跟 web UI 功能对等."""
    if json_out:
        os.environ["LITECLI_OUTPUT"] = "json"
    if url:
        global WEB_URL
        WEB_URL = url
        _sync_web_url(url)


def _sync_web_url(url: str):
    """--url 覆盖: lib/cli 各模块都实时读 common.WEB_URL, 只改这一处。"""
    _common.WEB_URL = url


# chat 留在入口模块: tests/test_thinking_route.py 打桩 litecli._req 后调 chat.callback
# ── chat ──────────────────────────────────────────────────
@cli.command()
@click.argument("message")
@click.option("-s", "--session", "sid", default=None, help="复用 session id, 不传则新建")
@click.option("--new", is_flag=True, help="新建 session (默认逻辑)")
@click.option("--no-stream", is_flag=True, help="一次性返回, 不流式")
@click.option("--hide-reasoning", is_flag=True,
              help="不显示模型思考流 (默认灰字显示, 与 Web 的折叠块对齐)")
def chat(message: str, sid: Optional[str], new: bool, no_stream: bool,
         hide_reasoning: bool = False):
    """跟 AI 对话 (SSE 流式)."""
    if not sid or new:
        d = _r_json("POST", "/api/sessions", json_body={"name": message[:40]})
        sid = d.get("id")
        _info(f"新建 session {sid}")
    # 走 SSE
    r = _req("POST", f"/api/chat/{sid}", json_body={"message": message},
             timeout=600, stream=not no_stream)
    if r.status_code != 200:
        _err(f"HTTP {r.status_code}: {r.text[:200]}")
    if no_stream:
        # 一次性返回: 解析完整 SSE, 只输出拼好的正文 (原来把 data: 帧原样打出来)
        parts = []
        for s in r.text.splitlines():
            if not s.startswith("data: ") or s[6:] == "[DONE]":
                continue
            try:
                chunk = json.loads(s[6:])
            except Exception:
                continue
            if "error" in chunk:
                click.echo(_c(f"[error] {chunk['error']}", "red"))
                continue
            try:
                parts.append(chunk["choices"][0]["delta"].get("content") or "")
            except (KeyError, IndexError, TypeError, AttributeError):
                continue
        click.echo("".join(parts))
        return
    # SSE 透传 OpenAI 兼容协议: data: {"choices":[{"delta":{...}}]} \n data: [DONE]
    # _rs["on"] = 当前是否正处在思考段 (用于思考→正文切换时补断行)
    _rs = {"on": False}
    for line in r.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8", errors="replace")
        if s.startswith(":"):
            continue  # 心跳注释
        if not s.startswith("data: "):
            continue
        data = s[6:]
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except Exception:
            continue
        if "error" in chunk:
            click.echo(_c(f"\n[error] {chunk['error']}", "red"))
            continue
        try:
            delta = chunk["choices"][0]["delta"]
        except (KeyError, IndexError, TypeError):
            continue
        # [thinking-cli 2026-08-26] reasoning (思考流增量) — 服务端已把 vLLM 的
        # reasoning_content/reasoning、Ollama 的 thinking 统一归一化成 delta.reasoning
        # (litecode_server.py 的多后端兼容段), 这里只认这一个字段即可。
        # 之前 CLI 只读 delta.content, 思考流被整段静默丢弃 —— Web 有折叠块、
        # CLI 却什么都看不到, 三端不一致。灰色 + 💭 抬头, 与 Web 的折叠块语义对齐。
        rc = delta.get("reasoning", "")
        if rc and not hide_reasoning:
            if not _rs["on"]:
                click.echo(_c("\n💭 ", "gray"), nl=False)
                _rs["on"] = True
            click.echo(_c(rc, "gray"), nl=False)
        # content (主回复增量)
        ct = delta.get("content", "")
        if ct:
            # 思考段结束、正文开始 → 断行, 否则灰字和正文糊在一行
            if _rs["on"]:
                click.echo()
                _rs["on"] = False
            click.echo(ct, nl=False)
        # tool 执行通知
        for key in ("task_exec", "data_collect", "task_analysis"):
            ev = delta.get(key)
            if not isinstance(ev, dict):
                continue
            st = ev.get("status", "")
            det = ev.get("detail", "")
            # [fix] key=preparing ("推理中…") 是状态占位, 非工具调用 — 跳过防幻影行
            if ev.get("key") == "preparing":
                continue
            if st == "executing":
                lbl = ev.get("agent_label") or ev.get("agent", "")
                tag = f"[{lbl}] " if lbl else ""
                click.echo(_c(f"\n[🔧 {tag}{det[:120]}]", "yellow"))
            elif st == "done" and det:
                click.echo(_c(f"  ↳ {det[:120]}", "gray"))
        # 子代理状态
        if "agent_status" in delta:
            av = delta["agent_status"]
            icons = {"pending": "⏳", "running": "▶", "done": "✅", "error": "❌"}
            icon = icons.get(av.get("status", ""), "●")
            click.echo(_c(f"\n{icon} [{av.get('label', '?')}] {av.get('detail', '')[:80]}", "cyan"))
        # DAG 节点状态
        if "orchestrator_step" in delta:
            o = delta["orchestrator_step"]
            icons = {"pending": "⏳", "running": "▶", "done": "✅",
                     "error": "❌", "failed": "❌", "skipped": "⏭"}
            icon = icons.get(o.get("status", ""), "●")
            lbl = o.get("label") or o.get("step_id") or "?"
            click.echo(_c(f"\n{icon} «{lbl}» {o.get('detail', '')[:80]}", "magenta"))
        # 面板刷新提示
        if "panel_refresh" in delta:
            pr = delta["panel_refresh"]
            click.echo(_c(f"\n[panel {pr.get('event', '')} {pr.get('op', '')} {pr.get('name', '')}]", "gray"))
    click.echo()  # 最终 newline


# ── repl (调旧 cli.py) ─────────────────────────────────────
@cli.command()
def repl():
    """打开交互式 REPL (调老 cli.py)."""
    import subprocess
    subprocess.call([sys.executable, str(BASE / "cli.py")])


# ── 子命令自动发现: lib/cli/cmd_*.py 的 COMMANDS + LITECODE_CLI_PLUGIN_PATH 插件 ──
# (放在 chat / repl 定义之后, 让发现逻辑知道哪些名字已被根命令占用)
from lib.cli import discover as _discover_commands  # noqa: E402
for _cmd in _discover_commands(taken=cli.commands):
    cli.add_command(_cmd)


if __name__ == "__main__":
    cli()
