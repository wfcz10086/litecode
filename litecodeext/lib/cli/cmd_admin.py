"""projects / config 子命令组 + memory/stats/health 命令 (从 litecli.py 拆出)."""
import json

import click

from .common import _c, _err, _ok, _r_json, _emit
from .common import _json_out, _none_if_empty


# ── projects ───────────────────────────────────────────────
@click.group()
def projects():
    """项目管理."""


@projects.command("list")
def projects_list():
    d = _r_json("GET", "/api/projects")
    if _json_out(d):
        return
    for p in _none_if_empty(d.get("projects", [])):
        # 接口字段是 project_id / project_type (ProjectMeta), 兼容旧的 id / type
        pid = p.get('project_id') or p.get('id', '?')
        ptype = p.get('project_type') or p.get('type', '?')
        click.echo(f"  {_c(pid, 'cyan')}  {p.get('name', '')}  ({ptype})  status={p.get('status', '?')}")


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
    _ok(f"已创建 {d.get('project_id') or d.get('id')} ({d.get('name')})")


@projects.command("rm")
@click.argument("pid")
def projects_rm(pid: str):
    _r_json("DELETE", f"/api/projects/{pid}")
    _ok(f"已删除 {pid}")


# ── 新增命令共用的小工具 ────────────────────────────────────
def _fail_if_error(d: dict, what: str):
    """经 web_ui 代理的接口出错时仍回 HTTP 200 + {"error": ..} 或 {"ok": false}, 这里统一转成非 0 退出。"""
    if not isinstance(d, dict):
        return
    if d.get("error") or d.get("ok") is False:
        _err(f"{what}失败: {d.get('error') or d.get('reason') or d}")


def _read_text(file, content):
    """--file 路径 (- 表示 stdin) 或 --content 文本, 二选一。"""
    if file is not None and content is not None:
        _err("--file 和 --content 只能二选一")
    if file is not None:
        return file.read()
    if content is not None:
        return content
    _err("需要 --file 或 --content")


def _confirm(yes: bool, prompt: str):
    """危险写操作: -y 跳过, 否则交互确认 (非交互且无输入时视为取消)。"""
    if not yes:
        click.confirm(prompt, abort=True)


# ── projects context (项目上下文 .md) ──────────────────────
@projects.group("context")
def projects_context():
    """项目上下文文件 (.md, 自动注入 agent 提示)."""


@projects_context.command("list")
@click.argument("pid")
def projects_context_list(pid: str):
    """列出项目的上下文文件."""
    d = _r_json("GET", f"/api/projects/{pid}/context")
    if _json_out(d):
        return
    for f in _none_if_empty(d.get("files", [])):
        preview = " ".join(str(f.get("preview", "")).split())[:60]
        click.echo(f"  {_c(f.get('name', '?'), 'cyan')}  {f.get('size', 0)} 字  {_c(preview, 'gray')}")


@projects_context.command("get")
@click.argument("pid")
@click.argument("name")
def projects_context_get(pid: str, name: str):
    """显示某个上下文文件的全文."""
    d = _r_json("GET", f"/api/projects/{pid}/context/{name}")
    if _json_out(d):
        return
    click.echo(d.get("content", ""))


@projects_context.command("add")
@click.argument("pid")
@click.argument("name")
@click.option("--file", "file", type=click.File("r", encoding="utf-8"), default=None,
              help="从本地文件读入内容 (- 表示标准输入)")
@click.option("--content", default=None, help="直接给出内容文本")
@click.option("-y", "--yes", is_flag=True, help="同名文件已存在时不询问直接覆盖")
def projects_context_add(pid: str, name: str, file, content, yes: bool):
    """新增/覆盖上下文文件 NAME (须以 .md 结尾)."""
    text = _read_text(file, content)
    existing = _r_json("GET", f"/api/projects/{pid}/context").get("files", [])
    if any(f.get("name") == name for f in existing):
        _confirm(yes, f"{name} 已存在, 覆盖?")
    d = _r_json("PUT", f"/api/projects/{pid}/context/{name}", json_body={"content": text})
    _ok(f"已写入 {d.get('name', name)} ({d.get('size', len(text))} 字)")


