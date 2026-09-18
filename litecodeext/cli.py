#!/usr/bin/env python3
"""
cli.py -- LiteCode 交互式 REPL
- prompt_toolkit: 中英文输入/方向键/退格/历史记录 (类 MySQL 控制台)
- diff 渲染 (Claude Code 风格)
- 工具状态 / warning 显示
"""

import argparse
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path

import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=False)

BASE = Path(__file__).parent

def _load_cfg() -> dict:
    p = BASE / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}

_cfg   = _load_cfg()
_srv   = _cfg.get("server", {})
_port  = _srv.get("port", 18789)
_token = _srv.get("token", "CHANGE_ME_TOKEN")
_paths = _cfg.get("paths", {})
_agt   = _cfg.get("agent", {})

SERVER_URL = os.environ.get("OPENCLAW_URL",   f"http://127.0.0.1:{_port}")
TOKEN      = os.environ.get("OPENCLAW_TOKEN",  _token)
MODEL      = _cfg.get("model", {}).get("id", "openclaw")
WEB_PORT   = str(_cfg.get("web_ui", {}).get("port", 18790))
# timeout: 环境变量 > config.json > 默认 600
TIMEOUT    = int(os.environ.get("OPENCLAW_TIMEOUT",
                 _agt.get("task_timeout_seconds", 600)))

# 路径与 server/litecode.py 保持一致
_WORKSPACE = Path(_paths.get("workspace_base",
                  _agt.get("workspace", str(Path.home() / ".litecode" / "workspace"))))
_LOGS_DIR  = Path(_paths.get("logs_dir", str(_WORKSPACE / "logs")))
_SERVER_LOG = _LOGS_DIR / "server.log"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type":  "application/json",
}

SHOW_THINKING_FULL    = False  # 默认折叠 thinking
SHOW_TOOL_RESULT_FULL = False  # 默认 tool_result 截断 90 字符
OUTPUT_FORMAT         = "text"  # set by --output-format
_CHAT_OK              = True    # [CLI-EXIT] 上一次 stream_chat 是否成功完成 (脚本/CI 退出码依据)

LITECODE_DIR  = Path.home() / ".litecode"
HISTORY_FILE  = LITECODE_DIR / "cli_history"
SESSION_FILE  = LITECODE_DIR / "cli_session.txt"
LITECODE_DIR.mkdir(parents=True, exist_ok=True)

_IS_TTY = sys.stdout.isatty()
def _c(code: str) -> str: return code if _IS_TTY else ""

G  = _c("\033[0;32m")
C  = _c("\033[0;36m")
Y  = _c("\033[1;33m")
D  = _c("\033[2m")
R  = _c("\033[0;31m")
B  = _c("\033[1m")
M  = _c("\033[0;35m")
NC = _c("\033[0m")

_DIFF_ADD = _c("\033[32m")
_DIFF_DEL = _c("\033[31m")
_DIFF_HDR = _c("\033[36m")
_DIFF_SEC = _c("\033[33m")

# ── prompt_toolkit ────────────────────────────────────────────
def _make_pt_session():
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.styles import Style
        from prompt_toolkit.key_binding import KeyBindings

        HISTORY_FILE.touch(exist_ok=True)

        style = Style.from_dict({
            "": "",
        })
        kb = KeyBindings()

        @kb.add("c-c")
        def _ctrl_c(event):
            event.app.current_buffer.reset()
            print(f"\n{Y}  (Ctrl-C clears input; Ctrl-D or /exit to quit){NC}")

        @kb.add("c-o")
        def _toggle_thinking(event):
            global SHOW_THINKING_FULL
            SHOW_THINKING_FULL = not SHOW_THINKING_FULL
            print(f"\n  💭 thinking 显示: {'开' if SHOW_THINKING_FULL else '折叠'}")

        @kb.add("c-k")
        def _toggle_result(event):
            global SHOW_TOOL_RESULT_FULL
            SHOW_TOOL_RESULT_FULL = not SHOW_TOOL_RESULT_FULL
            print(f"\n  🔧 tool_result 显示: {'全文' if SHOW_TOOL_RESULT_FULL else '截断90字符'}")

        return PromptSession(
            history=FileHistory(str(HISTORY_FILE)),
            auto_suggest=AutoSuggestFromHistory(),
            style=style,
            key_bindings=kb,
            enable_history_search=True,
            complete_while_typing=False,
            mouse_support=False,
        ), True
    except ImportError:
        return None, False

_PT_SESSION = None
_HAS_PT     = False

def _get_input(prompt_str: str) -> str:
    global _PT_SESSION, _HAS_PT
    if _HAS_PT and _PT_SESSION is not None:
        return _PT_SESSION.prompt(prompt_str)
    try:
        import readline
        readline.parse_and_bind("set input-meta on")
        readline.parse_and_bind("set output-meta on")
        readline.parse_and_bind("set convert-meta off")
        readline.parse_and_bind("set disable-completion on")
        readline.parse_and_bind("tab: self-insert")
    except ImportError:
        pass
    return input(prompt_str)

