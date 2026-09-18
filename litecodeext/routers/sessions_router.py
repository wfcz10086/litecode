"""sessions_router.py — sessions CRUD + tombstones + 文件管理 + memory + sync + export.

为最小风险, helper 函数 (list_sessions / save_session / load_session / _sf /
_load_tombstones / _add_tombstone / _TOMBSTONE_FILE) 仍在 web_ui.py;
本 router 通过 `_wui()` 拿 web_ui 模块引用 (lazy, 避免循环 import).
"""
import json
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from routers.auth_router import require_auth
from routers.state import S
from lib.audit import audit_log
from lib.owner import current_user, require_owner
from lib.scrub import scrub_secrets, scrub_session
from lib import share as _share
from lib import trash as _trash

router = APIRouter()


def _wui():
    """Lazy import web_ui 主模块, 避免循环 import."""
    import web_ui  # type: ignore
    return web_ui


_EDITABLE_MDS = {"MEMORY.md", "USER.md", "TOOLS.md", "SOUL.md",
                 "IDENTITY.md", "HEARTBEAT.md", "BOOTSTRAP.md", "AGENTS.md"}


@router.get("/api/sessions")
async def api_sessions(request: Request):
    require_auth(request)
    return _wui().list_sessions()


@router.post("/api/sessions")
async def api_create(request: Request):
    require_auth(request)
    b = await request.json()
    d = _wui().create_session(b.get("name", "New Chat"), owner=current_user(request))
    audit_log(request, "session.create", d.get("id", ""), name=d.get("name", ""))
    return d


@router.get("/api/tombstones")
async def api_tombstones_list(request: Request):
    require_auth(request)
    return {"sids": sorted(_wui()._load_tombstones())}


@router.delete("/api/tombstones")
async def api_tombstones_clear(request: Request):
    require_auth(request)
    tf = _wui()._TOMBSTONE_FILE
    if tf.exists():
        try:
            tf.unlink()
        except Exception:
            pass
    return {"ok": True}


@router.delete("/api/tombstones/{sid}")
async def api_tombstones_remove(sid: str, request: Request):
    require_auth(request)
    wui = _wui()
    tbs = wui._load_tombstones()
    if sid in tbs:
        tbs.discard(sid)
        tmp = wui._TOMBSTONE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(tbs), ensure_ascii=False))
        tmp.replace(wui._TOMBSTONE_FILE)
    return {"ok": True}


@router.get("/api/sessions/{sid}")
async def api_get(sid: str, request: Request):
    require_auth(request)
    d = _wui().load_session(sid)
    require_owner(d, request)
    return d


@router.patch("/api/sessions/{sid}")
async def api_rename(sid: str, request: Request):
    require_auth(request)
    b = await request.json()
    wui = _wui()
    d = wui.load_session(sid)
    require_owner(d, request)
    old_name = d.get("name", "")
    if "name" in b:
        d["name"] = b["name"]
    wui.save_session(d)
    audit_log(request, "session.rename", sid, old=old_name, new=d["name"])
    return {"ok": True, "name": d["name"]}


