"""handlers/providers.py — 能力接入 6 个工具: list_providers / get_provider / generate_image / generate_video /
generate_music / send_notify (实现见 lib/providers.py、lib/provider_gen.py、lib/provider_probe.py)。"""
from __future__ import annotations

import time
from pathlib import Path

from lib import provider_gen, provider_probe
from lib import providers as P

from . import register


def _artifact_dir(sid: str | None) -> Path:
    from lib.config import SESSIONS_DISK
    return Path(SESSIONS_DISK) / (sid or "_orphan") / "artifacts"


def _note_artifacts(sid: str, files: list, op: str) -> None:
    try:
        import tool_dispatch   # 扁平名: 和 litecode_server 注入 _add_artifact_fn 的是同一个模块实例
        for f in files:
            p = Path(f)
            tool_dispatch._add_artifact(sid, {"filepath": str(p), "op": op, "kind": p.suffix.lstrip("."),
                                              "size": p.stat().st_size, "ts": time.time()})
    except Exception:
        pass


def _tried_text(tried) -> str:
    return "\n".join(f"  ✗ {n}: {e}" for n, e in tried or ())


def _fail(op: str, r: dict, kind_label: str) -> str:
    if r.get("manual"):
        head = f"[{op}] 默认路由到「{r['provider']}」" + (f" (之前失败:\n{_tried_text(r['tried'])})" if r.get("tried") else "")
        return head + "\n" + r["manual"]
    if not r.get("tried"):
        return (f"ERROR: {op} 还没有可用的{kind_label}接入。请用户在网页左上角「🔌 能力接入」里添加 (或在对话里给出服务地址/key, "
                f"你按其文档直接 curl)。")
    return f"ERROR: {op} 所有{kind_label}接入都失败了:\n{_tried_text(r['tried'])}"


@register("list_providers")
async def h_list_providers(sid: str, args: dict):
    kind = str(args.get("kind") or "").strip().lower()
    items = P.load_all() + [P.normalize(b) for b in P.BUILTINS]
    if kind:
        items = [p for p in items if p["kind"] == kind]
    if not items:
        return ("还没有配置任何接入" + (f" (类型 {kind})" if kind else "")
                + "。用户可在网页左上角「🔌 能力接入」添加; 大模型在「模型管理」里。"), None
    lines = [f"[能力接入] 共 {len(items)} 个 (get_provider(name) 看完整用法):"]
    for k in P.KINDS:
        group = [p for p in items if p["kind"] == k]
        if group:
            lines.append(f"{P.KINDS[k]['icon']} {P.KINDS[k]['label']}:")
            lines += ["  " + P.brief(p) for p in group]
    return "\n".join(lines), None


@register("get_provider")
async def h_get_provider(sid: str, args: dict):
    p = P.find(str(args.get("name") or args.get("id") or ""))
    if p is None:
        names = ", ".join(x["name"] for x in P.load_all()) or "(无)"
        return f"ERROR: 没有名为 {args.get('name')!r} 的接入。现有: {names}", None
    return P.card_text(p), None


def _int(v, default):
    try:
        return max(64, min(int(v), 8192))
    except (TypeError, ValueError):
        return default


@register("generate_image")
async def h_generate_image(sid: str, args: dict):
    try:
        r = await provider_gen.generate_image(str(args.get("prompt") or ""), _int(args.get("width"), 1024),
                                              _int(args.get("height"), 1024), args.get("provider") or None,
                                              out_dir=_artifact_dir(sid))
    except (ValueError, LookupError) as e:
        return f"ERROR: generate_image {e}", None
    if not r.get("ok"):
        return _fail("generate_image", r, "生图"), None
    _note_artifacts(sid, r["files"], "generate_image")
    lines = [f"[generate_image OK] {f}" for f in r["files"]]
    lines.append(f"  接入: {r['provider']}" + (f" (前面失败的:\n{_tried_text(r['tried'])})" if r["tried"] else ""))
    lines.append("回复里写出完整路径 (网页据此显示预览); 微信/企微会话再加一行 [发送文件: 路径]。")
    return "\n".join(lines), None


@register("generate_video")
async def h_generate_video(sid: str, args: dict):
    try:
        r = await provider_gen.generate_video(
            str(args.get("prompt") or ""), args.get("width"), args.get("height"), args.get("frames"),
            args.get("image") or None, args.get("provider") or None, out_dir=_artifact_dir(sid), sid=sid)
    except (ValueError, LookupError) as e:
        return f"ERROR: generate_video {e}", None
    if not r.get("ok"):
        return _fail("generate_video", r, "生视频"), None
    return (f"[generate_video 已提交] 接入: {r['provider']} · 后台 pid={r['pid']}\n"
            f"日志: {r['log']}\n"
            f"视频要几分钟: 隔一会儿 tail_log(path='{r['log']}') 看进度, 出现 `[out] <路径>` 行即完成 (那是 mp4 本地路径, "
            f"写进回复); `[err]` 行是失败原因。先告诉用户已提交, 别空等。"), None


@register("generate_music")
async def h_generate_music(sid: str, args: dict):
    try:
        dur = int(args.get("duration") or 30)
    except (TypeError, ValueError):
        dur = 30
    try:
        r = await provider_gen.generate_music(str(args.get("prompt") or ""), dur, args.get("mood") or None,
                                              args.get("provider") or None, out_dir=_artifact_dir(sid))
    except (ValueError, LookupError) as e:
        return f"ERROR: generate_music {e}", None
    if not r.get("ok"):
        return _fail("generate_music", r, "音乐"), None
    _note_artifacts(sid, r["files"], "generate_music")
    lines = [f"[generate_music OK] {f}" for f in r["files"]]
    lines.append(f"  接入: {r['provider']}" + (f" · {r['note']}" if r.get("note") else ""))
    if r["tried"]:
        lines.append(f"  前面失败的:\n{_tried_text(r['tried'])}")
    lines.append("回复里写出完整路径 (网页显示播放器)。")
    return "\n".join(lines), None


@register("send_notify")
async def h_send_notify(sid: str, args: dict):
    text = str(args.get("text") or "").strip()
    if not text:
        return "ERROR: send_notify 需要 text", None
    try:
        cands = P.candidates("notify", args.get("channel") or None)
    except LookupError as e:
        return f"ERROR: send_notify {e}", None
    if not cands:
        return "ERROR: send_notify 还没有通知渠道, 请用户在网页「🔌 能力接入」里添加群机器人或 SMTP。", None
    p = cands[0]   # 通知不自动换渠道 (避免同一条消息发到多个群)
    auto = p["protocol"] in ("wecom_webhook", "feishu_webhook", "dingtalk_webhook", "smtp") or \
        (p["protocol"] == "http" and P.first_curl(p.get("usage") or ""))
    if not auto:   # 识别不出怎么发: 把明文交给 agent, 按说明自己发
        return (f"[send_notify] 「{p['name']}」不是能自动发送的渠道 (没识别出钉钉/飞书/企微 webhook、邮箱或 curl)。"
                f"请按下面这段明文自己发送 (execute_shell 跑 curl 等), 消息正文: {text}\n" + P.card_text(p)), None
    r = await provider_probe.send_notify(p, text, str(args.get("title") or ""))
    if r.get("ok"):
        return f"[send_notify OK] 已通过「{p['name']}」发送 ({r['ms']} ms)", None
    hint = f"\n  原因: {r['hint']}" if r.get("hint") else ""
    return f"ERROR: send_notify 「{p['name']}」发送失败: {r.get('error')}{hint}", None
