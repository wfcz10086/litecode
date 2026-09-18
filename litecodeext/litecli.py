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
BASE = Path(__file__).parent
LITECODE_DIR = Path.home() / ".litecode"
LITECODE_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_FILE = LITECODE_DIR / "cli_cookie.txt"

WEB_URL = os.environ.get("LITECODE_WEB_URL", "http://localhost:18790")
SERVER_URL = os.environ.get("OPENCLAW_URL", "http://localhost:18789")

_IS_TTY = sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    if not _IS_TTY:
        return text
    codes = {"red": "31", "green": "32", "yellow": "33", "blue": "34",
             "cyan": "36", "magenta": "35", "gray": "90", "bold": "1"}
    return f"\033[{codes.get(color, '0')}m{text}\033[0m"


def _err(msg: str, exit_code: int = 1):
    click.echo(_c(f"✗ {msg}", "red"), err=True)
    sys.exit(exit_code)


def _ok(msg: str):
    click.echo(_c(f"✓ {msg}", "green"))


def _info(msg: str):
    click.echo(_c(msg, "cyan"))


def _save_cookie(cookie: str):
    COOKIE_FILE.write_text(cookie)
    try:
        COOKIE_FILE.chmod(0o600)
    except Exception:
        pass


def _load_cookie() -> str:
    return COOKIE_FILE.read_text().strip() if COOKIE_FILE.exists() else ""


def _session() -> requests.Session:
    s = requests.Session()
    cookie = _load_cookie()
    if cookie:
        s.cookies.set("lc_auth", cookie)
    return s


def _req(method: str, path: str, json_body=None, params=None, timeout: int = 30,
         stream: bool = False) -> requests.Response:
    url = f"{WEB_URL}{path}"
    s = _session()
    try:
        r = s.request(method, url, json=json_body, params=params, timeout=timeout, stream=stream)
    except requests.RequestException as e:
        _err(f"网络错误: {e}")
    if r.status_code == 401:
        _err("未登录或 cookie 失效, 跑: litecli login")
    return r