@router.post("/api/sessions/{sid}/truncate")
async def api_truncate(sid: str, request: Request):
    """截断 session: 保留前 keep 条消息, 删除其后所有.
    [BUG-FIX 2026-05-21] 旧版只 truncate web cache, server-side session 不动.
    导致 msgRetry 后 send 时 server 把"已截掉的旧 user/assistant"+ 新 user 一起当历史
    → assistant 写完 reload, web cache 又从 server 拿回重复的 user msg.
    现在同步 truncate 两端: web cache + server /tmp/litecode_workspace/sessions/<sid>.json
    """
    import json
    from pathlib import Path
    require_auth(request)
    b = await request.json()
    keep = int(b.get("keep", 0))
    wui = _wui()
    d = wui.load_session(sid)
    require_owner(d, request)
    msgs = d.get("messages", [])
    if keep < 0: keep = 0
    if keep > len(msgs): keep = len(msgs)
    # [P0-#17] truncate 前 snapshot 到回收站 (24h TTL)
    _dropped = msgs[keep:]
    _trash_meta = None
    if _dropped:
        try:
            _trash_meta = _trash.snapshot(sid, keep, _dropped,
                                          reason=str(b.get("reason") or "truncate"))
        except Exception as _te:
            print(f"[truncate] trash snapshot fail: {_te}")
    d["messages"] = msgs[:keep]
    wui.save_session(d)
    # ── server-side session 同步 truncate ──
    server_dropped = 0
    try:
        _paths = wui.CFG.get("paths", {})
        _sessions_dir = Path(_paths.get("sessions_dir", "/tmp/litecode_workspace/sessions"))
        _srv_file = _sessions_dir / f"{sid}.json"
        if _srv_file.exists():
            srv_d = json.loads(_srv_file.read_text())
            srv_msgs = srv_d.get("msgs", [])
            # server 端 msgs 通常含 system; 找到 keep 等价位置:
            # web cache messages 不含 system, server msgs[0] 通常是 system.
            # 简化策略: truncate 到 web keep 等价 user/assistant 计数
            _kept_non_sys = 0
            _cut_idx = len(srv_msgs)
            for _i, _m in enumerate(srv_msgs):
                if _m.get("role") in ("user", "assistant", "tool"):
                    if _kept_non_sys >= keep:
                        _cut_idx = _i
                        break
                    _kept_non_sys += 1
            server_dropped = len(srv_msgs) - _cut_idx
            srv_d["msgs"] = srv_msgs[:_cut_idx]
            # atomic write
            _tmp = _srv_file.with_suffix(".tmp")
            _tmp.write_text(json.dumps(srv_d, ensure_ascii=False, indent=2))
            _tmp.replace(_srv_file)
    except Exception as _e:
        import logging; logging.getLogger("openclaw").warning(f"  [truncate] server-side fail: {_e}")
    audit_log(request, "session.truncate", sid,
              kept=keep, dropped=len(msgs) - keep, server_dropped=server_dropped,
              trash_id=(_trash_meta.get("trash_id") if _trash_meta else None))
    return {"ok": True, "kept": keep, "dropped": len(msgs) - keep,
            "server_dropped": server_dropped,
            "trash": _trash_meta}


@router.delete("/api/sessions/{sid}")
async def api_delete(sid: str, request: Request):
    require_auth(request)
    wui = _wui()
    # 若 session 存在, 校验 owner; 不存在 (load 返合成空 dict) 也允许 (前端墓碑用)
    try:
        _pre = wui.load_session(sid)
    except Exception:
        _pre = None
    if _pre:
        require_owner(_pre, request)
    deleted = {"web_session": False, "server_session": False, "session_dir": False}
    f = wui._sf(sid)
    if f.exists():
        try:
            f.unlink()
            deleted["web_session"] = True
        except Exception as e:
            print(f"[api_delete] unlink web_session {sid} failed: {e}")
    if S.aproxy is not None:
        try:
            await S.aproxy(f"/v1/sessions/{sid}/interrupt", "POST", timeout=3)
        except Exception:
            pass
        try:
            d = await S.aproxy(f"/v1/sessions/{sid}", "DELETE", timeout=5)
            deleted["server_session"] = bool(d and d.get("ok"))
        except Exception as e:
            print(f"[api_delete] proxy /v1/sessions/{sid} DELETE failed: {e}")
    try:
        _paths = S.cfg.get("paths", {})
        _ws = Path(_paths.get("workspace_base", "/tmp/litecode_workspace"))
        sess_sub = Path(_paths.get("sessions_dir", str(_ws / "sessions"))) / sid
        if sess_sub.exists() and sess_sub.is_dir():
            shutil.rmtree(sess_sub, ignore_errors=True)
            deleted["session_dir"] = not sess_sub.exists()
    except Exception as e:
        print(f"[api_delete] rmtree sessions/{sid}/ failed: {e}")
    try:
        wui._add_tombstone(sid)
    except Exception as e:
        print(f"[api_delete] tombstone add {sid} failed: {e}")
    print(f"[api_delete] {sid} → {deleted}")
    audit_log(request, "session.delete", sid, deleted=deleted)
    return {"ok": True, "deleted": deleted}


