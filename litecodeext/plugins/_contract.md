# LiteCode 插件契约 (Chassis ABI v1)

LiteCode 是一个**机架 (chassis)**。核心工具在 `plugins/tools/` 随机架出厂；外部独立项目 (`/opt/cad`, `/opt/pptx`, `/opt/beian` 等) 留在原地，通过一个符合本契约的 `plugin.py` 挂进来。

## 挂载方式

**内部工具**（自动扫描）：`litecodeext/plugins/tools/*.py`

**外部插件**（通过环境变量）：
```bash
export LITECODE_PLUGIN_PATH="/opt/cad:/opt/pptx:/opt/beian"
```

registry 扫描每个目录里的：
- 单文件入口：`<root>/plugin.py`
- 子目录多插件：`<root>/plugins/*.py`

## 一个 plugin.py 长这样

```python
# /opt/cad/plugin.py
NAME = "cad_generate"
DESCRIPTION = "根据自然语言描述生成 DXF/STL/PDF"
SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "CAD 需求描述"},
        "output": {"type": "string", "enum": ["dxf", "stl", "pdf"], "default": "dxf"},
    },
    "required": ["prompt"],
}
CAPABILITIES = {
    "produces_artifact": True,
    "long_running": True,
    "cost": "heavy",
    "safe": True,
}
VERSION = "0.3.0"

async def run(args: dict, ctx: dict) -> dict:
    ctx["emit_progress"](0.1, "解析需求")
    # ... 调 /opt/cad/engine_2d.py 等 ...
    ctx["emit_progress"](0.9, "导出")
    aid = ctx["emit_artifact"]("dxf", dxf_bytes, meta={"name": "part.dxf"})
    return {"ok": True, "result": "生成成功", "artifacts": [aid]}
```

## 必填字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `NAME` | `str` | 工具名（会作为 `tool_call.name`，全局唯一） |
| `DESCRIPTION` | `str` | 给 LLM 看的说明 |
| `SCHEMA` | `dict` | JSON-Schema，兼容 OpenAI function-calling |
| `run(args, ctx)` | `async fn` | 执行体 |

## 可选字段

| 字段 | 类型 | 缺省 |
|---|---|---|
| `VERSION` | `str` | `"0.0.1"` |
| `AUTHOR` | `str` | `""` |
| `CAPABILITIES` | `dict` | `{}` |
| `HEALTH_CHECK` | `async fn() -> bool` | `None` |

### CAPABILITIES 键

- `produces_artifact` (bool) — 会调 `ctx["emit_artifact"]`
- `long_running` (bool) — 需要 BG 管理页跟踪
- `streaming` (bool) — `run` 内部会分段回吐
- `cost` (`"cheap"` / `"medium"` / `"heavy"`) — 调度器排队用
- `auth_required` (bool) — 需要用户级鉴权
- `safe` (bool) — `False` 表示破坏性操作，前端会二次确认

## ctx (PluginCtx) 提供

- `ctx["sid"]` — 当前会话 id
- `ctx["workspace"]` — 工作区路径
- `ctx["logger"]` — `logging.Logger`
- `ctx["emit_artifact"](kind, payload, meta=None) -> str` — 落产出物，返回 `artifact_id`
- `ctx["emit_progress"](pct: float, msg: str)` — 进度回吐（转发到 SSE）
- `ctx["config"]` — 该插件的本地配置

## 返回体约定

```python
{"ok": True,  "result": "...", "artifacts": ["a123", ...]}
{"ok": False, "error":  "..."}
```

## 卸载

`unset LITECODE_PLUGIN_PATH` → 下次 `registry.scan()` 该插件消失，LiteCode 零改动继续跑。

## 契约变更

`_contract.py` 里的 `REQUIRED_FIELDS` / `CAPABILITY_KEYS` 是权威。改动请同步本文档并 bump `_contract.py` 顶部注释里的 ABI 版本号。
