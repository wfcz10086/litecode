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

## 可选: 挂 Web 路由 (ROUTER, 2026-10-17 起; ABI v1 向后兼容扩展)

`plugin.py` 可以**额外**导出一个 FastAPI 路由, web_ui 启动时与仓内 `routers/*_router.py` 一起 include。
不导出这些字段的老插件 (如 `/opt/pptx`、`/opt/cad`) 行为不变; 只导出 `ROUTER`、不导出 `NAME`/`TOOLS` 的
纯路由插件也可以 (工具侧当辅助模块跳过, 路由照常挂)。

| 字段 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `ROUTER` | `fastapi.APIRouter` | 无 | 要挂的路由; 不是 `APIRouter` 实例 → 记日志跳过 |
| `ROUTER_ORDER` | `int` | `500` | 注册顺序, 与仓内 router 的 `ORDER` 同一排序 `(ORDER, 名称)`, 路径重叠时先注册者生效。**>998 会被钳到 998**, 保证 `static_router` (999) 兜底永远最后; 非 int → 用 500 |
| `ROUTER_PUBLIC` | `bool` | `False` | 默认插件路由**统一要求登录**; 只有显式 `True` 才放开 (如 webhook 回调) |

```python
# /opt/demo/plugin.py
from fastapi import APIRouter
ROUTER = APIRouter(prefix="/api/demo")     # 建议统一 /api/<插件名> 前缀, 避免与仓内冲突
ROUTER_ORDER = 600

@ROUTER.get("/ping")
def ping():
    return {"pong": True}
```

鉴权:
- `ROUTER_PUBLIC` 不为 `True` 时, 机架不改动插件自己的 router 对象, 而是包一层
  `APIRouter(dependencies=[Depends(routers.plugin_auth)])` 再 include。`plugin_auth` 复用
  `plugins/auth/core.py` 的 cookie 校验 (与仓内 router 手动调用的 `require_auth(request)` 同源):
  HTTP 未登录 → 401, WebSocket 未登录 → 以 1008 关闭。`config.web_ui.auth.enabled=false` 时全部放行 (与仓内一致)。
- 该依赖只作用于 `@ROUTER.get/post/.../websocket` 声明的 FastAPI 路由; 用 `ROUTER.mount()` 挂的
  StaticFiles / 子应用等 Starlette 原生路由**不受保护**, 插件须自行处理。
- `ROUTER_PUBLIC = True` 的插件若有需要登录的端点, 必须在端点内自行调用
  `from plugins.auth.core import require_auth; require_auth(request)` (仓内 router 的做法)。

加载与隔离:
- 模块对象**复用** `plugins.registry` 扫描时导入的那一份 (`registry.external_modules()`), 同一个 `plugin.py`
  不会被导入两次, 工具 `run()` 与路由共享模块级状态。
- 插件导入失败、`ROUTER` 类型错误、挂载异常 → 只记日志跳过, 不影响 web_ui 启动 (仓内 router 缺 `router` 仍直接报错)。
- 未设置 `LITECODE_PLUGIN_PATH` 时不收集; 路由在 web_ui **启动时**挂上, `POST /api/plugins/reload` 只重扫工具,
  路由改动需重启 web_ui 生效。
- 前端面板 / 静态资源见下一节 `WEB_ASSETS` / `WEB_ENTRY`。

## 可选: 前端资源与面板 (WEB_ASSETS / WEB_ENTRY, 2026-10-17 起; ABI v1 向后兼容扩展)

插件可以自带 JS/CSS/图片等前端资源, 并把其中的 .js/.css 注入主页 (`/`)。不导出这两个字段的老插件行为不变,
主页字节完全不变。