@router.get("/api/sessions/{sid}/export")
async def api_export(sid: str, request: Request, fmt: str = "md"):
    """导出会话为 Markdown 或 JSON."""
    require_auth(request)
    wui = _wui()
    session = wui.load_session(sid)
    require_owner(session, request)
    # [P0-#16] 导出前脱敏 (sk-*/Bearer/Basic/~/.ssh/env-*=)
    session = scrub_session(session)
    msgs = session.get("messages", [])
    if fmt == "json":
        content = json.dumps(session, ensure_ascii=False, indent=2)
        audit_log(request, "session.export", sid, fmt="json", bytes=len(content), scrubbed=True)
        return Response(content=content, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{sid}.json"'})
    lines = [f"# {session.get('name', sid)}", ""]
    for m in msgs:
        role = m.get("role", "?")
        ts = m.get("ts")
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else ""
        src = m.get("source", "")
        wx = m.get("wx_user", "")
        label = {"user": "用户", "assistant": "AI"}.get(role, role)
        if src == "wechat" and wx:
            label = f"微信·{wx}" if role == "user" else "AI(微信)"
        lines.append(f"### {label}  {ts_str}")
        lines.append("")
        lines.append(m.get("content", ""))
        lines.append("")
        tools = m.get("tools", [])
        if tools:
            lines.append(f"<details><summary>工具调用 ({len(tools)})</summary>\n")
            for t in tools[:20]:
                lines.append(f"- {t}")
            lines.append("\n</details>\n")
    content = "\n".join(lines)
    audit_log(request, "session.export", sid, fmt="md", bytes=len(content), scrubbed=True)
    return Response(content=content, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{sid}.md"'})


@router.get("/api/sessions/{sid}/files")
async def api_session_files(sid: str, request: Request):
    """列出 session 目录下所有 MD 文件."""
    require_auth(request)
    try:
        require_owner(_wui().load_session(sid), request)
    except HTTPException:
        raise
    except Exception:
        pass
    _paths = S.cfg.get("paths", {})
    _ws = Path(_paths.get("workspace_base", "/tmp/litecode_workspace"))
    sess_dir = Path(_paths.get("sessions_dir", str(_ws / "sessions"))) / sid
    files = []
    if sess_dir.exists():
        for f in sorted(sess_dir.glob("*.md")):
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
                "editable": f.name in _EDITABLE_MDS,
            })
        mem_dir = sess_dir / "memory"
        if mem_dir.exists():
            for f in sorted(mem_dir.glob("*.md")):
                files.append({
                    "name": f"memory/{f.name}",
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                    "editable": True,
                })
    return {"sid": sid, "files": files}


@router.get("/api/sessions/{sid}/files/{name:path}")
async def api_session_file_read(sid: str, name: str, request: Request):
    require_auth(request)
    try:
        require_owner(_wui().load_session(sid), request)
    except HTTPException:
        raise
    except Exception:
        pass
    _paths = S.cfg.get("paths", {})
    _ws = Path(_paths.get("workspace_base", "/tmp/litecode_workspace"))
    sess_dir = Path(_paths.get("sessions_dir", str(_ws / "sessions"))) / sid
    fp = sess_dir / name
    if not fp.exists():
        raise HTTPException(404, f"{name} not found")
    try:
        fp.resolve().relative_to(sess_dir.resolve())
    except ValueError:
        raise HTTPException(403, "forbidden")
    return {"name": name, "content": fp.read_text(errors="replace"), "size": fp.stat().st_size}


