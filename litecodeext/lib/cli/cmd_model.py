"""model 子命令组: 多模型列出/切换/测试/删除 (从 litecli.py 拆出)."""
import click

from .common import (
    _c,
    _err,
    _ok,
    _r_json,
    _emit,
    _resolve_model_id,
    _resolve_model_ref,
)
from .common import _json_out, _none_if_empty


# ── model ──────────────────────────────────────────────────
@click.group()
def model():
    """多模型管理."""


@model.command("list")
def model_list():
    """列出所有模型."""
    d = _r_json("GET", "/api/models")
    if _json_out(d):
        return
    active = d.get("active", "")
    active_backend = (d.get("active_backend") or "").rstrip("/")
    for i, m in enumerate(_none_if_empty(d.get("models", [])), 1):
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


@model.command("add")
@click.option("--id", "model_id", required=True, help="模型 ID (发给后端的 model 名)")
@click.option("--url", "backend_url", required=True, help="后端地址, 如 http://host:8000/v1")
@click.option("--type", "backend_type", type=click.Choice(["vllm", "ollama", "openai", "generic"]),
              default="openai", show_default=True, help="后端类型")
@click.option("--api-key", default="", envvar="LITECODE_MODEL_API_KEY", show_envvar=True,
              help="API key (也可用环境变量传, 不会回显)")
@click.option("--label", default=None, help="显示名 (默认: ID (类型))")
@click.option("--ctx", "context_window", type=int, default=200000, show_default=True, help="上下文窗口")
@click.option("--max-tokens", type=int, default=18000, show_default=True, help="单次最大输出 token")
@click.option("--vision", is_flag=True, help="支持图片输入")
@click.option("--thinking", is_flag=True, help="开启思考模式")
@click.option("-y", "--yes", is_flag=True, help="同 ID 已存在时不询问直接覆盖")
def model_add(model_id, backend_url, backend_type, api_key, label, context_window, max_tokens,
              vision, thinking, yes):
    """新增模型到配置 (同 ID 已存在则覆盖)."""
    existing = _r_json("GET", "/api/models").get("models", [])
    if any(m.get("id") == model_id for m in existing) and not yes:
        click.confirm(f"模型 {model_id} 已存在, 覆盖其配置?", abort=True)
    # 字段与 Web 设置页 (web_assets/config_editor.js mdlAdd) 保持一致
    body = {
        "id": model_id, "backend_type": backend_type, "backend_url": backend_url.strip(),
        "api_key": api_key.strip(), "api_format": "openai-chat",
        "context_window": context_window, "max_tokens": max_tokens,
        "supports_vision": vision, "enable_thinking": thinking, "thinking_budget": 8192,
        "label": label or f"{model_id} ({backend_type})",
    }
    d = _r_json("POST", "/api/models/add", json_body=body)
    if d.get("ok"):
        _ok(f"已添加 {model_id} (共 {d.get('count', '?')} 个模型)")
    else:
        _err(f"添加失败: {d.get('error', '?')}")


# 由 lib.cli.discover() 自动注册到 litecli 根命令
COMMANDS = [model]