@projects_context.command("rm")
@click.argument("pid")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="不询问直接删除")
def projects_context_rm(pid: str, name: str, yes: bool):
    """删除上下文文件."""
    _confirm(yes, f"删除项目 {pid} 的上下文 {name}?")
    _r_json("DELETE", f"/api/projects/{pid}/context/{name}")
    _ok(f"已删除 {name}")


# ── projects decisions (跨会话决策记录) ─────────────────────
@projects.group("decisions")
def projects_decisions():
    """项目决策记录 (PROJECT_DECISIONS.md)."""


@projects_decisions.command("list")
@click.argument("pid")
def projects_decisions_list(pid: str):
    """显示项目的全部决策记录."""
    d = _r_json("GET", f"/api/projects/{pid}/decisions")
    if _json_out(d):
        return
    content = d.get("content", "")
    if content.strip():
        click.echo(content.rstrip("\n"))
    else:
        _none_if_empty([])


@projects_decisions.command("add")
@click.argument("pid")
@click.argument("text")
@click.option("--author", default="cli", show_default=True, help="记录人")
def projects_decisions_add(pid: str, text: str, author: str):
    """追加一条决策."""
    d = _r_json("POST", f"/api/projects/{pid}/decisions", json_body={"decision": text, "author": author})
    _ok(f"已追加决策 (文件 {d.get('size', '?')} 字节)")


@projects_decisions.command("clear")
@click.argument("pid")
@click.option("-y", "--yes", is_flag=True, help="不询问直接清空")
def projects_decisions_clear(pid: str, yes: bool):
    """清空项目的全部决策记录."""
    _confirm(yes, f"清空项目 {pid} 的全部决策记录?")
    _r_json("DELETE", f"/api/projects/{pid}/decisions")
    _ok(f"已清空 {pid} 的决策记录")


# ── projects instructions (项目指令) ───────────────────────
@projects.group("instructions")
def projects_instructions():
    """项目指令 (对项目内所有会话生效)."""


@projects_instructions.command("get")
@click.argument("pid")
def projects_instructions_get(pid: str):
    """显示项目指令."""
    d = _r_json("GET", f"/api/projects/{pid}/instructions")
    if _json_out(d):
        return
    content = d.get("content", "")
    if content.strip():
        click.echo(content.rstrip("\n"))
    else:
        _none_if_empty([])


@projects_instructions.command("set")
@click.argument("pid")
@click.option("--file", "file", type=click.File("r", encoding="utf-8"), default=None,
              help="从本地文件读入 (- 表示标准输入)")
@click.option("--content", default=None, help="直接给出指令文本 (空串表示清空)")
@click.option("-y", "--yes", is_flag=True, help="已有指令时不询问直接覆盖")
def projects_instructions_set(pid: str, file, content, yes: bool):
    """设置 (覆盖) 项目指令."""
    text = _read_text(file, content)
    old = _r_json("GET", f"/api/projects/{pid}/instructions").get("content", "")
    if old.strip() and old != text:
        _confirm(yes, f"项目 {pid} 已有指令 ({len(old)} 字), 覆盖?")
    d = _r_json("PUT", f"/api/projects/{pid}/instructions", json_body={"content": text})
    _ok(f"已保存项目指令 ({d.get('size', len(text))} 字)")


# ── projects sessions (会话 ↔ 项目归属) ─────────────────────
@projects.group("sessions")
def projects_sessions():
    """会话与项目的归属关系."""


@projects_sessions.command("list")
@click.option("--project", "pid", default=None, help="只看某个项目下的会话")
def projects_sessions_list(pid):
    """列出 会话 → 项目 的归属."""
    d = _r_json("GET", "/api/project-sessions")
    if pid:
        d = {"map": {s: p for s, p in d.get("map", {}).items() if p == pid}}
    if _json_out(d):
        return
    for sid, p in _none_if_empty(sorted(d.get("map", {}).items(), key=lambda kv: (kv[1], kv[0]))):
        click.echo(f"  {_c(sid, 'cyan')}  →  {p}")


@projects_sessions.command("add")
@click.argument("pid")
@click.argument("sid")
def projects_sessions_add(pid: str, sid: str):
    """把会话 SID 挪进项目 PID (会先从原项目摘掉)."""
    d = _r_json("POST", f"/api/projects/{pid}/sessions", json_body={"session_id": sid})
    _ok(f"已把 {sid} 归入 {pid} (项目现有 {d.get('session_count', '?')} 个会话)")