@router.put("/api/sessions/{sid}/files/{name:path}")
async def api_session_file_write(sid: str, name: str, request: Request):
    require_auth(request)
    try:
        require_owner(_wui().load_session(sid), request)
    except HTTPException:
        raise
    except Exception:
        pass
    basename = name.split("/")[-1]
    if basename not in _EDITABLE_MDS:
        raise HTTPException(403, f"{basename} is not editable")
    _paths = S.cfg.get("paths", {})
    _ws = Path(_paths.get("workspace_base", "/tmp/litecode_workspace"))
    sess_dir = Path(_paths.get("sessions_dir", str(_ws / "sessions"))) / sid
    fp = sess_dir / name
    fp.parent.mkdir(parents=True, exist_ok=True)
    body = await request.json()
    fp.write_text(body.get("content", ""))
    audit_log(request, "session.file_write", sid, file=name, bytes=fp.stat().st_size)
    return {"ok": True, "name": name, "size": fp.stat().st_size}


@router.get("/api/memory/{sid}")
async def api_mem(sid: str, request: Request):
    require_auth(request)
    if S.aproxy is not None:
        d = await S.aproxy(f"/v1/memory/{sid}")
        if d:
            return d
    # Fallback: 直接读 session 目录的 MEMORY.md 及上下文 MD
    _paths = S.cfg.get("paths", {})
    _agt = S.cfg.get("agent", {})
    ws = Path(_paths.get("workspace_base", _agt.get("workspace", "/tmp/litecode_workspace")))
    sess_base = Path(_paths.get("sessions_dir", str(ws / "sessions")))
    root = sess_base / sid
    l1 = root / "MEMORY.md"
    soft = int(S.ctx_window * 0.60)
    hard = int(S.ctx_window * 0.80)
    res = {
        "sid": sid, "l1": None, "l2": [], "archive": [], "context_files": [],
        "token_estimate": 0,
        "soft_limit": soft, "hard_limit": hard, "context_window": S.ctx_window, "pct": 0,
        "compress_pending": False, "compress_running": False,
    }
    _ctx_mds = ["BOOTSTRAP.md", "IDENTITY.md", "SOUL.md", "USER.md", "TOOLS.md",
                "HEARTBEAT.md", "AGENTS.md"]
    for _mdf in _ctx_mds:
        _mfp = root / _mdf
        if _mfp.exists():
            _mc = _mfp.read_text(errors="replace").strip()
            if _mc:
                res["context_files"].append({
                    "name": _mdf, "lines": len(_mc.splitlines()),
                    "tokens": len(_mc) // 4, "preview": _mc[:200], "content": _mc,
                })
    if l1.exists():
        c = l1.read_text()
        sects, cur = {}, None
        for ln in c.splitlines():
            if ln.startswith("## "):
                cur = ln[3:].strip()
                sects[cur] = []
            elif cur and ln.strip().startswith("- ") and len(sects[cur]) < 5:
                sects[cur].append(ln.strip()[2:])
        res["l1"] = {
            "content": c, "lines": len(c.splitlines()),
            "tokens": len(c) // 4,
            "sections": {k: v for k, v in sects.items() if v},
        }
    l2d = root / "memory"
    if l2d.exists():
        for f in sorted(l2d.glob("*.md")):
            c = f.read_text()
            res["l2"].append({
                "name": f.name, "topic": f.stem,
                "lines": len(c.splitlines()), "tokens": len(c) // 4, "preview": c[:150],
            })
        archd = l2d / "archive"
        if archd.exists():
            for f in sorted(archd.glob("*.md"), reverse=True):
                ts = f.stem.replace("l1_", "")
                try:
                    import datetime
                    dt = datetime.datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")
                except Exception:
                    dt = ts
                res["archive"].append({"name": f.name, "date": dt})
    _l1_tok = res["l1"]["tokens"] if res["l1"] else 0
    _l2_tok = sum(it.get("tokens", 0) for it in res["l2"])
    _ctx_tok = sum(it.get("tokens", 0) for it in res["context_files"])
    res["token_estimate"] = _l1_tok + _l2_tok + _ctx_tok
    if hard > 0:
        res["pct"] = min(100, int(res["token_estimate"] * 100 / hard))
    return res