# ── Session ───────────────────────────────────────────────────
def new_session() -> str:
    sid = f"cli-{uuid.uuid4().hex[:8]}"
    SESSION_FILE.write_text(sid)
    return sid

def load_session() -> str:
    if SESSION_FILE.exists():
        sid = SESSION_FILE.read_text().strip()
        if sid:
            return sid
    return new_session()

_log_file: Path | None = None

def _log(text: str):
    if _log_file:
        try:
            with _log_file.open("a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

def check_server() -> bool:
    try:
        r = requests.get(f"{SERVER_URL}/health", headers=HEADERS, timeout=3)
        return r.status_code < 500
    except Exception:
        return False

# ── Diff renderer ─────────────────────────────────────────────
def render_diff(diff_text: str, filepath: str = ""):
    if not diff_text.strip():
        return
    lines = diff_text.splitlines()
    adds  = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    dels  = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    name  = filepath.split("/")[-1] if filepath else "file"
    print(f"\n{C}  {name}  {_DIFF_ADD}+{adds}{NC} {_DIFF_DEL}-{dels}{NC}")
    for line in lines:
        if line.startswith("+++") or line.startswith("---"):
            print(f"{_DIFF_HDR}{line}{NC}")
        elif line.startswith("+"):
            print(f"{_DIFF_ADD}{line}{NC}")
        elif line.startswith("-"):
            print(f"{_DIFF_DEL}{line}{NC}")
        elif line.startswith("@@"):
            print(f"{_DIFF_SEC}{line}{NC}")
        else:
            print(f"{D}{line}{NC}")
    print()

def _emit_json(obj: dict):
    print(json.dumps(obj, ensure_ascii=False), flush=True)

# ── Stream chat ───────────────────────────────────────────────
def stream_chat(message: str, session_id: str) -> str:
    global _CHAT_OK
    _CHAT_OK = False  # 入口先置失败, 只有跑到末尾正常 done 才翻 True
    # 发消息前先检查 server，离线立即提示而不是卡 600 秒
    if not check_server():
        if OUTPUT_FORMAT == "stream-json":
            _emit_json({"type": "error", "error": "Server offline"})
        else:
            print(f"\n{R}  Server offline. Use /restart to bring it back up.{NC}")
        return ""

    payload = {
        "model":    MODEL,
        "stream":   True,
        "messages": [{"role": "user", "content": message}],
        "user":     session_id,
    }
    full_reply    = ""
    tool_count    = 0
    first_content = True
    _usage_data   = None
    t0            = time.time()
    thinking_buf  = ""
    thinking_shown = False

    # ── Ctrl-C 中断: 首次 POST /api/interrupt 通知服务端 agent, 二次强断本地 ──
    # REPL 主循环把 SIGINT 设为 SIG_IGN 防误退, 这里在 stream 期间临时接管
    _int_state = {"count": 0}
    def _on_sigint(_signum, _frame):
        _int_state["count"] += 1
        if _int_state["count"] >= 2:
            raise KeyboardInterrupt
        try:
            requests.post(f"{SERVER_URL}/v1/sessions/{session_id}/interrupt",
                          headers=HEADERS, timeout=2)
        except Exception:
            pass
        if OUTPUT_FORMAT == "stream-json":
            _emit_json({"type": "interrupt_sent"})
        else:
            print(f"\n{Y}⏹ 已发送中断信号 (再按一次 Ctrl-C 强制退出){NC}", flush=True)
    _prev_sigint = signal.signal(signal.SIGINT, _on_sigint)

    try:
        # timeout=(connect_timeout, read_timeout)
        # 连接超时 8s（server 挂了立即失败），读取超时 600s（允许长时间推理）
        with requests.post(
            f"{SERVER_URL}/v1/chat/completions",
            headers=HEADERS, json=payload,
            stream=True, timeout=(8, TIMEOUT),
        ) as resp:

            if resp.status_code == 401:
                if OUTPUT_FORMAT == "stream-json":
                    _emit_json({"type": "error", "error": "AUTH ERROR"})
                else:
                    print(f"\n{R}[AUTH ERROR]{NC}")
                return ""
            if resp.status_code != 200:
                if OUTPUT_FORMAT == "stream-json":
                    _emit_json({"type": "error", "error": f"HTTP {resp.status_code}: {resp.text[:300]}"})
                else:
                    print(f"\n{R}[HTTP {resp.status_code}] {resp.text[:300]}{NC}")
                return ""

            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                # 跳过 SSE 注释行（心跳保活）
                if line.startswith(":"):
                    continue
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

                # diff_view 事件
                if "diff_view" in delta:
                    dv = delta["diff_view"]
                    if OUTPUT_FORMAT == "stream-json":
                        _emit_json({"type": "diff", "filepath": dv.get("filepath", ""), "diff": dv.get("diff", "")})
                    else:
                        render_diff(dv.get("diff", ""), dv.get("filepath", ""))
                    continue

                # reasoning/thinking 折叠
                if "reasoning" in delta:
                    reasoning_text = delta["reasoning"]
                    if reasoning_text:
                        thinking_buf += reasoning_text
                        if OUTPUT_FORMAT == "stream-json":
                            _emit_json({"type": "reasoning", "text": reasoning_text})
                        elif SHOW_THINKING_FULL:
                            if not thinking_shown:
                                print()
                                thinking_shown = True
                            sys.stdout.write(f"{D}{reasoning_text}{NC}")
                            sys.stdout.flush()
                        else:
                            if not thinking_shown:
                                print(f"\n{D}💭 thinking…{NC}", flush=True)
                                thinking_shown = True
                    continue

                # 工具调用状态
                for key in ("task_exec", "data_collect", "task_analysis"):
                    if key not in delta:
                        continue
                    ev     = delta[key]
                    status = ev.get("status", "")
                    detail = ev.get("detail", "")

                    # [fix] server 抢首反馈发的 task_exec key=preparing ("推理中…") 是
                    # 状态占位, 不是工具调用 — 跳过, 否则多一行幻影工具且 tool_count 虚增。
                    if ev.get("key") == "preparing":
                        continue

                    if status == "executing":
                        tool_count += 1
                        agent_lbl  = ev.get("agent_label") or ev.get("agent", "")
                        if OUTPUT_FORMAT == "stream-json":
                            tool_name = detail.split(":")[0] if ":" in detail else detail
                            args_str  = detail[len(tool_name)+1:].strip() if ":" in detail else ""
                            _emit_json({"type": "tool_call", "name": tool_name, "args": args_str, "agent_label": agent_lbl})
                        else:
                            lbl_tag    = f"{C}[{agent_lbl}]{NC} " if agent_lbl else ""
                            tool_name  = detail.split(":")[0] if ":" in detail else detail
                            rest       = detail[len(tool_name)+1:].strip()[:80] if ":" in detail else ""
                            print(f"\n{M}  [{tool_count}] {lbl_tag}{B}{tool_name}{NC}{M}: {rest}{NC}",
                                  flush=True)
                            _log(f"\n  [TOOL#{tool_count}] {detail}")

                    elif status == "done" and detail:
                        if OUTPUT_FORMAT == "stream-json":
                            _emit_json({"type": "tool_result", "detail": detail, "status": "done"})
                        else:
                            # [STDERR_WARNINGS - exit 0] 前缀表示命令成功但有 stderr，显示为 warning
                            if "[STDERR_WARNINGS" in detail:
                                warn = detail.split("]")[-1].strip()
                                warn = warn if SHOW_TOOL_RESULT_FULL else warn[:90]
                                print(f"{Y}  [warn] {warn}{NC}", flush=True)
                            else:
                                shown = detail if SHOW_TOOL_RESULT_FULL else detail[:90]
                                print(f"{D}    -> {shown}{NC}", flush=True)

                # ── Multi-agent pipeline status (agent_status events) ──
                if "agent_status" in delta:
                    av     = delta["agent_status"]
                    a_lbl  = av.get("label", "?")
                    a_st   = av.get("status", "")
                    a_det  = av.get("detail", "")
                    if OUTPUT_FORMAT == "stream-json":
                        _emit_json({"type": "agent_status", "label": a_lbl, "status": a_st, "detail": a_det})
                    else:
                        icons  = {"pending": f"{D}⏳", "running": f"{Y}▶", "done": f"{G}✅", "error": f"{R}❌"}
                        icon   = icons.get(a_st, "  ")
                        print(f"\n{icon} {B}[{a_lbl}]{NC} {a_det[:80]}{NC}", flush=True)

                # ── DAG 节点状态 (orchestrator_step) ──
                if "orchestrator_step" in delta:
                    o = delta["orchestrator_step"]
                    o_sid   = o.get("step_id", "")
                    o_lbl   = o.get("label") or o_sid or "?"
                    o_st    = o.get("status", "")
                    o_det   = o.get("detail", "")
                    o_phase = o.get("phase", "")
                    if OUTPUT_FORMAT == "stream-json":
                        _emit_json({
                            "type": "orchestrator_step",
                            "step_id": o_sid, "label": o_lbl, "status": o_st,
                            "detail": o_det, "phase": o_phase,
                            "elapsed_ms": o.get("elapsed_ms"),
                            "output_delta": o.get("output_delta", ""),
                            "stream": o.get("stream", ""),
                        })
                    else:
                        # phase=chunk 是 DAG 节点输出流, 默认终端模式不渲染 (太碎),
                        # 只渲染状态条 (start/end/纯状态切换).
                        if o_st and (o_det or o_phase in ("start", "end") or not o_phase):
                            icons = {"pending": f"{D}⏳", "running": f"{Y}▶",
                                     "done": f"{G}✅", "error": f"{R}❌",
                                     "failed": f"{R}❌", "skipped": f"{D}⏭"}
                            icon = icons.get(o_st, "  ")
                            ms = o.get("elapsed_ms")
                            ms_tag = f" ({ms/1000:.1f}s)" if isinstance(ms, (int, float)) else ""
                            print(f"\n{icon} {M}«{o_lbl}»{NC} {o_det[:80]}{ms_tag}{NC}", flush=True)

                # ── 面板刷新信号 (panel_refresh) — CLI 无面板, 仅 stream-json 透传 ──
                if "panel_refresh" in delta and OUTPUT_FORMAT == "stream-json":
                    pr = delta["panel_refresh"]
                    _emit_json({
                        "type": "panel_refresh",
                        "event": pr.get("event", ""),
                        "op": pr.get("op", ""),
                        "name": pr.get("name", ""),
                    })

                # Usage 统计事件
                if "usage" in delta:
                    _usage_data = delta["usage"]
                    if OUTPUT_FORMAT == "stream-json":
                        elapsed = time.time() - t0
                        _emit_json({
                            "type":               "usage",
                            "prompt_tokens":      _usage_data.get("prompt_tokens", 0),
                            "completion_tokens":  _usage_data.get("completion_tokens", 0),
                            "total_tokens":       _usage_data.get("prompt_tokens", 0) + _usage_data.get("completion_tokens", 0),
                            "iterations":         _usage_data.get("iterations", 1),
                            "elapsed_seconds":    round(elapsed, 2),
                        })

                content = delta.get("content", "")
                if content:
                    if OUTPUT_FORMAT == "stream-json":
                        _emit_json({"type": "content", "text": content})
                    else:
                        if first_content:
                            print()
                            first_content = False
                        sys.stdout.write(content)
                        sys.stdout.flush()
                    full_reply += content

    except requests.exceptions.ConnectionError:
        if OUTPUT_FORMAT == "stream-json":
            _emit_json({"type": "error", "error": f"Cannot connect to {SERVER_URL}"})
        else:
            print(f"\n{R}[ERROR] Cannot connect to {SERVER_URL}{NC}")
        return ""
    except requests.exceptions.Timeout:
        if OUTPUT_FORMAT == "stream-json":
            _emit_json({"type": "error", "error": f"TIMEOUT after {TIMEOUT}s"})
        else:
            print(f"\n{Y}[TIMEOUT after {TIMEOUT}s]{NC}")
        return full_reply
    except KeyboardInterrupt:
        if OUTPUT_FORMAT == "stream-json":
            _emit_json({"type": "error", "error": "interrupted"})
        else:
            print(f"\n{Y}[interrupted]{NC}")
        return full_reply
    except Exception as e:
        if OUTPUT_FORMAT == "stream-json":
            _emit_json({"type": "error", "error": str(e)})
        else:
            print(f"\n{R}[ERROR] {e}{NC}")
        return ""
    finally:
        signal.signal(signal.SIGINT, _prev_sigint)

    _CHAT_OK = True  # 跑到这里=正常完成 (未被上面任何 except 提前 return)
    if OUTPUT_FORMAT == "stream-json":
        _emit_json({"type": "done"})
    else:
        elapsed = time.time() - t0
        if _usage_data:
            _pt = _usage_data.get("prompt_tokens",0); _ct = _usage_data.get("completion_tokens",0)
            _it = _usage_data.get("iterations",1)
            _tok_info = f"  P:{_pt:,} C:{_ct:,} T:{_pt+_ct:,}"
            _iter_info = f" | {_it}轮" if _it > 1 else ""
            print(f"\n{D}  [{elapsed:.1f}s]{_tok_info}{_iter_info}{NC}")
        else:
            print(f"\n{D}  [{elapsed:.1f}s]{NC}")
    if thinking_buf:
        _log(f"\n[thinking]\n{thinking_buf}\n[/thinking]\n")
    _log(f"\n<<< {full_reply}\n")
    return full_reply

# ── Slash 命令 ────────────────────────────────────────────────
def handle_slash(cmd: str, session_id: str) -> tuple[bool, str]:
    parts = cmd.strip().split(None, 1)
    name  = parts[0].lower()

    if name in ("/exit", "/quit", "/q"):
        print(f"\n{C}Bye.{NC}\n")
        sys.exit(0)

    elif name == "/help":
        print(f"""
{B}本地控制:{NC}
  {G}/session [id]{NC}    Show / switch session
  {G}/newsession{NC}      Fresh session
  {G}/memory{NC}          View session memory status
  {G}/checkpoints{NC}     List file checkpoints (P1-5)
  {G}/rewind [file]{NC}   Rewind a file to its last checkpoint
  {G}/web [port]{NC}      Start Web UI
  {G}/status{NC}          Ping server
  {G}/restart{NC}         Kill and restart server
  {G}/log [n]{NC}         Show last n lines of server.log (default 30)
  {G}/procs{NC}           List/kill background processes
  {G}/stats{NC}           Token 使用统计（输入/输出/总计/按天/会话排行）
  {G}/map [root]{NC}      查看 PROJECT_MAP 热更新状态 / 强制重新生成
  {G}/think on|off{NC}    Toggle reasoning display (or Ctrl-O)
  {G}/result on|off{NC}   Toggle tool_result full display (or Ctrl-K)
  {G}/clear{NC}           Clear screen
  {G}/reset{NC}           New session (keep old on disk)
  {G}/nuke{NC}            {R}⚠{NC} Delete current session + wipe workspace top
  {G}/exit{NC}            Exit (or Ctrl-D)

{B}Web/Server 管理 (代理 litecli, 首次用先 /login):{NC}
  {G}/login{NC} | {G}/logout{NC} | {G}/whoami{NC}        Web UI 鉴权
  {G}/dag list|show|run|jobs|rm {C}<name>{NC}       DAG 编排
  {G}/timer list|create|run|toggle|rm|history{NC}  定时任务
  {G}/wechat status|send|bots {C}<args>{NC}        微信桥
  {G}/wecom  status|add|send|bots|messages{NC}    企微智能机器人桥
  {G}/model list|use|test|rm {C}<id>{NC}           多模型切换
  {G}/projects list|show|create|rm {C}<pid>{NC}    项目管理
  {G}/ws ls|cat|download {C}<path>{NC}             Workspace 浏览
""")

    elif name == "/session":
        if len(parts) > 1:
            session_id = parts[1].strip()
            SESSION_FILE.write_text(session_id)
            print(f"  {Y}Session: {session_id}{NC}")
        else:
            print(f"  Session: {Y}{session_id}{NC}")

    elif name == "/newsession":
        session_id = new_session()
        print(f"  {Y}New session: {session_id}{NC}")

    elif name == "/status":
        sys.stdout.write(f"  Checking {SERVER_URL} ... ")
        sys.stdout.flush()
        print(f"{G}online{NC}" if check_server() else f"{R}offline{NC}")

    elif name == "/restart":
        import subprocess, importlib.util
        print(f"  {Y}Killing server...{NC}")
        subprocess.run(
            "pkill -f litecode_server.py 2>/dev/null; "
            "pkill -f server.py 2>/dev/null; "
            "pkill -f web_ui.py 2>/dev/null; true",
            shell=True, capture_output=True)
        time.sleep(1.0)
        # 强杀残留
        subprocess.run(
            "pkill -9 -f litecode_server.py 2>/dev/null; "
            "pkill -9 -f server.py 2>/dev/null; true",
            shell=True, capture_output=True)
        time.sleep(0.3)
        # 优先使用重构版 server.py，回退到旧版
        server_py = BASE / "server.py" if (BASE / "server.py").exists() else BASE / "litecode_server.py"
        _WORKSPACE.mkdir(parents=True, exist_ok=True)
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)

        log_fd = open(str(_SERVER_LOG), "a")
        log_fd.write(f"\n{'='*40}\n[restart]\n")
        log_fd.flush()
        try:
            proc = subprocess.Popen(
                [sys.executable, str(server_py)],
                stdout=log_fd, stderr=log_fd,
                start_new_session=True,
            )
        finally:
            log_fd.close()
        print(f"  {Y}Server starting (pid={proc.pid})...{NC}")
        for i in range(24):
            time.sleep(0.5)
            if proc.poll() is not None:
                print(f"  {R}Server process died immediately (code={proc.returncode}){NC}")
                print(f"  {Y}Last log lines:{NC}")
                tail = _SERVER_LOG.read_text(errors="replace").splitlines()
                print("\n".join(f"    {l}" for l in tail[-15:]))
                break
            if check_server():
                print(f"  {G}Server online{NC}")
                break
        else:
            print(f"  {R}Server still down after 12s. Check: {_SERVER_LOG}{NC}")

    elif name == "/web":
        port = parts[1].strip() if len(parts) > 1 else WEB_PORT
        import subprocess
        web_py = BASE / "web_ui.py"
        if not web_py.exists():
            print(f"  {R}web_ui.py not found{NC}")
        else:
            subprocess.Popen(
                [sys.executable, str(web_py)],
                env={**os.environ, "WEB_PORT": str(port)},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                # start_new_session: Web UI 在独立 session，CLI 退出后继续运行
                start_new_session=True,
            )
            print(f"  {G}Web UI starting: http://127.0.0.1:{port}{NC}")

    elif name == "/procs":
        # 列出 server 管理的后台进程
        try:
            r = requests.get(f"{SERVER_URL}/v1/bgprocs", headers=HEADERS, timeout=5)
            data = r.json()
        except Exception as ex:
            print(f"  {R}Cannot reach server: {ex}{NC}")
            return True, session_id
        procs = data.get("procs", [])
        if not procs:
            print(f"  {D}No background processes.{NC}")
        else:
            # [v1.0] 显示 keep 标记和 session 关联
            print(f"\n  {'PID':<8} {'Alive':<6} {'Keep':<6} {'Session':<12} {'Started':<8} {'Command'}")
            print(f"  {'─'*70}")
            for p in procs:
                ts   = time.strftime("%H:%M:%S", time.localtime(p["ts"])) if p.get("ts") else "?"
                dot  = f"{G}●{NC}" if p["alive"] else f"{R}●{NC}"
                cmd  = p.get("cmd","")[:40]
                keep_mark = f"{Y}KEEP{NC}" if p.get("keep") else f"{D}temp{NC}"
                sid_short = (p.get("sid","")[:10] + "..") if len(p.get("sid","")) > 12 else p.get("sid","-")
                print(f"  {dot} {p['pid']:<6}  {keep_mark:<10}  {sid_short:<12} {ts:<8} {cmd}")
            print()
            target = input(f"  {Y}Kill PID (enter to skip): {NC}").strip()
            if target.isdigit():
                try:
                    r2 = requests.delete(f"{SERVER_URL}/v1/bgprocs/{target}",
                                         headers=HEADERS, timeout=5)
                    d2 = r2.json()
                    if d2.get("ok"):
                        print(f"  {G}Killed PID {target}{NC}")
                    else:
                        print(f"  {R}Kill failed: {d2}{NC}")
                except Exception as ex:
                    print(f"  {R}Error: {ex}{NC}")

    elif name == "/clear":
        os.system("clear")

    elif name == "/reset":
        """新起一个 session, 保留旧 session 在 disk."""
        try:
            requests.post(f"{SERVER_URL}/v1/sessions/{session_id}/interrupt",
                          headers=HEADERS, timeout=3)
        except Exception:
            pass
        old_sid = session_id
        session_id = new_session()
        print(f"  {G}Reset OK.{NC} old={D}{old_sid}{NC}  new={Y}{session_id}{NC}")

    elif name == "/nuke":
        """核弹: 删当前 session (server + disk) + 清 workspace 顶层残留 + 新起 session."""
        confirm = input(f"  {R}⚠ /nuke 会删掉当前 session + 清空 workspace 顶层. 确认? [y/N]: {NC}").strip().lower()
        if confirm != 'y':
            print(f"  {D}取消.{NC}")
        else:
            try:
                r = requests.delete(f"{SERVER_URL}/v1/sessions/{session_id}",
                                    headers=HEADERS, timeout=5)
                ok = r.json().get("ok") if r.status_code == 200 else False
                print(f"  {G if ok else R}Server session {'deleted' if ok else 'delete failed'}: {session_id}{NC}")
            except Exception as ex:
                print(f"  {R}Server delete err: {ex}{NC}")
            # 清 workspace 顶层 (只删文件, 保留 .checkpoints 等隐藏目录)
            try:
                cnt = 0
                if _WORKSPACE.exists():
                    for p in _WORKSPACE.iterdir():
                        if p.name.startswith('.'):
                            continue
                        if p.is_file():
                            p.unlink(); cnt += 1
                        elif p.is_dir() and p.name not in ('sessions', 'logs', 'checkpoints'):
                            import shutil; shutil.rmtree(p, ignore_errors=True); cnt += 1
                print(f"  {G}Workspace 清理: {cnt} 个顶层项{NC}")
            except Exception as ex:
                print(f"  {R}Workspace clean err: {ex}{NC}")
            session_id = new_session()
            print(f"  {Y}New session: {session_id}{NC}")

    elif name == "/log":
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
        if _SERVER_LOG.exists():
            tail = _SERVER_LOG.read_text(errors="replace").splitlines()
            print(f"\n{D}--- {_SERVER_LOG} (last {n} lines) ---{NC}")
            print("\n".join(tail[-n:]))
            print(f"{D}{'─' * 50}{NC}\n")
        else:
            print(f"  {R}No log file at {_SERVER_LOG}{NC}")

    elif name == "/memory":
        # 查看当前 session 的记忆状态
        try:
            r = requests.get(f"{SERVER_URL}/memory/{session_id}",
                             headers=HEADERS, timeout=5)
            if r.status_code == 200:
                mem = r.json()
                l1 = mem.get("l1")
                if l1:
                    print(f"\n{C}  MEMORY.md ({l1.get('lines',0)} lines, ~{l1.get('tokens',0)} tokens){NC}")
                    sects = l1.get("sections", {})
                    for sec, items in sects.items():
                        print(f"  {Y}## {sec}{NC}")
                        for item in items[:3]:
                            print(f"    - {item[:80]}")
                else:
                    print(f"  {D}(尚无记忆 — 对话压缩后自动生成){NC}")
                l2 = mem.get("l2", [])
                if l2:
                    print(f"\n  {C}L2 files:{NC}")
                    for f in l2:
                        print(f"    {f['name']} ({f['lines']} lines)")
                pct = mem.get("pct", 0)
                print(f"\n  {D}Context usage: {pct}%{NC}")
            elif r.status_code == 503:
                print(f"  {D}Memory module not available{NC}")
            else:
                print(f"  {R}HTTP {r.status_code}{NC}")
        except Exception as ex:
            print(f"  {R}Cannot reach server: {ex}{NC}")

    elif name == "/checkpoints":
        ckpt_dir = _WORKSPACE / ".openclaw_checkpoints"
        if not ckpt_dir.exists() or not list(ckpt_dir.glob("*.bak")):
            print(f"  {D}No checkpoints yet.{NC}")
        else:
            files = sorted(ckpt_dir.glob("*.bak"), reverse=True)
            print(f"\n  {C}Checkpoints ({len(files)}):{NC}")
            seen = set()
            for f in files:
                name_parts = f.name.rsplit(".", 2)
                orig = name_parts[0] if len(name_parts) >= 3 else f.name
                ts_str = name_parts[1] if len(name_parts) >= 3 else "?"
                try:
                    dt = time.strftime("%m-%d %H:%M:%S", time.localtime(int(ts_str)))
                except Exception:
                    dt = ts_str
                size = f.stat().st_size
                marker = f"  {G}*{NC}" if orig not in seen else f"  {D} {NC}"
                seen.add(orig)
                print(f"{marker} {orig:<30} {dt}  {size:>6} bytes")
            print()

    elif name == "/rewind":
        target = parts[1].strip() if len(parts) > 1 else None
        ckpt_dir = _WORKSPACE / ".openclaw_checkpoints"
        if not target:
            print(f"  {Y}Usage: /rewind <filename>{NC}")
            print(f"  {D}Use /checkpoints to see available files{NC}")
        elif not ckpt_dir.exists():
            print(f"  {R}No checkpoints found{NC}")
        else:
            candidates = sorted(ckpt_dir.glob(f"{target}.*.bak"), reverse=True)
            if not candidates:
                print(f"  {R}No checkpoint for '{target}'{NC}")
            else:
                bak = candidates[0]
                orig_path = _WORKSPACE / target
                if not orig_path.exists():
                    found = list(_WORKSPACE.rglob(target))
                    orig_path = found[0] if found else _WORKSPACE / target
                content = bak.read_text(errors="replace")
                orig_path.write_text(content)
                print(f"  {G}Rewound {target} <- {bak.name} ({len(content)} chars){NC}")

    elif name == "/stats":
        # 查询 token 使用统计
        try:
            r = requests.get(f"{SERVER_URL}/stats", headers=HEADERS, timeout=5)
            if r.status_code == 200:
                d = r.json()
                fmt = lambda n: f"{(n or 0):,}"
                pt  = d.get("total_prompt_tokens", 0)
                ct  = d.get("total_completion_tokens", 0)
                tt  = d.get("total_tokens", 0)
                calls = d.get("total_calls", 0)
                avg   = d.get("avg_tokens_per_call", 0)
                print(f"""
{C}  ── Token 使用统计 ──────────────────────{NC}
  {Y}输入 Prompt   {NC}{B}{fmt(pt)}{NC} tokens
  {G}输出 Completion{NC}{B}{fmt(ct)}{NC} tokens
  {B}总计           {fmt(tt)}{NC} tokens  |  调用 {calls} 次  |  均 {avg} tokens/次
""")
                days = sorted(d.get("by_day", {}).items())[-7:]
                if days:
                    print(f"  {D}近7天:{NC}")
                    for day, v in days:
                        bar_len = int((v.get("prompt",0)+v.get("completion",0)) / max(tt/30,1))
                        bar = "█" * min(bar_len, 30)
                        print(f"  {D}{day}{NC}  {bar}  {fmt(v.get('prompt',0)+v.get('completion',0))}")
                print()
                top = d.get("top_sessions", [])[:5]
                if top:
                    print(f"  {D}会话排行（Top 5）:{NC}")
                    for s in top:
                        stot = s.get("prompt",0)+s.get("completion",0)
                        print(f"  {D}{s['sid'][:20]:<20}{NC}  {fmt(stot):>12}  {s.get('calls',0)}次")
                print()
            else:
                print(f"  {R}HTTP {r.status_code}{NC}")
        except Exception as ex:
            print(f"  {R}Cannot reach server: {ex}{NC}")

    elif name == "/map":
        target_root = parts[1].strip() if len(parts) > 1 else None
        # Check map status
        try:
            r = requests.get(f"{SERVER_URL}/v1/map_status", headers=HEADERS, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if not data.get("enabled"):
                    print(f"  {Y}MapWatcher 未启用 (project_map_watcher.py 未安装){NC}")
                else:
                    projs = data.get("projects", [])
                    if not projs:
                        print(f"  {D}暂无监控项目。MapWatcher 已启用但未注册项目。{NC}")
                    else:
                        print(f"\n{C}  PROJECT_MAP 热更新状态:{NC}")
                        for p in projs:
                            status = p.get("status", "?")
                            root_str = p.get("root", "?")
                            updated = p.get("map_updated", "?")
                            size = p.get("map_size", 0)
                            nfiles = p.get("tracked_files", 0)
                            icon = f"{G}●{NC}" if status == "ok" else f"{Y}●{NC}"
                            print(f"  {icon} {root_str}")
                            print(f"    {D}更新: {updated}  大小: {size}B  文件: {nfiles} tracked{NC}")
                if target_root:
                    # Force update
                    r2 = requests.post(f"{SERVER_URL}/v1/map_force_update",
                                       headers=HEADERS, json={"project_root": target_root}, timeout=5)
                    if r2.status_code == 200:
                        print(f"  {G}强制更新已触发: {target_root}{NC}")
                    else:
                        print(f"  {R}强制更新失败: HTTP {r2.status_code}{NC}")
            elif r.status_code == 404:
                print(f"  {Y}旧版 server，/v1/map_status 接口不存在{NC}")
            else:
                print(f"  {R}HTTP {r.status_code}{NC}")
        except Exception as ex:
            print(f"  {R}Cannot reach server: {ex}{NC}")

    elif name == "/think":
        global SHOW_THINKING_FULL
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if arg == "on":
            SHOW_THINKING_FULL = True
        elif arg == "off":
            SHOW_THINKING_FULL = False
        else:
            state = "开" if SHOW_THINKING_FULL else "折叠"
            print(f"  💭 thinking 显示: {state}  (用 /think on|off 切换, 或 Ctrl-O)")
            return True, session_id
        state = "开" if SHOW_THINKING_FULL else "折叠"
        print(f"  💭 thinking 显示: {state}")

    elif name == "/result":
        global SHOW_TOOL_RESULT_FULL
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if arg == "on":
            SHOW_TOOL_RESULT_FULL = True
        elif arg == "off":
            SHOW_TOOL_RESULT_FULL = False
        else:
            state = "全文" if SHOW_TOOL_RESULT_FULL else "截断90字符"
            print(f"  🔧 tool_result 显示: {state}  (用 /result on|off 切换, 或 Ctrl-K)")
            return True, session_id
        state = "全文" if SHOW_TOOL_RESULT_FULL else "截断90字符"
        print(f"  🔧 tool_result 显示: {state}")

    elif name in ("/dag", "/timer", "/wechat", "/wecom", "/model", "/projects",
                  "/ws", "/login", "/logout", "/whoami"):
        # 复用 litecli subcommand 实现 — cookie 鉴权 / HTTP 调用都在 litecli 里
        import subprocess, shlex
        litecli_py = BASE / "litecli.py"
        if not litecli_py.exists():
            print(f"  {R}litecli.py 缺失: {litecli_py}{NC}")
            return True, session_id
        sub = name.lstrip("/")
        extra = shlex.split(parts[1]) if len(parts) > 1 else []
        try:
            subprocess.call([sys.executable, str(litecli_py), sub, *extra])
        except KeyboardInterrupt:
            print()

    else:
        print(f"  {R}Unknown: {cmd}{NC}  (/help)")

    return True, session_id

# ── REPL ──────────────────────────────────────────────────────
def repl(session_id: str):
    global _PT_SESSION, _HAS_PT
    _PT_SESSION, _HAS_PT = _make_pt_session()

    prompt_str = "litecode > "
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    while True:
        print(f"\n{D}{'─' * 45}{NC}")
        try:
            user_input = _get_input(prompt_str).strip()
        except EOFError:
            print(f"\n{C}Bye.{NC}")
            break
        except KeyboardInterrupt:
            print()
            continue

        if not user_input:
            continue

        if user_input.startswith("/"):
            _, session_id = handle_slash(user_input, session_id)
            continue

        _log(f"\n>>> {user_input}\n")
        stream_chat(user_input, session_id)

# ── Entry ─────────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--query",    help="Single query")
    p.add_argument("--session",  help="Session ID")
    p.add_argument("--log",      help="Log file path")
    p.add_argument("--server",   help="Server URL")
    p.add_argument("--token",    help="Auth token")
    p.add_argument("--model",    help="Model ID")
    p.add_argument("--web-port", dest="web_port")
    p.add_argument("--check",    action="store_true")
    p.add_argument("--output-format", dest="output_format",
                   choices=["text", "stream-json"], default="text",
                   help="Output format: text (default, ANSI colored) or stream-json (NDJSON for CI/scripts)")
    args = p.parse_args()

    if args.server:
        SERVER_URL = args.server
    if args.token:
        TOKEN = args.token
        HEADERS["Authorization"] = f"Bearer {TOKEN}"
    if args.model:
        MODEL = args.model
    if args.web_port:
        WEB_PORT = args.web_port
    if args.log:
        _log_file = Path(args.log)

    if args.output_format == "stream-json":
        OUTPUT_FORMAT = "stream-json"
        SHOW_THINKING_FULL = True
        SHOW_TOOL_RESULT_FULL = True
        G = C = Y = D = R = B = M = NC = ""
        _DIFF_ADD = _DIFF_DEL = _DIFF_HDR = _DIFF_SEC = ""

    if args.check:
        sys.exit(0 if check_server() else 1)

    sid = args.session or load_session()

    # [CLI-EXIT] 显式拦截空 query: 否则 `if args.query` 把空串当假, 静默落到 stdin 分支 exit 0
    if args.query is not None and not args.query.strip():
        if OUTPUT_FORMAT == "stream-json":
            _emit_json({"type": "error", "error": "empty query"})
        else:
            print(f"{R}  空查询: --query 不能为空{NC}", file=sys.stderr)
        sys.exit(2)

    if args.query:
        stream_chat(args.query, sid)
        sys.exit(0 if _CHAT_OK else 1)  # server 离线/请求失败 → 非零, 脚本可判

    if not sys.stdin.isatty():
        _all_ok = True
        for line in sys.stdin:
            line = line.strip()
            if line:
                stream_chat(line, sid)
                _all_ok = _all_ok and _CHAT_OK
        sys.exit(0 if _all_ok else 1)

    repl(session_id=sid)