def _r_json(method: str, path: str, **kw) -> dict:
    r = _req(method, path, **kw)
    if r.status_code >= 400:
        try:
            d = r.json()
            _err(f"{path} → HTTP {r.status_code}: {d.get('detail') or d.get('error') or d}")
        except Exception:
            _err(f"{path} → HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text}


def _emit(obj, fmt: str = "auto"):
    """根据 --json 标志或对象类型决定输出格式."""
    if fmt == "json" or os.environ.get("LITECLI_OUTPUT") == "json":
        click.echo(json.dumps(obj, ensure_ascii=False, indent=2))
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                click.echo(f"{_c(k, 'cyan')}: {json.dumps(v, ensure_ascii=False)[:200]}")
            else:
                click.echo(f"{_c(k, 'cyan')}: {v}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, dict):
                click.echo(_c(f"--- [{i}] ---", "gray"))
                for k, v in item.items():
                    click.echo(f"  {k}: {str(v)[:100]}")
            else:
                click.echo(f"[{i}] {item}")
    else:
        click.echo(obj)


def _resolve_model_id(target: str) -> str:
    """支持 #1..#6 简写, 自动查 /api/models 找 id."""
    mid, _ = _resolve_model_ref(target)
    return mid


def _resolve_model_ref(target: str) -> tuple[str, str]:
    """返回 (model_id, backend_url). 同名双 entry 必须靠 backend_url 区分.
    - "#N" 简写 → 取列表第 N 条, 同时取它的 backend_url
    - 直接 id 字符串 → backend_url 留空, 让 server 走 id-only 兜底
    """
    if not target.startswith("#"):
        return target, ""
    try:
        n = int(target[1:])
    except ValueError:
        return target, ""
    d = _r_json("GET", "/api/models")
    models = d.get("models", [])
    if 1 <= n <= len(models):
        m = models[n - 1]
        return m["id"], m.get("backend_url", "")
    _err(f"模型 #{n} 不存在 (共 {len(models)} 个)")
    return "", ""


# ── click 根 + 全局选项 ──────────────────────────────────────
@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--json", "json_out", is_flag=True, help="输出 JSON 格式")
@click.option("--url", default=None, help=f"Web UI URL (默认 {WEB_URL})")
def cli(json_out: bool, url: Optional[str]):
    """LiteCode CLI — 跟 web UI 功能对等."""
    if json_out:
        os.environ["LITECLI_OUTPUT"] = "json"
    if url:
        global WEB_URL
        WEB_URL = url


# ── auth ───────────────────────────────────────────────────
@cli.command()
@click.option("-p", "--password", prompt=True, hide_input=True, help="登录密码")
def login(password: str):
    """登录并保存 cookie 到 ~/.litecode/cli_cookie.txt."""
    r = _req("POST", "/api/login", json_body={"password": password})
    if r.status_code != 200:
        _err(f"登录失败 HTTP {r.status_code}: {r.text[:200]}")
    if not r.cookies.get("lc_auth"):
        # auth 关闭的情况
        if r.json().get("ok") and "disabled" in r.json().get("msg", ""):
            _ok("auth 未启用, 无需登录")
            return
        _err("登录响应没有 cookie")
    _save_cookie(r.cookies["lc_auth"])
    _ok(f"已登录 {WEB_URL}, cookie 存 {COOKIE_FILE}")


@cli.command()
def logout():
    """清除本地 cookie."""
    if COOKIE_FILE.exists():
        COOKIE_FILE.unlink()
    _ok("已清除 cookie")


@cli.command()
def whoami():
    """显示登录状态."""
    d = _r_json("GET", "/api/auth/status")
    _emit(d)


# ── model ──────────────────────────────────────────────────
@cli.group()
def model():
    """多模型管理."""


@model.command("list")
def model_list():
    """列出所有模型."""
    d = _r_json("GET", "/api/models")
    active = d.get("active", "")
    active_backend = (d.get("active_backend") or "").rstrip("/")
    for i, m in enumerate(d.get("models", []), 1):
        # 同 id 不同 backend (如 deepseek-v4-pro 直连 + gether 网关) 必须按 (id, backend_url)
        # 双键判定, 否则两条都会打 ★
        m_url = (m.get("backend_url") or "").rstrip("/")
        is_active = m["id"] == active and (not active_backend or not m_url or m_url == active_backend)
        flag = _c(" ★", "green") if is_active else ""
        label = m.get("label", m["id"])
        click.echo(f"  {_c(f'#{i}', 'cyan')}  {label}{flag}")
        click.echo(f"      id={m['id']}  backend={m.get('backend_type', '?')}@{m.get('backend_url', '?')}")


@model.command("use")
@click.argument("target")
def model_use(target: str):
    """切换到指定模型 (支持 #N 简写 或完整 id)."""
    mid, backend_url = _resolve_model_ref(target)
    if not mid:
        return
    # 必须把 backend_url 一起发, 否则同名双 entry 服务端永远命中列表第一条
    body: dict = {"model_id": mid}
    if backend_url:
        body["backend_url"] = backend_url
    d = _r_json("POST", "/api/models/switch", json_body=body)
    if d.get("ok"):
        _ok(f"已切到 {d.get('model', mid)}  ctx={d.get('context_window', '?')}  backend={d.get('backend_type', '?')}")
    else:
        _err(f"切换失败: {d.get('error', '?')}")


@model.command("test")
@click.argument("target")
@click.option("--prompt", default="1+1=?", help="测试 prompt")
def model_test(target: str, prompt: str):
    """测试模型是否可用 (vLLM 冷启动会慢, 等 30s)."""
    mid = _resolve_model_id(target)
    d = _r_json("POST", "/api/models/test", json_body={"model_id": mid, "prompt": prompt}, timeout=35)
    _emit(d)


@model.command("rm")
@click.argument("model_id")
def model_rm(model_id: str):
    """从配置删除模型 (不影响 backend 本身)."""
    d = _r_json("DELETE", f"/api/models/{model_id}")
    _emit(d)


# ── timer ──────────────────────────────────────────────────
@cli.group()
def timer():
    """定时任务."""


@timer.command("list")
def timer_list():
    d = _r_json("GET", "/api/timers")
    if not d.get("available"):
        _err("timer 模块不可用")
    for t in d.get("timers", []):
        en = "✓" if t.get("enabled") else "✗"
        en_color = "green" if t.get("enabled") else "gray"
        click.echo(f"  {_c(en, en_color)}  {_c(t['id'], 'cyan')}  {t['name']}  ({t['type']} {t['schedule']})  run_count={t.get('run_count', 0)}")
        action = t.get("action", {})
        click.echo(f"      → {action.get('type')} {str(action.get('content', ''))[:80]}")


@timer.command("create")
@click.option("--name", required=True)
@click.option("--type", "ttype", type=click.Choice(["cron", "once"]), default="cron")
@click.option("--schedule", required=True, help="cron 表达式 (* * * * *) 或 ISO 时间")
@click.option("--action", "atype", type=click.Choice(["shell", "agent",
              "wechat_msg", "wechat_shell",
              "wecom_msg", "wecom_shell",
              "web_notify", "dag"]), required=True)
@click.option("--target", default="")
@click.option("--content", required=True)
def timer_create(name: str, ttype: str, schedule: str, atype: str, target: str, content: str):
    body = {"name": name, "type": ttype, "schedule": schedule,
            "action_type": atype, "action_target": target, "action_content": content,
            "enabled": True}
    d = _r_json("POST", "/api/timers", json_body=body)
    if d.get("ok"):
        _ok(f"已创建 id={d['timer']['id']}")
    else:
        _err(d.get("error", "创建失败"))


@timer.command("run")
@click.argument("tid")
def timer_run(tid: str):
    """立即手动执行一次."""
    d = _r_json("POST", f"/api/timers/{tid}/run")
    _emit(d)


@timer.command("toggle")
@click.argument("tid")
@click.option("--off/--on", default=False)
def timer_toggle(tid: str, off: bool):
    d = _r_json("PATCH", f"/api/timers/{tid}", json_body={"enabled": not off})
    _ok(f"{'已暂停' if off else '已启用'} {tid}")


@timer.command("rm")
@click.argument("tid")
def timer_rm(tid: str):
    d = _r_json("DELETE", f"/api/timers/{tid}")
    _ok(f"已删除 {tid}")


@timer.command("history")
@click.argument("tid")
def timer_history(tid: str):
    d = _r_json("GET", f"/api/timers/{tid}/history")
    history = d.get("history", [])
    if not history:
        _info("无历史")
        return
    for h in history[-20:]:
        ts = time.strftime("%m-%d %H:%M:%S", time.localtime(h.get("ts", 0)))
        status_color = "green" if h.get("status") == "ok" else "red"
        click.echo(f"  {ts}  {_c(h.get('status', '?'), status_color)}  {str(h.get('result', ''))[:80]}")


# ── sessions ───────────────────────────────────────────────
@cli.group()
def sessions():
    """会话管理."""


@sessions.command("list")
@click.option("-n", "--limit", default=20)
def sessions_list(limit: int):
    sess = _r_json("GET", "/api/sessions")
    if not isinstance(sess, list):
        _err("意外响应格式")
    for s in sess[:limit]:
        ts = time.strftime("%m-%d %H:%M", time.localtime(s.get("last_used", 0)))
        click.echo(f"  {_c(s['id'], 'cyan')}  msgs={s.get('count', 0):>3}  {ts}  {s.get('name', '')[:50]}")


@sessions.command("show")
@click.argument("sid")
def sessions_show(sid: str):
    d = _r_json("GET", f"/api/sessions/{sid}")
    msgs = d.get("messages", [])
    click.echo(_c(f"=== {d.get('name', sid)} ({len(msgs)} msgs) ===", "bold"))
    for m in msgs[-15:]:
        role = m.get("role", "?")
        color = {"user": "blue", "assistant": "green"}.get(role, "gray")
        ts = time.strftime("%H:%M:%S", time.localtime(m.get("ts", 0)))
        click.echo(f"{_c(role, color)}  {ts}")
        click.echo(f"  {m.get('content', '')[:300]}")


@sessions.command("rm")
@click.argument("sid")
@click.option("-y", "--yes", is_flag=True)
def sessions_rm(sid: str, yes: bool):
    if not yes:
        click.confirm(f"确认删除 session {sid}?", abort=True)
    d = _r_json("DELETE", f"/api/sessions/{sid}")
    _ok(f"已删除 {sid}")


@sessions.command("export")
@click.argument("sid")
@click.option("--fmt", type=click.Choice(["md", "json"]), default="md")
@click.option("-o", "--output", default=None, help="输出文件 (默认 stdout)")
def sessions_export(sid: str, fmt: str, output: Optional[str]):
    r = _req("GET", f"/api/sessions/{sid}/export", params={"fmt": fmt})
    if r.status_code != 200:
        _err(f"导出失败 HTTP {r.status_code}")
    if output:
        Path(output).write_bytes(r.content)
        _ok(f"已导出 → {output}")
    else:
        click.echo(r.text)


@sessions.command("truncate")
@click.argument("sid")
@click.option("--keep", required=True, type=int)
def sessions_truncate(sid: str, keep: int):
    d = _r_json("POST", f"/api/sessions/{sid}/truncate", json_body={"keep": keep})
    _emit(d)


@sessions.command("abort")
@click.argument("sid")
def sessions_abort(sid: str):
    """中断正在跑的一轮 (跨端: 同 Web ⏹ / WeChat 抢占)."""
    d = _r_json("POST", f"/api/interrupt/{sid}")
    if d.get("ok"):
        _ok(f"已中断 {_c(sid[:12], 'cyan')} (method={d.get('method', 'flag')})")
    else:
        _err(d.get("error", "中断失败"))


@sessions.command("resume")
@click.argument("sid")
@click.option("--text", default="请从上次中断的位置继续", help="继续时给 LLM 的提示语")
@click.option("--stream/--no-stream", default=True, help="是否流式显示")
def sessions_resume(sid: str, text: str, stream: bool):
    """在被中断/结束的会话上追一条"继续"用户消息, 重启一轮.

    默认发 "请从上次中断的位置继续"; --text 自定义提示语.
    """
    if stream:
        sess = _session()
        with sess.post(f"{WEB_URL}/api/chat/{sid}", json={"message": text},
                       stream=True, timeout=(8, 600)) as r:
            if r.status_code != 200:
                _err(f"HTTP {r.status_code}: {r.text[:200]}")
            _info(f"resume → {sid[:12]} (text=\"{text[:40]}\")")
            printed = 0
            _rs = {"on": False}   # 是否正处在思考段 (思考→正文切换时补断行)
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    obj = json.loads(body)
                    delta = ((obj.get("choices") or [{}])[0].get("delta") or {})
                    # [thinking-cli 2026-08-26] 思考流也显示, 与 chat 子命令一致
                    _rc = delta.get("reasoning") or ""
                    if _rc:
                        if not _rs["on"]:
                            click.echo(_c("\n💭 ", "gray"), nl=False)
                            _rs["on"] = True
                        click.echo(_c(_rc, "gray"), nl=False)
                    chunk = delta.get("content") or ""
                    if chunk:
                        if _rs["on"]:
                            click.echo()
                            _rs["on"] = False
                        click.echo(chunk, nl=False)
                        printed += len(chunk)
                except Exception:
                    pass
            click.echo()
            _ok(f"resume 完 ({printed} 字符)")
    else:
        d = _r_json("POST", f"/api/chat/{sid}", json_body={"message": text}, timeout=600)
        _emit(d)


# ── ws (workspace) ─────────────────────────────────────────
@cli.group()
def ws():
    """Workspace 文件浏览."""


@ws.command("ls")
@click.argument("path", default="")
def ws_ls(path: str):
    d = _r_json("GET", "/api/workspace/files", params={"path": path})
    for f in d.get("files", []):
        icon = "📁" if f["is_dir"] else "📄"
        size = f"{f['size']:>10}" if not f["is_dir"] else " " * 10
        click.echo(f"  {icon}  {size}  {f['name']}")


@ws.command("cat")
@click.argument("path")
def ws_cat(path: str):
    r = _req("GET", "/api/workspace/preview", params={"path": path})
    if r.status_code != 200:
        _err(f"HTTP {r.status_code}")
    ct = r.headers.get("content-type", "")
    if "json" in ct:
        d = r.json()
        if d.get("type") == "text":
            click.echo(d.get("content", ""))
        else:
            _info(f"{d.get('type', '?')} 文件: {path} ({d.get('size', '?')} bytes)")
    else:
        # 二进制 (image/pdf) — 不输出，提示用 download
        _info(f"二进制 {ct}, 用 'litecli ws download {path}' 下载查看")


@ws.command("download")
@click.argument("path")
@click.option("-o", "--output", default=None)
def ws_download(path: str, output: Optional[str]):
    r = _req("GET", "/api/workspace/download", params={"path": path})
    if r.status_code != 200:
        _err(f"HTTP {r.status_code}")
    out = output or Path(path).name
    Path(out).write_bytes(r.content)
    _ok(f"已下载 {len(r.content)} bytes → {out}")


# ── preview (绝对路径文件) ──────────────────────────────────
@cli.command()
@click.argument("path")
def preview(path: str):
    """预览绝对路径文件 (用 /api/preview 的白名单端点)."""
    r = _req("GET", "/api/preview", params={"path": path})
    if r.status_code != 200:
        _err(f"HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    typ = d.get("type", "?")
    if typ == "text":
        click.echo(d.get("content", ""))
    else:
        _info(f"{typ}: {d.get('name', '')} ({d.get('size', '?')} bytes)")
        _info(f"  用 litecli ws download / 浏览器打开 {WEB_URL}/api/media?path={path}")


# ── dag ────────────────────────────────────────────────────
@cli.group()
def dag():
    """DAG 编排."""


@dag.command("list")
def dag_list():
    d = _r_json("GET", "/api/dags")
    for x in d.get("dags", []):
        if isinstance(x, dict):
            click.echo(f"  {_c(x.get('name', '?'), 'cyan')}  steps={len(x.get('steps', []))}")
        else:
            click.echo(f"  {_c(str(x), 'cyan')}")


@dag.command("show")
@click.argument("name")
def dag_show(name: str):
    d = _r_json("GET", f"/api/dags/{name}")
    click.echo(json.dumps(d, ensure_ascii=False, indent=2))


@dag.command("run")
@click.argument("name")
def dag_run(name: str):
    d = _r_json("POST", f"/api/dags/{name}/run")
    if d.get("ok"):
        _ok(f"已启动 job_id={d.get('job_id')} step_count={d.get('step_count')}")
    else:
        _err(d.get("error", "启动失败"))


@dag.command("rm")
@click.argument("name")
def dag_rm(name: str):
    _r_json("DELETE", f"/api/dags/{name}")
    _ok(f"已删除 {name}")


@dag.command("jobs")
def dag_jobs():
    d = _r_json("GET", "/api/dags/jobs")
    for j in d.get("jobs", []):
        ts = time.strftime("%m-%d %H:%M", time.localtime(j.get("started", 0)))
        status_color = {"done": "green", "running": "yellow", "failed": "red"}.get(j.get("status", ""), "gray")
        click.echo(f"  {_c(j['job_id'], 'cyan')}  {j.get('name', '')}  {_c(j.get('status', '?'), status_color)}  {ts}")


@dag.command("nl-gen")
@click.argument("request", nargs=-1, required=True)
@click.option("--name", default="", help="生成后保存为该名称")
def dag_nl_gen(request: tuple[str, ...], name: str):
    """自然语言生成 DAG. 例: litecli dag nl-gen 抓 hackernews 排序发到微信"""
    req = " ".join(request).strip()
    d = _r_json("POST", "/api/dags/generate", json_body={"request": req})
    steps = d.get("steps", [])
    if not steps:
        _err(d.get("error") or "生成失败")
    _ok(f"生成 {len(steps)} 步")
    for s in steps:
        deps = ",".join(s.get("depends_on") or []) or "-"
        click.echo(f"  {_c(s.get('id', '?'), 'cyan')}  [{s.get('agent_type', '?')}]  {s.get('task', '')[:60]}  ← {deps}")
    if name:
        _r_json("PUT", f"/api/dags/{name}", json_body=d)
        _ok(f"已保存为 {_c(name, 'cyan')}")


@dag.command("nl-edit")
@click.argument("name")
@click.argument("instruction", nargs=-1, required=True)
@click.option("--save/--dry", default=False, help="--save 直写文件, 默认 dry-run 只返回新版")
def dag_nl_edit(name: str, instruction: tuple[str, ...], save: bool):
    """自然语言修改已有 DAG. 例: litecli dag nl-edit weekly 把最后一步换成写周报"""
    instr = " ".join(instruction).strip()
    d = _r_json("POST", f"/api/dags/{name}/nl_edit", json_body={"instruction": instr, "save": save})
    if not d.get("ok"):
        _err(d.get("error") or "修改失败")
    dag_new = d.get("dag") or {}
    steps = dag_new.get("steps", [])
    _ok(f"修改后 {len(steps)} 步 ({'已保存' if d.get('saved') else 'dry-run'})")
    for s in steps:
        deps = ",".join(s.get("depends_on") or []) or "-"
        click.echo(f"  {_c(s.get('id', '?'), 'cyan')}  [{s.get('agent_type', '?')}]  {s.get('task', '')[:60]}  ← {deps}")


@dag.command("nl-rm")
@click.argument("query", nargs=-1, required=True)
@click.option("-y", "--yes", is_flag=True, help="确认删除, 不加只列候选")
def dag_nl_rm(query: tuple[str, ...], yes: bool):
    """自然语言删 DAG. 默认列候选, -y 才真删. 例: litecli dag nl-rm pptx"""
    q = " ".join(query).strip()
    d = _r_json("POST", "/api/dags/nl_delete", json_body={"query": q, "confirm": yes})
    cands = d.get("candidates", [])
    if not cands:
        _err(d.get("message") or "无匹配")
    if not yes:
        _info(f"匹配 {len(cands)} 个 (加 -y 才真删):")
        for c in cands:
            click.echo(f"  · {_c(c['name'], 'cyan')}  [{c['match']}]  {c.get('description', '')}")
    else:
        deleted = d.get("deleted", [])
        _ok(f"已删 {len(deleted)} 个: {', '.join(deleted)}")


@dag.command("nl-cron")
@click.argument("name")
@click.argument("when", nargs=-1, required=True)
def dag_nl_cron(name: str, when: tuple[str, ...]):
    """自然语言给 DAG 定时. 例: litecli dag nl-cron weekly 每周一早上 9 点"""
    w = " ".join(when).strip()
    d = _r_json("POST", "/api/dags/nl_schedule", json_body={"name": name, "when": w})
    if not d.get("ok"):
        _err(d.get("error") or "定时创建失败")
    t = d.get("timer") or {}
    _ok(f"已创建定时 {_c(t.get('id', '?'), 'cyan')}  cron={_c(d.get('cron', ''), 'yellow')}  ({d.get('human', '')})")


# ── wechat ─────────────────────────────────────────────────
@cli.group()
def wechat():
    """微信桥."""


@wechat.command("status")
def wechat_status():
    d = _r_json("GET", "/api/wechat/status")
    _emit(d)


@wechat.command("add")
@click.argument("bot_id", required=False, default="")
def wechat_add(bot_id: str):
    """新增 bot 并触发登录 (bot_id 可空, 服务端自动分配, 首次要扫二维码)."""
    body = {"bot_id": bot_id} if bot_id else {}
    d = _r_json("POST", "/api/wechat/bots", json_body=body)
    _emit(d)


@wechat.command("rm")
@click.argument("bot_id")
def wechat_rm(bot_id: str):
    d = _r_json("DELETE", f"/api/wechat/bots/{bot_id}")
    _emit(d)


@wechat.command("relogin")
@click.argument("bot_id")
def wechat_relogin(bot_id: str):
    d = _r_json("POST", f"/api/wechat/bots/{bot_id}/relogin")
    _emit(d)


@wechat.command("send")
@click.argument("bot_id")
@click.argument("to")
@click.argument("text")
def wechat_send(bot_id: str, to: str, text: str):
    d = _r_json("POST", f"/api/wechat/bots/{bot_id}/send", json_body={"to": to, "text": text})
    _emit(d)


@wechat.command("bots")
@click.argument("bot_id", required=False)
def wechat_bots(bot_id: Optional[str]):
    if bot_id:
        d = _r_json("GET", f"/api/wechat/bots/{bot_id}")
    else:
        d = _r_json("GET", "/api/wechat/status")
        d = {"bots": d.get("bots", [])}
    _emit(d)


# ── wecom (企业微信智能机器人) ────────────────────────────
@cli.group()
def wecom():
    """企业微信智能机器人桥 (与 /wechat 对称)."""


@wecom.command("status")
def wecom_status():
    d = _r_json("GET", "/api/wecom/status")
    _emit(d)


@wecom.command("add")
@click.argument("bot_id")
@click.argument("secret")
def wecom_add(bot_id: str, secret: str):
    """新增 bot 并上线."""
    d = _r_json("POST", "/api/wecom/bots",
                json_body={"bot_id": bot_id, "secret": secret})
    _emit(d)


@wecom.command("rm")
@click.argument("bot_id")
def wecom_rm(bot_id: str):
    d = _r_json("DELETE", f"/api/wecom/bots/{bot_id}")
    _emit(d)


@wecom.command("relogin")
@click.argument("bot_id")
def wecom_relogin(bot_id: str):
    d = _r_json("POST", f"/api/wecom/bots/{bot_id}/relogin")
    _emit(d)


@wecom.command("send")
@click.argument("bot_id")
@click.argument("userid")
@click.argument("markdown")
def wecom_send(bot_id: str, userid: str, markdown: str):
    """主动推送 markdown 消息."""
    d = _r_json("POST", f"/api/wecom/bots/{bot_id}/send",
                json_body={"userid": userid, "markdown": markdown})
    _emit(d)


@wecom.command("bots")
@click.argument("bot_id", required=False)
def wecom_bots(bot_id: Optional[str]):
    if bot_id:
        d = _r_json("GET", f"/api/wecom/bots/{bot_id}")
    else:
        d = _r_json("GET", "/api/wecom/status")
        d = {"bots": d.get("bots", [])}
    _emit(d)


@wecom.command("messages")
@click.argument("bot_id")
@click.option("--limit", default=20, help="返回条数 (默认 20)")
@click.option("--userid", default="", help="按 userid 过滤")
def wecom_messages(bot_id: str, limit: int, userid: str):
    q = f"?limit={limit}" + (f"&userid={userid}" if userid else "")
    d = _r_json("GET", f"/api/wecom/bots/{bot_id}/messages{q}")
    _emit(d)


@wecom.command("contacts")
@click.argument("bot_id")
def wecom_contacts(bot_id: str):
    d = _r_json("GET", f"/api/wecom/bots/{bot_id}/contacts")
    _emit(d)


# ── docker (#44) ───────────────────────────────────────────
@cli.group()
def docker():
    """Docker 容器管理 (走 web_ui /api/docker/*)."""


@docker.command("ps")
@click.option("-a", "--all", "all_", is_flag=True, help="含已停止")
def docker_ps(all_: bool):
    d = _r_json("GET", f"/api/docker/ps?all={1 if all_ else 0}")
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    for c in d.get("containers", []):
        scope = _c("*", "green") if c.get("in_scope") else _c("-", "gray")
        state_c = "green" if c.get("state") == "running" else "yellow"
        click.echo(f"  {scope} {_c(c.get('id',''), 'gray')}  {_c(c.get('name',''), 'cyan')}  {_c(c.get('state',''), state_c)}  {c.get('image','')}  {c.get('status','')}")
    click.echo(_c(f"  ({d.get('count',0)} containers, * = in scope)", "gray"))


@docker.command("logs")
@click.argument("name")
@click.option("-n", "--tail", default=100, help="尾部行数 (上限 1000)")
def docker_logs(name: str, tail: int):
    d = _r_json("GET", f"/api/docker/logs?name={name}&tail={tail}")
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    click.echo(d.get("text", ""))


@docker.command("stats")
def docker_stats():
    d = _r_json("GET", "/api/docker/stats")
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    click.echo(f"  {'NAME':16s} {'CPU%':>7s}  {'MEM':>10s}  {'MEM%':>6s}")
    for r in d.get("stats", []):
        click.echo(f"  {r.get('name',''):16s} {r.get('cpu_pct',0):>7.2f}  {r.get('mem_mb',0):>7.1f}MB  {r.get('mem_pct',0):>5.1f}%")


@docker.command("inspect")
@click.argument("name")
def docker_inspect(name: str):
    d = _r_json("GET", f"/api/docker/inspect?name={name}")
    _emit(d)


@docker.command("restart")
@click.argument("name")
def docker_restart(name: str):
    d = _r_json("POST", "/api/docker/restart", json_body={"name": name})
    if d.get("ok"):
        _ok(f"restart {name} ✓")
    else:
        _err(d.get("error", "unknown"))


@docker.command("stop")
@click.argument("name")
def docker_stop(name: str):
    d = _r_json("POST", "/api/docker/stop", json_body={"name": name})
    if d.get("ok"):
        _ok(f"stop {name} ✓")
    else:
        _err(d.get("error", "unknown"))


@docker.command("start")
@click.argument("name")
def docker_start(name: str):
    d = _r_json("POST", "/api/docker/start", json_body={"name": name})
    if d.get("ok"):
        _ok(f"start {name} ✓")
    else:
        _err(d.get("error", "unknown"))


# ── plugins ─────────────────────────────────────────────────
@cli.group()
def plugins():
    """Plugin registry 内省 / 热重扫 (走 /api/plugins*)."""


@plugins.command("list")
@click.option("-e", "--errors-only", is_flag=True, help="只列加载失败的插件")
def plugins_list(errors_only: bool):
    d = _r_json("GET", "/api/plugins")
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    errs = d.get("errors", []) or []
    tools = d.get("tools", []) or []
    if errors_only:
        if not errs:
            _ok("no plugin load errors")
            return
        for e in errs:
            click.echo(f"  {_c('✗', 'red')}  {_c(e.get('path',''), 'gray')}  {e.get('err','')[:200]}")
        return
    if os.environ.get("LITECLI_OUTPUT") == "json":
        _emit(d)
        return
    click.echo(_c(f"  registry: {d.get('count',0)} tools, {len(errs)} errors", "cyan"))
    for t in tools:
        caps = t.get("capabilities", {}) or {}
        badges = []
        if caps.get("safe") is False: badges.append(_c("unsafe", "yellow"))
        if caps.get("long_running"): badges.append(_c("long", "gray"))
        if caps.get("produces_artifact"): badges.append(_c("artifact", "cyan"))
        if caps.get("auth_required"): badges.append(_c("auth", "magenta"))
        badge_s = " ".join(badges)
        name   = (t.get("name", "") or "")[:24].ljust(24)
        origin = (t.get("origin", "") or "?")[:10].ljust(10)
        click.echo(f"  {_c(name, 'green')}  {_c(origin, 'gray')}  {badge_s}")
    if errs:
        click.echo(_c(f"\n  {len(errs)} error(s):", "red"))
        for e in errs:
            click.echo(f"    ✗ {_c(e.get('path',''), 'gray')}  {e.get('err','')[:160]}")


@plugins.command("reload")
def plugins_reload():
    d = _r_json("POST", "/api/plugins/reload")
    if not d.get("ok"):
        _err(d.get("error", "unknown"))
    before = d.get("count_before", 0)
    after  = d.get("count_after", 0)
    ec     = d.get("error_count", 0)
    delta  = after - before
    arrow  = _c(f"({delta:+d})", "green" if delta >= 0 else "red")
    _ok(f"plugin registry rescanned: {before} → {after} {arrow}, {ec} error(s)")
    for e in (d.get("errors") or [])[:20]:
        click.echo(f"    {_c('✗', 'red')} {_c(e.get('path',''), 'gray')}  {e.get('err','')[:160]}")


# ── projects ───────────────────────────────────────────────
@cli.group()
def projects():
    """项目管理."""


@projects.command("list")
def projects_list():
    d = _r_json("GET", "/api/projects")
    for p in d.get("projects", []):
        click.echo(f"  {_c(p.get('id', '?'), 'cyan')}  {p.get('name', '')}  ({p.get('type', '?')})  status={p.get('status', '?')}")


@projects.command("show")
@click.argument("pid")
def projects_show(pid: str):
    d = _r_json("GET", f"/api/projects/{pid}")
    _emit(d)


@projects.command("create")
@click.option("--name", required=True)
@click.option("--type", "ptype", default="general")
@click.option("--description", default="")
def projects_create(name: str, ptype: str, description: str):
    d = _r_json("POST", "/api/projects", json_body={"name": name, "type": ptype, "description": description})
    _ok(f"已创建 {d.get('id')} ({d.get('name')})")


@projects.command("rm")
@click.argument("pid")
def projects_rm(pid: str):
    _r_json("DELETE", f"/api/projects/{pid}")
    _ok(f"已删除 {pid}")


# ── config ─────────────────────────────────────────────────
@cli.group()
def config():
    """读写 config.json (非敏感字段)."""


@config.command("get")
def config_get():
    d = _r_json("GET", "/api/config")
    click.echo(json.dumps(d.get("config", {}), ensure_ascii=False, indent=2))


@config.command("set")
@click.argument("section")
@click.argument("kv_pairs", nargs=-1)
def config_set(section: str, kv_pairs):
    """设置 config[section][key]=value, 例: litecli config set agent task_timeout_seconds=3600"""
    if not kv_pairs:
        _err("需要至少 1 个 key=value")
    body = {section: {}}
    for kv in kv_pairs:
        if "=" not in kv:
            _err(f"格式错: {kv} (应为 key=value)")
        k, v = kv.split("=", 1)
        try:
            v = json.loads(v)
        except Exception:
            pass
        body[section][k] = v
    d = _r_json("PATCH", "/api/config", json_body=body)
    _emit(d)


# ── memory / stats / health ────────────────────────────────
@cli.command()
@click.argument("sid")
def memory(sid: str):
    """显示 session 的记忆 (L1/L2/context_files)."""
    d = _r_json("GET", f"/api/memory/{sid}")
    click.echo(f"  ctx={d.get('context_window')}  used={d.get('token_estimate')} ({d.get('pct')}%)")
    if d.get("l1"):
        click.echo(_c(f"  L1: {d['l1']['lines']} 行 / {d['l1']['tokens']} tok", "cyan"))
    for l2 in d.get("l2", []):
        click.echo(f"  L2 {l2['topic']}: {l2['tokens']} tok")
    for cf in d.get("context_files", []):
        click.echo(f"  ctx {cf['name']}: {cf['tokens']} tok")


@cli.command()
def stats():
    """显示用量统计 + 估算成本."""
    d = _r_json("GET", "/api/stats")
    _emit(d)


@cli.command()
def health():
    """健康检查."""
    d = _r_json("GET", "/api/health")
    _emit(d)


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
        click.echo(r.text)
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


if __name__ == "__main__":
    cli()