| 字段 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `WEB_ASSETS` | `str` / `Path` | 无 | 静态资源目录; 相对路径以 `plugin.py` 所在目录为基准, 也可绝对路径。必须是已存在目录 |
| `WEB_ENTRY` | `list[str]` / `tuple` | 无 | 要注入主页的文件名, 只允许 `WEB_ASSETS` **第一层**的 `.js` / `.css` (不得含 `/` `\` `..`), 按列出顺序注入 |

```python
# /opt/demo/plugin.py
WEB_ASSETS = "web"                       # → /opt/demo/web/
WEB_ENTRY = ["demo.css", "demo.js"]
```

托管: `GET /plugin-assets/{插件名}/{path}` 只读提供 `WEB_ASSETS` 目录 (含子目录)。
- 插件名: `<dir>/plugin.py` → `<dir>`; `<root>/plugins/<x>.py` → `<root>.<x>`。只允许 `[A-Za-z0-9_.-]` (首字符字母数字, ≤64),
  否则该插件前端资源跳过; 重名时先扫到的生效。
- 与主页 `/assets/` 策略一致, **不要求登录** (登录页本身也要加载静态资源) — 别把敏感数据放进 `WEB_ASSETS`, 数据走带鉴权的 `ROUTER`。
- 后缀白名单: js mjs css map json txt png jpg jpeg gif webp svg ico woff woff2 ttf, 其他 → 400。
- 路径任一段为空/`.`/`..`/以 `.` 开头 (隐藏文件) 或含反斜杠 → 400; resolve 后不在目录内 (符号链接逃逸) → 404;
  未知插件/文件不存在 → 404。响应带 `X-Content-Type-Options: nosniff`。

注入: 返回主页时在最后一个 `</body>` 前插入 (按插件名排序, 同插件按 `WEB_ENTRY` 顺序):
```html
<!-- plugin web assets -->
<link rel="stylesheet" href="/plugin-assets/demo/demo.css?v=<mtime>" data-plugin="demo">
<script src="/plugin-assets/demo/demo.js?v=<mtime>" defer data-plugin="demo"></script>
```
注入内容只由 (已校验的) 插件名和文件名拼成, 并经 `html.escape`; 主页当前无 CSP 头, 同源脚本可直接加载。

前端挂载约定 (`web_assets/plugin_panels.js`, 主页始终加载):
```js
window.LiteCodePlugins.registerPanel({
  id: 'demo',                 // [A-Za-z0-9][A-Za-z0-9_-]{0,47}, 全局唯一
  title: 'Demo',              // 按钮与面板标题 (纯文本, ≤40 字)
  icon: '🧩',                 // 可选, 纯文本 (emoji)
  render(container) {         // 首次打开时调用一次, container = 面板 .pbd_ 区域
    container.textContent = 'hello';
  },
  onOpen(container) {},       // 可选, 每次打开都调用 (刷新数据)
});                           // 返回 true/false (id 非法/重复/缺 render → false + console.warn)
```
- 按钮加在侧栏 "接入" 分组 (无分组时平铺进 `#sbf`); 面板复用 `.pnl/.pb_/.ph_/.px_` 样式与 `openP/closeP`,
  DOM id 为 `pnl-plugin-<id>`, 点 × 或遮罩关闭。也可 `LiteCodePlugins.open(id)` / `.close(id)` 编程控制。
- 插件脚本是 `defer`, 执行时 DOM 已解析; 在 `DOMContentLoaded` 前注册的面板会排队, 等侧栏分组建好后再挂。
- 面板内要调后端, 用插件自己的 `ROUTER` (默认需登录, 浏览器同源请求自动带 cookie)。

出错隔离: `WEB_ASSETS` 类型不对/目录不存在、`WEB_ENTRY` 不是列表 → 该插件跳过 (只记日志);
`WEB_ENTRY` 中单个非法/不存在的文件名只跳过该项; 只有 `WEB_ENTRY` 没有 `WEB_ASSETS` → 忽略。
资源每次请求按 `registry.external_modules()` 计算, `POST /api/plugins/reload` 重扫后即跟随; 未设置 `LITECODE_PLUGIN_PATH` 时完全不收集。

## 卸载

`unset LITECODE_PLUGIN_PATH` → 下次 `registry.scan()` 该插件的工具消失, 下次 web_ui 启动其路由消失, 主页不再注入其前端资源、`/plugin-assets/<名>/` 返回 404，LiteCode 零改动继续跑。

## 契约变更

`_contract.py` 里的 `REQUIRED_FIELDS` / `CAPABILITY_KEYS` 是权威。改动请同步本文档并 bump `_contract.py` 顶部注释里的 ABI 版本号。