@router.get("/api/sessions/{sid}/sync")
async def api_sync(sid: str, request: Request):
    """从 server session 存储读取最新消息同步到 web_sessions."""
    require_auth(request)
    wui = _wui()
    try:
        require_owner(wui.load_session(sid), request)
    except HTTPException:
        raise
    except Exception:
        pass
    try:
        _paths = S.cfg.get("paths", {})
        _agt = S.cfg.get("agent", {})
        ws = Path(_paths.get("workspace_base", _agt.get("workspace", "/tmp/litecode_workspace")))
        sess_base = Path(_paths.get("sessions_dir", str(ws / "sessions")))
        server_sess_file = sess_base / f"{sid}.json"
        web_sess = wui.load_session(sid)
        web_msgs = web_sess.get("messages", [])
        if not server_sess_file.exists():
            return {"synced": False, "messages": web_msgs, "last_assistant": "", "still_running": False}
        server_data = json.loads(server_sess_file.read_text(errors="replace"))
        server_msgs = server_data.get("msgs", [])
        server_ts = server_data.get("ts", 0)
        assistant_msgs = [m for m in server_msgs if m.get("role") == "assistant" and m.get("content")]
        last_text = assistant_msgs[-1]["content"] if assistant_msgs else ""
        web_user_msgs = [m for m in web_msgs if m.get("role") == "user"]
        web_assistant_msgs = [m for m in web_msgs if m.get("role") == "assistant"]
        still_running = (
            len(web_user_msgs) > len(web_assistant_msgs)
            and last_text == ""
            and (time.time() - server_ts) < 1800
        )
        synced = False
        if last_text:
            web_last_assistant = next((m for m in reversed(web_msgs) if m.get("role") == "assistant"), None)
            web_last_content = web_last_assistant.get("content", "") if web_last_assistant else ""
            if web_last_content != last_text:
                if web_msgs and web_msgs[-1].get("role") == "user":
                    web_sess["messages"].append({
                        "role": "assistant", "content": last_text,
                        "ts": time.time(), "tools": [], "diffs": [], "recovered": True,
                    })
                else:
                    for i in range(len(web_sess["messages"]) - 1, -1, -1):
                        if web_sess["messages"][i].get("role") == "assistant":
                            web_sess["messages"][i]["content"] = last_text
                            web_sess["messages"][i]["recovered"] = True
                            break
                web_sess["last_used"] = time.time()
                wui.save_session(web_sess)
                synced = True
        return {
            "synced": synced,
            "messages": web_sess.get("messages", []),
            "last_assistant": last_text[:300],
            "still_running": still_running,
        }
    except Exception as e:
        return {"synced": False, "messages": [], "last_assistant": "", "still_running": False, "error": str(e)}


