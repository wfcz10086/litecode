"""sessions 子命令组: 会话列表/查看/导出/截断/中断/续跑 + 重命名/会话文件/分享/同步/接回后台轮次 (从 litecli.py 拆出)."""
import json
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import click

from .common import (
    _c,
    _err,
    _ok,
    _info,
    _session,
    _req,
    _r_json,
    _emit,
)
from . import common as _common  # WEB_URL 实时读取: --url 覆盖只改 common 一处
from .common import _json_out, _none_if_empty


# ── sessions ───────────────────────────────────────────────
@click.group()
def sessions():
    """会话管理."""


@sessions.command("list")
@click.option("-n", "--limit", default=20)
def sessions_list(limit: int):
    sess = _r_json("GET", "/api/sessions")
    if _json_out(sess[:limit] if isinstance(sess, list) else sess):
        return
    if not isinstance(sess, list):
        _err("意外响应格式")
    for s in _none_if_empty(sess[:limit]):
        ts = time.strftime("%m-%d %H:%M", time.localtime(s.get("last_used", 0)))
        click.echo(f"  {_c(s['id'], 'cyan')}  msgs={s.get('count', 0):>3}  {ts}  {s.get('name', '')[:50]}")


@sessions.command("show")
@click.argument("sid")
def sessions_show(sid: str):
    d = _r_json("GET", f"/api/sessions/{sid}")
    if _json_out(d):
        return
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
        with sess.post(f"{_common.WEB_URL}/api/chat/{sid}", json={"message": text},
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


# ── 会话高级操作: 重命名 / 会话文件 / 分享链接 / 同步 / 接回后台轮次 ─────────
# 接口见 routers/sessions_router.py 与 routers/chat_router.py
@sessions.command("rename")
@click.argument("sid")
@click.argument("name", nargs=-1, required=True)
def sessions_rename(sid: str, name: tuple[str, ...]):
    """重命名会话. 例: litecli sessions rename <sid> 周报整理"""
    new_name = " ".join(name).strip()
    if not new_name:
        _err("名字不能为空")
    d = _r_json("PATCH", f"/api/sessions/{sid}", json_body={"name": new_name})
    _ok(f"已重命名 {_c(sid[:12], 'cyan')} → {d.get('name', new_name)}")


@sessions.command("files")
@click.argument("sid")
def sessions_files(sid: str):
    """列出会话目录下的 MD 文件 (含 memory/)."""
    d = _r_json("GET", f"/api/sessions/{sid}/files")
    if _json_out(d):
        return
    for f in _none_if_empty(d.get("files", [])):
        ts = time.strftime("%m-%d %H:%M", time.localtime(f.get("mtime", 0)))
        tag = _c(" 可编辑", "gray") if f.get("editable") else ""
        click.echo(f"  📄 {f.get('size', 0):>8}  {ts}  {_c(f.get('name', '?'), 'cyan')}{tag}")


@sessions.command("file")
@click.argument("sid")
@click.argument("name")
def sessions_file(sid: str, name: str):
    """读取会话文件内容. 例: litecli sessions file <sid> memory/notes.md"""
    d = _r_json("GET", f"/api/sessions/{sid}/files/{quote(name, safe='/')}")
    if _json_out(d):
        return
    click.echo(d.get("content", ""))


def _share_url(token: str) -> str:
    # 人读页面 /share/{token} (免登录); 原始 JSON 仍可走 /api/share/{token}
    return f"{_common.WEB_URL}/share/{token}"


def _fmt_ts(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-"


@sessions.command("share")
@click.argument("sid")
@click.option("--ttl", "ttl_sec", type=int, default=7 * 86400, show_default=True,
              help="有效期 (秒), 默认 7 天")
def sessions_share(sid: str, ttl_sec: int):
    """为会话创建只读分享链接并打印完整 URL."""
    d = _r_json("POST", f"/api/sessions/{sid}/share", json_body={"ttl_sec": ttl_sec, "read_only": True})
    if d.get("token"):
        d["url"] = _share_url(d["token"])
    if _json_out(d):
        return
    if not d.get("token"):
        _err(f"创建分享失败: {d}")
    _ok(f"已创建分享 (到期 {_fmt_ts(d.get('expires'))})")
    click.echo(f"  {d['url']}")


@sessions.command("shares")
@click.argument("sid")
def sessions_shares(sid: str):
    """列出会话的有效分享链接."""
    d = _r_json("GET", f"/api/sessions/{sid}/shares")
    for s in d.get("shares", []) or []:
        s["url"] = _share_url(s.get("token", ""))
    if _json_out(d):
        return
    for s in _none_if_empty(d.get("shares", [])):
        ro = "只读" if s.get("read_only", True) else "可写"
        click.echo(f"  {_c(s.get('token', '?'), 'cyan')}  到期 {_fmt_ts(s.get('expires'))}  {ro}"
                   f"  by {s.get('created_by', '?')}")
        click.echo(_c(f"    {s['url']}", "gray"))


@sessions.command("unshare")
@click.argument("sid")
@click.argument("token")
def sessions_unshare(sid: str, token: str):
    """撤销会话的一个分享链接."""
    d = _r_json("DELETE", f"/api/sessions/{sid}/share/{token}")
    if d.get("ok"):
        _ok(f"已撤销分享 {token[:8]}…")
    else:
        _err(f"撤销失败: token {token[:8]}… 不存在或已过期")


@sessions.command("sync")
@click.argument("sid")
def sessions_sync(sid: str):
    """从 server 端会话存储补齐最新回复 (同 Web 刷新时的同步)."""
    d = _r_json("GET", f"/api/sessions/{sid}/sync")
    if _json_out(d):
        return
    if d.get("error"):
        _err(f"同步失败: {d['error']}")
    _ok(f"{'已同步新回复' if d.get('synced') else '已是最新'}  msgs={len(d.get('messages', []))}"
        f"  still_running={d.get('still_running', False)}")
    if d.get("last_assistant"):
        click.echo(_c("  最近回复: ", "cyan") + " ".join(d["last_assistant"].split())[:200])


def _print_sse(lines, hide_reasoning: bool = False) -> dict:
    """解析 /api/chat 系的 SSE 帧并实时打印 (口径同 litecli chat); 遇 [DONE] 或流结束返回。
    返回 {"content": 拼好的正文, "done": 是否见到 [DONE], "errors": [...]}."""
    out = {"content": "", "done": False, "errors": []}
    in_reasoning = False
    for raw in lines:
        if not raw:
            continue
        s = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        if not s.startswith("data:"):
            continue  # 心跳注释 ": keep-alive" 等
        data = s[5:].strip()
        if data == "[DONE]":
            out["done"] = True
            break
        try:
            chunk = json.loads(data)
        except Exception:
            continue
        if isinstance(chunk, dict) and "error" in chunk:
            out["errors"].append(str(chunk["error"]))
            click.echo(_c(f"\n[error] {chunk['error']}", "red"))
            continue
        try:
            delta = chunk["choices"][0]["delta"] or {}
        except (KeyError, IndexError, TypeError):
            continue
        rc = delta.get("reasoning") or ""
        if rc and not hide_reasoning:
            if not in_reasoning:
                click.echo(_c("\n💭 ", "gray"), nl=False)
                in_reasoning = True
            click.echo(_c(rc, "gray"), nl=False)
        ct = delta.get("content") or ""
        if ct:
            if in_reasoning:
                click.echo()
                in_reasoning = False
            click.echo(ct, nl=False)
            out["content"] += ct
        for key in ("task_exec", "data_collect", "task_analysis"):
            ev = delta.get(key)
            if isinstance(ev, dict) and ev.get("status") == "executing" and ev.get("key") != "preparing":
                lbl = ev.get("agent_label") or ev.get("agent", "")
                tag = f"[{lbl}] " if lbl else ""
                click.echo(_c(f"\n[🔧 {tag}{(ev.get('detail') or '')[:120]}]", "yellow"))
    click.echo()
    return out


@sessions.command("attach")
@click.argument("sid")
@click.option("--start", type=int, default=0, show_default=True,
              help="从第 N 帧开始回放 (0 = 从本轮开头完整回放)")
@click.option("--hide-reasoning", is_flag=True, help="不显示模型思考流")
def sessions_attach(sid: str, start: int, hide_reasoning: bool):
    """接回会话里正在后台跑的一轮对话, 实时打印后续回复 (Ctrl-C 只断开, 不中断轮次)."""
    st = _r_json("GET", f"/api/chat/{sid}/run")
    if not st.get("running"):
        _info(f"会话 {sid[:12]} 当前没有进行中的轮次"
              + ("" if not st.get("frames") else f" (上一轮已结束, {st['frames']} 帧)")
              + "; 用 litecli sessions show 查看历史")
        return
    _info(f"attach → {sid[:12]} (已产出 {st.get('frames', 0)} 帧, 从第 {start} 帧回放)")
    r = _req("GET", f"/api/chat/{sid}/stream", params={"start": start}, timeout=(8, 600), stream=True)
    if r.status_code == 404:
        _info("该轮次刚刚结束, 用 litecli sessions show 查看结果")
        return
    if r.status_code != 200:
        _err(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        res = _print_sse(r.iter_lines(), hide_reasoning=hide_reasoning)
    except KeyboardInterrupt:
        click.echo()
        _info("已断开 (后台轮次继续运行)")
        return
    finally:
        r.close()
    _ok(f"本轮结束 ({len(res['content'])} 字符)" if res["done"] else f"流已结束 ({len(res['content'])} 字符)")


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [sessions]