@projects_sessions.command("rm")
@click.argument("sid")
@click.option("-y", "--yes", is_flag=True, help="不询问直接移出")
def projects_sessions_rm(sid: str, yes: bool):
    """把会话移出所属项目 (不删除会话本身)."""
    _confirm(yes, f"把会话 {sid} 移出所属项目?")
    d = _r_json("DELETE", f"/api/project-sessions/{sid}")
    if d.get("ok"):
        _ok(f"已把 {sid} 移出项目")
    else:
        _err(f"{sid} 不属于任何项目")


# ── config ─────────────────────────────────────────────────
@click.group()
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
@click.command()
@click.argument("sid")
def memory(sid: str):
    """显示 session 的记忆 (L1/L2/context_files)."""
    d = _r_json("GET", f"/api/memory/{sid}")
    if _json_out(d):
        return
    click.echo(f"  ctx={d.get('context_window')}  used={d.get('token_estimate')} ({d.get('pct')}%)")
    if d.get("l1"):
        click.echo(_c(f"  L1: {d['l1']['lines']} 行 / {d['l1']['tokens']} tok", "cyan"))
    for l2 in d.get("l2", []):
        click.echo(f"  L2 {l2['topic']}: {l2['tokens']} tok")
    for cf in d.get("context_files", []):
        click.echo(f"  ctx {cf['name']}: {cf['tokens']} tok")


# 记忆写操作用独立命令名 (memory-xxx), 不把 memory 改成 group: 那会让 `memory <sid>`
# 的 sid 跟子命令名抢位置, 也会改变 `memory --help` 的输出。
@click.command("memory-compress")
@click.argument("sid")
def memory_compress(sid: str):
    """立即压缩会话 SID 的记忆 (把历史提炼进 L1/L2)."""
    d = _r_json("POST", f"/api/memory/{sid}/compress", timeout=120)
    _fail_if_error(d, "压缩记忆")
    _ok(f"已压缩 {sid} 的记忆")


@click.command("memory-clear")
@click.argument("sid")
@click.option("-y", "--yes", is_flag=True, help="不询问直接清空")
def memory_clear(sid: str, yes: bool):
    """清空会话 SID 的 L1 记忆 (MEMORY.md 重置为空模板)."""
    _confirm(yes, f"清空会话 {sid} 的 L1 记忆? 此操作不可撤销")
    d = _r_json("DELETE", f"/api/memory/{sid}")
    _fail_if_error(d, "清空记忆")
    _ok(f"已清空 {sid} 的 L1 记忆")


@click.command("memory-edit")
@click.argument("sid")
@click.option("--file", "file", type=click.File("r", encoding="utf-8"), default=None,
              help="用本地文件内容整体替换 L1 (- 表示标准输入)")
@click.option("-e", "--editor", "use_editor", is_flag=True, help="用 $EDITOR 打开当前 L1 编辑")
@click.option("-y", "--yes", is_flag=True, help="--file 覆盖时不询问")
def memory_edit(sid: str, file, use_editor: bool, yes: bool):
    """编辑会话 SID 的 L1 记忆 (MEMORY.md)."""
    if bool(file) == use_editor:
        _err("需要 --file 或 -e 二选一")
    if use_editor:
        cur = ((_r_json("GET", f"/api/memory/{sid}").get("l1") or {}).get("content")) or ""
        text = click.edit(cur, extension=".md")
        if text is None or text == cur:
            click.echo("未修改, 已放弃")
            return
    else:
        text = file.read()
        _confirm(yes, f"用 {getattr(file, 'name', '文件')} 覆盖会话 {sid} 的 L1 记忆?")
    d = _r_json("PATCH", f"/api/memory/{sid}/l1", json_body={"content": text})
    _fail_if_error(d, "保存记忆")
    _ok(f"已保存 {sid} 的 L1 记忆 ({len(text.splitlines())} 行)")


@click.command()
def stats():
    """显示用量统计 + 估算成本."""
    d = _r_json("GET", "/api/stats")
    _emit(d)


@click.command()
def health():
    """健康检查."""
    d = _r_json("GET", "/api/health")
    _emit(d)


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [projects, config, memory, memory_compress, memory_clear, memory_edit, stats, health]