# ── [P0-#16] share link ──────────────────────────────────────────
@router.post("/api/sessions/{sid}/share")
async def api_share_create(sid: str, request: Request):
    """签一个 share token. body 可选 {ttl_sec:int=604800, read_only:bool=true}."""
    require_auth(request)
    d = _wui().load_session(sid)
    require_owner(d, request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    rec = _share.create_share(
        sid=sid,
        created_by=current_user(request),
        ttl_sec=int(body.get("ttl_sec") or 7 * 86400),
        read_only=bool(body.get("read_only", True)),
    )
    audit_log(request, "share.create", sid,
              token=rec["token"][:8] + "…", expires=rec["expires"])
    return {"ok": True, **rec}


@router.get("/api/sessions/{sid}/shares")
async def api_share_list(sid: str, request: Request):
    require_auth(request)
    d = _wui().load_session(sid)
    require_owner(d, request)
    return {"sid": sid, "shares": _share.list_shares(sid)}


@router.delete("/api/sessions/{sid}/share/{token}")
async def api_share_revoke(sid: str, token: str, request: Request):
    require_auth(request)
    d = _wui().load_session(sid)
    require_owner(d, request)
    rec = _share.lookup_share(token)
    if rec and rec.get("sid") != sid:
        raise HTTPException(status_code=403, detail="token does not belong to sid")
    ok = _share.revoke_share(token)
    audit_log(request, "share.revoke", sid, token=token[:8] + "…", ok=ok)
    return {"ok": ok}


@router.get("/api/share/{token}")
async def api_share_view(token: str, request: Request):
    """公开只读访问 (无需 auth). 返回脱敏后的 session snapshot."""
    rec = _share.lookup_share(token)
    if not rec:
        raise HTTPException(status_code=404, detail="share token invalid or expired")
    wui = _wui()
    try:
        session = wui.load_session(rec["sid"])
    except Exception:
        raise HTTPException(status_code=404, detail="session gone")
    if not session or not session.get("messages"):
        raise HTTPException(status_code=404, detail="session empty")
    # 脱敏后返
    safe = scrub_session(session)
    # 剥敏感元字段
    safe.pop("owner", None)
    audit_log(request, "share.view", rec["sid"], token=token[:8] + "…")
    return {
        "ok": True,
        "read_only": rec.get("read_only", True),
        "expires": rec.get("expires"),
        "session": {
            "id": safe.get("id"),
            "name": safe.get("name"),
            "messages": safe.get("messages", []),
            "created": safe.get("created"),
        },
    }


# ── [P0-#17] trash / 回收站 ───────────────────────────────────────
@router.get("/api/trash")
async def api_trash_list(request: Request, sid: str = ""):
    require_auth(request)
    items = _trash.list_trash(sid or None)
    if sid:
        try:
            d = _wui().load_session(sid)
            require_owner(d, request)
        except HTTPException:
            raise
        except Exception:
            pass
    return {"count": len(items), "trash": items}


@router.post("/api/trash/{trash_id}/restore")
async def api_trash_restore(trash_id: str, request: Request):
    """将 dropped_msgs 追加回 session (在当前末尾)."""
    require_auth(request)
    rec = _trash.load_trash(trash_id)
    if not rec:
        raise HTTPException(status_code=404, detail="trash entry not found or expired")
    sid = rec.get("sid", "")
    wui = _wui()
    d = wui.load_session(sid)
    require_owner(d, request)
    dropped = rec.get("dropped_msgs") or []
    if not dropped:
        return {"ok": True, "restored": 0, "note": "empty trash"}
    d.setdefault("messages", [])
    d["messages"].extend(dropped)
    d["last_used"] = time.time()
    wui.save_session(d)
    audit_log(request, "trash.restore", sid, trash_id=trash_id, restored=len(dropped))
    return {"ok": True, "restored": len(dropped), "sid": sid}


@router.delete("/api/trash/{trash_id}")
async def api_trash_delete(trash_id: str, request: Request):
    require_auth(request)
    rec = _trash.load_trash(trash_id)
    sid = rec.get("sid", "") if rec else ""
    if sid:
        try:
            d = _wui().load_session(sid)
            require_owner(d, request)
        except HTTPException:
            raise
        except Exception:
            pass
    ok = _trash.delete_trash(trash_id)
    audit_log(request, "trash.delete", sid, trash_id=trash_id, ok=ok)
    return {"ok": ok}


@router.post("/api/trash/purge_expired")
async def api_trash_purge(request: Request):
    require_auth(request)
    n = _trash.purge_expired()
    audit_log(request, "trash.purge_expired", "", removed=n)
    return {"ok": True, "removed": n}
