# SSE_CONTRACT.md · LiteCode SSE 字段冻结契约

> **冻结声明**：未经 WEB_CLAUDE 拍板并写决策日志，不得删改任何字段名/类型/嵌套结构。
> 历史禁忌见 git 历史里的 HANDOFF.md (已退役, 内容并入 BACKLOG.md 流程)。
>
> 本文件由 P1-a 普查 `litecodeext/lib/sse.py`、`litecodeext/litecode_server.py`、
> `litecodeext/core/multi_agent.py`、`litecodeext/web_ui.py` 真实 yield 后生成。
> 所有字段均已与消费侧 `cli.py`、`wechat_bridge.py`、`web_assets/app.js` 交叉核对。

---

## 1. 协议层

- **路由**：`POST /v1/chat/completions`（server 侧）；`POST /api/chat/{sid}`（web_ui 转发）
- **Content-Type**：`text/event-stream`
- **事件行格式**：`data: <JSON>\n\n`
- **流结束标记**：`data: [DONE]\n\n`（OpenAI 兼容，无 JSON，消费侧匹配字符串 `[DONE]`）
- **SSE 注释行**：以 `:` 开头，消费侧**直接忽略**，用于保活或内部诊断

所有 JSON 事件（`[DONE]` 除外）共享同一顶层结构：

```json
{
  "id":      "cw-a1b2c3d4",
  "object":  "chat.completion.chunk",
  "model":   "<model-id>",
  "choices": [
    {
      "index":        0,
      "delta":        { ... },
      "finish_reason": null
    }
  ]
}
```

所有业务字段都在 `choices[0].delta` 内。下面各节只描述 `delta` 部分。

---

## 2. 事件类型一览

> 术语对照：`delta.text` 即 `delta.content`（主文本增量）；`delta.reasoning` 为推理增量；
> `tool_call` / `tool_result` 语义由 `task_exec` 事件承载（executing / done）；
> `done` 标记 = `data: [DONE]` 行；`error` = 顶层错误 JSON。

| delta 键 | 事件名 | 产生位置 | 含义 |
|---|---|---|---|
| `content` | 文本增量 | `lib/sse.py:sse_content` | 主回复文本流 |
| `reasoning` | 推理增量 | `lib/sse.py:sse_reasoning` | 模型思考过程（Qwen3/R1） |
| `task_exec` | 工具执行状态 | `litecode_server.py:1501,1965` | 工具调用前/后状态通知 |
| `diff_view` | 文件 diff | `litecode_server.py:1955` | 写文件后展示 diff |
| `agent_status` | 子代理状态 | `core/multi_agent.py:108` | 多代理 pipeline 进度 |
| `orchestrator_step` | DAG 编排步骤 | `core/orchestrator.py:129` | DAG 节点状态推送（v1.5 P5-d 引入）；P44 扩展：phase/output_delta/elapsed_ms 可选字段支持实时输出流；P53 扩展：stream 字段标记 stdout/stderr |
| `usage` | 用量统计 | `lib/sse.py:sse_usage` | token/迭代/耗时汇总 |
| `finish_reason: "stop"` | 流终止 | `lib/sse.py:sse_stop` | delta 为空 `{}`, finish_reason="stop" |
| `data: [DONE]` | 流结束 | `lib/sse.py:sse_done` | 无 JSON，纯字符串标记 |
| `error` (顶层) | 错误 | `web_ui.py:874,899` | 非 choices 结构，见第 10 节 |
| `panel_refresh` | 面板刷新信号 | `lib/sse.py:sse_meta` / `litecode_server.py:2014` | DAG/Timer 工具成功后通知前端刷新对应面板（P43-c 引入） |
| `: <注释>` | 注释/心跳 | `litecode_server.py:1039,1916,1948` | internal，消费侧忽略 |

---

## 3. content — 文本增量

**产生位置**：`litecodeext/lib/sse.py:8-11`，`litecodeext/litecode_server.py` 多处

**delta 结构**：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `content` | string | 是 | 本次增量文本片段（可能是单个字符，也可能是多字） |

**附加字段**（多代理时由 `multi_agent.py:134` 注入）：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `agent_label` | string | 否 | 子代理名称标签，用于 CLI/Web 区分多代理输出来源 |

**JSON 示例**：

```json
{
  "id": "cw-1a2b3c4d",
  "object": "chat.completion.chunk",
  "model": "qwen3",
  "choices": [{"index": 0, "delta": {"content": "你好，"}, "finish_reason": null}]
}
```

多代理带标签示例：

```json
{
  "id": "ma-ab1234",
  "object": "chat.completion.chunk",
  "model": "litecode",
  "choices": [{"index": 0, "delta": {"content": "任务完成。", "agent_label": "Coder"}, "finish_reason": null}]
}
```

---

## 4. reasoning — 推理增量

**产生位置**：`litecodeext/lib/sse.py:13-17`，`litecodeext/litecode_server.py:938,1003,1008`

**delta 结构**：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `reasoning` | string | 是 | 模型推理/思考过程的增量文本 |

**约束**：
- `reasoning` 和 `content` 不同时出现在同一 chunk
- 先流完 `reasoning`，再开始 `content`（服务端保证顺序）
- 前端收到第一个 `content` chunk 时可视为推理结束，收起思考折叠块

**JSON 示例**：

```json
{
  "id": "cr-5e6f7a8b",
  "object": "chat.completion.chunk",
  "model": "qwen3",
  "choices": [{"index": 0, "delta": {"reasoning": "让我先分析一下问题..."}, "finish_reason": null}]
}
```

---

## 5. task_exec — 工具执行状态

**产生位置**：`litecodeext/litecode_server.py:1501-1505`（executing），`litecode_server.py:1965-1968`（done）

**delta 结构**：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `task_exec` | object | 是 | 工具状态容器 |
| `task_exec.status` | string | 是 | `"executing"` 或 `"done"` |
| `task_exec.detail` | string | 是 | 工具名+参数摘要，格式为 `"tool_name: 参数摘要"` |
| `task_exec.agent_label` | string | 否 | 子代理标签（由 `multi_agent.py:538` 注入到 task_exec 内） |

**`agent_label` 的两条 emit 路径**：

1. **顶层 delta**（`multi_agent.py:127 _sse_agent_output`）：与 `content` 同级，标记主回复来自哪个子代理 — 见 §3 附加字段
2. **task_exec 内**（`multi_agent.py:538`）：内嵌在工具执行通知中，标记本次工具调用归属哪个子代理

消费侧（`cli.py:302`、`app.js:235`）两个位置都读，**优先读 `delta.<key>.agent_label`，回退 `delta.agent_label`**。

**status 值**：

| 值 | 含义 | 时机 |
|---|---|---|
| `"executing"` | 工具开始执行 | 调用前立即发出 |
| `"done"` | 工具执行完毕 | 拿到结果后发出，`detail` 为结果前 200 字 |

**JSON 示例（executing）**：

```json
{
  "id": "cs-9c0d1e2f",
  "object": "chat.completion.chunk",
  "model": "qwen3",
  "choices": [{"index": 0, "delta": {
    "task_exec": {"status": "executing", "detail": "read_file: litecodeext/cli.py"}
  }, "finish_reason": null}]
}
```

**JSON 示例（done）**：

```json
{
  "id": "cs-3a4b5c6d",
  "object": "chat.completion.chunk",
  "model": "qwen3",
  "choices": [{"index": 0, "delta": {
    "task_exec": {"status": "done", "detail": "import sys\\nimport os..."}
  }, "finish_reason": null}]
}
```

**注意**：`data_collect` 和 `task_analysis` 是消费侧（`cli.py:241`、`web_ui.py:890`、`app.js:230`）同时监听的 delta 键，与 `task_exec` 结构完全相同。但当前服务端**只发 `task_exec`**，`data_collect`/`task_analysis` 为预留键位，保留消费侧兼容代码。

---

## 6. diff_view — 文件 diff

**产生位置**：`litecodeext/litecode_server.py:1955-1958`

**delta 结构**：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `diff_view` | object | 是 | diff 容器 |
| `diff_view.filepath` | string | 是 | 被修改的文件路径（相对于工作区根） |
| `diff_view.diff` | string | 是 | unified diff 格式文本 |

**触发条件**：工具（如 `write_file`）执行后 diff 不为空时发出，每次写文件一个事件。

**JSON 示例**：

```json
{
  "id": "cs-7e8f9a0b",
  "object": "chat.completion.chunk",
  "model": "qwen3",
  "choices": [{"index": 0, "delta": {
    "diff_view": {
      "filepath": "litecodeext/cli.py",
      "diff": "--- a/litecodeext/cli.py\n+++ b/litecodeext/cli.py\n@@ -1,3 +1,4 @@\n+import json\n import sys\n"
    }
  }, "finish_reason": null}]
}
```

---

## 7. agent_status — 子代理状态

**产生位置**：`litecodeext/core/multi_agent.py:108-124`

**delta 结构**：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `agent_status` | object | 是 | 子代理状态容器 |
| `agent_status.label` | string | 是 | 子代理名称（如 `"Coder"`, `"Critic"`, `"Tester"`） |
| `agent_status.status` | string | 是 | `"pending"` / `"running"` / `"retrying"` / `"done"` / `"error"` |
| `agent_status.detail` | string | 否 | 状态描述文本，可为空字符串 |

**JSON 示例**：

```json
{
  "id": "ma-1c2d3e",
  "object": "chat.completion.chunk",
  "model": "litecode",
  "choices": [{"index": 0, "delta": {
    "agent_status": {"label": "Coder", "status": "running", "detail": "实现登录功能..."}
  }, "finish_reason": null}]
}
```

---

## 7.5 orchestrator_step — DAG 编排步骤（v1.5 P5-d 引入）

**产生位置**：`litecodeext/core/orchestrator.py:129-134`，emit 点：`orchestrator.py:232,407,481,507,523,667,724`

**delta 结构**：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `orchestrator_step` | object | 是 | DAG 步骤状态容器 |
| `orchestrator_step.step_id` | string | 是 | DAG 节点 ID（如 `"node_001"` 或 spec.id） |
| `orchestrator_step.label` | string | 是 | 节点显示名称（如 `"Planner"` / `"Coder"`） |
| `orchestrator_step.status` | string | 是 | `"pending"` / `"running"` / `"done"` / `"error"` / `"skipped"` |
| `orchestrator_step.detail` | string | 否 | 节点描述文本（task 前 80 字），可为空 |
| `orchestrator_step.phase` | string | 否 | 节点生命周期阶段：`"start"` / `"chunk"` / `"end"`（P44 引入，缺省时向后兼容） |
| `orchestrator_step.output_delta` | string | 否 | phase=chunk 时携带子代理输出增量（content 或 reasoning 片段） |
| `orchestrator_step.elapsed_ms` | int | 否 | phase=end 时携带节点总耗时（毫秒） |
| `orchestrator_step.stream` | string | 否 | `"stdout"` / `"stderr"`，phase=chunk 时区分输出流（P53 引入，stderr 来自 task_exec.detail 以 ERROR: / [STDERR 开头时） |

**JSON 示例**：

```json
{
  "id": "orch-a1b2c3",
  "object": "chat.completion.chunk",
  "model": "litecode",
  "choices": [{"index": 0, "delta": {
    "orchestrator_step": {
      "step_id": "node_001",
      "label": "Planner",
      "status": "running",
      "detail": "拆解任务为 3 个子节点..."
    }
  }, "finish_reason": null}]
}
```

**消费状态**（截至 v1.8 P34-b）：
- `cli.py` — **未消费**（DAG 进度对 CLI 不可见，待后续决定是否补渲染）
- `web_assets/app.js` — **未消费**（同上）
- `wechat_bridge.py` — 未消费

**说明**：`orchestrator_step` 在 v1.5 P5-d 引入用于 DAG 编排器进度推送，但 CLI/Web 当前未实装消费逻辑。是否补三端渲染由后续 P 项决定（候选：P35-b 三端 E2E 矩阵决议）。

**DAG 编辑器 ↔ orchestrator_step ↔ spawn_agent 关系（v1.8 P34-d 补全）**：

```
┌─────────────────────┐       ┌─────────────────────┐
│ Web UI: DAG 编辑器  │  PUT  │ /api/dags/{name}    │
│ (dag_editor.js)     │──────▶│ → workspace/dags/   │
│ 拖拽 + 连线 + JSON  │       │   {name}.json       │
└─────────────────────┘       └─────────────────────┘
                                        │
                                        │ (运行时加载)
                                        ▼
                              ┌────────────────────────────┐
                              │ DAGOrchestrator            │
                              │ (core/orchestrator.py)     │
                              │ 解析 DAG → 调度子代理       │
                              └────────────────────────────┘
                                        │
                              ┌─────────┴─────────┐
                              │                   │
                              ▼                   ▼
                  ┌──────────────────┐   ┌──────────────────┐
                  │ spawn_agent 工具  │   │ 进度 SSE 推送    │
                  │ (子代理调用)      │   │ orchestrator_step│
                  │ → agent_status   │   │ {step_id,status, │
                  │   事件          │   │  label,detail}  │
                  └──────────────────┘   └──────────────────┘
```

- **DAG 编辑器**（Web）通过 `PUT /api/dags/{name}` 持久化 DAG 计划
- **DAGOrchestrator** 运行时加载 DAG 并发出两类 SSE：
  - `orchestrator_step` — DAG 节点状态（pending/running/done/error/skipped）
  - `agent_status` — 该节点对应子代理的执行状态（通过 spawn_agent 实例化）
- 三端入口：CLI（`/dag` slash 命令规划中）+ Web（🔀 DAG 按钮）+ Wechat（远期）

---

## 7.6 panel_refresh — 面板刷新信号（P43-c 引入）

**产生位置**：`litecodeext/lib/sse.py:sse_meta`，emit 点：`litecodeext/litecode_server.py:1998-2014`

**触发工具**（9 个写操作工具成功后 yield）：`create_dag` / `update_dag` / `delete_dag` / `patch_dag_node` / `run_dag` / `create_timer` / `update_timer` / `delete_timer` / `run_timer_now`

**非触发工具**（只读，不发此事件）：`list_timers` / `get_timer_history`

**delta 结构**：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `panel_refresh` | object | 是 | 面板刷新信号容器 |
| `panel_refresh.event` | string | 是 | `"dag_changed"` 或 `"timer_changed"` |
| `panel_refresh.op` | string | 是 | `"create"` / `"update"` / `"delete"` / `"run"` |
| `panel_refresh.name` | string | 是 | DAG 名称或 Timer id |

**JSON 示例**：

```json
{
  "id": "cm-a1b2c3d4",
  "object": "chat.completion.chunk",
  "model": "litecode",
  "choices": [{"index": 0, "delta": {
    "panel_refresh": {
      "event": "dag_changed",
      "op": "create",
      "name": "my_pipeline"
    }
  }, "finish_reason": null}]
}
```

**消费状态**：
- `web_assets/app.js` — **已消费**：收到后调 `loadDAGList()` / `loadTimers()` 刷新对应面板，并显示轻量 toast（"DAG xxx 已创建/已修改/已删除"）
- `cli.py` — **未消费**（丢弃，无副作用；CLI 无 DAG/Timer 面板）
- `wechat_bridge.py` — **未消费**（丢弃）
- 第三方 OpenAI 兼容客户端 — 忽略（OpenAI 协议允许 delta 含未识别字段）

---

## 8. usage — 用量统计

**产生位置**：`litecodeext/lib/sse.py:32-40`，在流末 `sse_stop` 之前发出

**delta 结构**：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `usage` | object | 是 | 用量容器 |
| `usage.prompt_tokens` | int | 是 | 本次请求累计输入 token 数 |
| `usage.completion_tokens` | int | 是 | 本次请求累计输出 token 数 |
| `usage.total_tokens` | int | 是 | prompt + completion 合计 |
| `usage.iterations` | int | 是 | agent 迭代轮次（tool call 次数） |
| `usage.elapsed_seconds` | float | 是 | 总耗时（秒，保留 1 位小数） |

**JSON 示例**：

```json
{
  "id": "ce-b1c2d3e4",
  "object": "chat.completion.chunk",
  "model": "qwen3",
  "choices": [{"index": 0, "delta": {
    "usage": {
      "prompt_tokens": 4200,
      "completion_tokens": 830,
      "total_tokens": 5030,
      "iterations": 3,
      "elapsed_seconds": 12.4
    }
  }, "finish_reason": null}]
}
```

---

## 9. stop — 流终止

**产生位置**：`litecodeext/lib/sse.py:24-27`，`litecodeext/litecode_server.py:2773`

`delta` 为空对象 `{}`，`finish_reason` 为 `"stop"`。

**JSON 示例**：

```json
{
  "id": "ce-5f6a7b8c",
  "object": "chat.completion.chunk",
  "model": "qwen3",
  "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
}
```

发出顺序：`usage` 事件 → `stop` 事件 → `data: [DONE]`

---

## 10. error — 错误

**产生位置**：`litecodeext/web_ui.py:874,899`

**格式**：不走 `choices` 结构，直接 `data: {"error": "..."}`

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `error` | string | 是 | 错误描述 |

**JSON 示例**：

```json
{"error": "HTTP 503"}
```

```json
{"error": "Connection refused"}
```

**触发条件**：web_ui 向 litecode_server 转发时，后端返回非 200 状态码，或网络异常。

---

## 11. SSE 注释行（internal）

以 `:` 开头，不含 `data:` 前缀，**消费侧应直接跳过**。

| 格式 | 产生位置 | 含义 |
|---|---|---|
| `: heartbeat llm_stream` | `litecode_server.py:1039` | LLM 流超 15 秒无数据时保持连接 |
| `: heartbeat spawn_agent` | `litecode_server.py:1916` | 子代理启动期间保活 |
| `: heartbeat {fn_name}` | `litecode_server.py:1948` | 工具执行期间保活 |
| `: [400-retry-N] sanitizing history...` | `litecode_server.py:1058` | internal，400 重试诊断 |
| `: [400-retry-N] rebuilding tool pairs...` | `litecode_server.py:1063` | internal，400 重试诊断 |
| `: [400-retry-N] nuclear clean...` | `litecode_server.py:1072` | internal，400 重试诊断 |

---

## 12. 发出顺序（正常对话）

```
[可选] : heartbeat ...          ← SSE 注释，保活
[多次] data: {"choices":[{"delta":{"reasoning":"..."}}]}   ← 思考流
[多次] data: {"choices":[{"delta":{"content":"..."}}]}     ← 文本流
[多次] data: {"choices":[{"delta":{"task_exec":{...}}}]}   ← 工具状态
[可选] data: {"choices":[{"delta":{"diff_view":{...}}}]}   ← 文件 diff
[可选] data: {"choices":[{"delta":{"agent_status":{...}}}]} ← 子代理状态
[1次]  data: {"choices":[{"delta":{"usage":{...}}}]}       ← 用量汇总
[1次]  data: {"choices":[{"delta":{},"finish_reason":"stop"}]} ← 终止
[1次]  data: [DONE]                                        ← 流结束
```

---

## 13. 三端消费表

| delta 字段 | CLI (`cli.py`) | Web (`app.js`) | wechat_bridge (`wechat_bridge.py`) |
|---|---|---|---|
| `content` | 实时 stdout 打印 | marked.parse 渲染 | 累积入 `result["text"]` |
| `reasoning` | 未显示（收到但丢弃） | 折叠块流式展示（v12.5+） | 累积入 `result["reasoning"]` |
| `task_exec` | 彩色打印工具名+参数 | 工具列表折叠渲染 | 累积入 `result["tools"]` |
| `data_collect` | 同 task_exec（兼容） | 同 task_exec（兼容） | 同 task_exec（兼容） |
| `task_analysis` | 同 task_exec（兼容） | 同 task_exec（兼容） | 同 task_exec（兼容） |
| `diff_view` | `render_diff()` 终端彩色 | 可折叠 diff 块 | 累积入 `result["diffs"]` |
| `agent_status` | 彩色状态行 + 图标 | 子代理进度行 | 未消费（丢弃） |
| `agent_label` | 工具行前缀标签 | 工具行前缀标签 | 未消费（丢弃） |
| `agent`（兼容字段，**deprecated**） | 兜底回退 `or ev.get("agent")` | 兜底回退 `\|\| .agent` | — |
| `orchestrator_step` | **未消费**（v1.8 后续决定） | **未消费**（v1.8 后续决定） | 未消费 |
| `panel_refresh` | 未消费（丢弃） | 自动刷新 DAG/Timer 面板 + toast | 未消费（丢弃） |
| `usage` | 耗时+token 统计行 | 底部显示（`_lu`） | 累积入 `result["usage"]` |
| `finish_reason: stop` | 流结束检测 | 流结束检测 | 流结束检测 |
| `data: [DONE]` | 跳出读循环 | 跳出读循环 | 跳出读循环 |
| `error`（顶层） | 红色错误打印 | 红色错误文本 | 日志警告 |
| SSE 注释行 | 忽略 | 忽略 | 忽略 |

---

## 14. 字段冻结清单

以下字段名/类型/嵌套**全部冻结**，违反 = REVIEWER REJECT：

| 冻结项 | 冻结内容 |
|---|---|
| `choices[0].delta.content` | string，文本增量 |
| `choices[0].delta.reasoning` | string，推理增量 |
| `choices[0].delta.task_exec.status` | string，`"executing"` 或 `"done"` |
| `choices[0].delta.task_exec.detail` | string，工具摘要 |
| `choices[0].delta.diff_view.filepath` | string，文件路径 |
| `choices[0].delta.diff_view.diff` | string，unified diff |
| `choices[0].delta.agent_status.label` | string，子代理名 |
| `choices[0].delta.agent_status.status` | string，状态值 |
| `choices[0].delta.agent_status.detail` | string，描述 |
| `choices[0].delta.orchestrator_step.step_id` | string，DAG 节点 ID |
| `choices[0].delta.orchestrator_step.label` | string，节点名 |
| `choices[0].delta.orchestrator_step.status` | string，pending/running/done/error/skipped |
| `choices[0].delta.orchestrator_step.detail` | string，节点描述 |
| `choices[0].delta.orchestrator_step.phase` | string，start/chunk/end（可选，P44 引入） |
| `choices[0].delta.orchestrator_step.output_delta` | string，子代理输出增量（可选，phase=chunk 时） |
| `choices[0].delta.orchestrator_step.elapsed_ms` | int，节点耗时毫秒（可选，phase=end 时） |
| `choices[0].delta.orchestrator_step.stream` | string，"stdout"/"stderr"（可选，P53 引入） |
| `choices[0].delta.panel_refresh.event` | string，`"dag_changed"` / `"timer_changed"` |
| `choices[0].delta.panel_refresh.op` | string，`"create"` / `"update"` / `"delete"` / `"run"` |
| `choices[0].delta.panel_refresh.name` | string，DAG 名称或 Timer id |
| `choices[0].delta.usage.prompt_tokens` | int |
| `choices[0].delta.usage.completion_tokens` | int |
| `choices[0].delta.usage.total_tokens` | int |
| `choices[0].delta.usage.iterations` | int |
| `choices[0].delta.usage.elapsed_seconds` | float |
| `choices[0].finish_reason` | `null` / `"stop"` |
| `data: [DONE]` | 字符串格式 |
| `{"error": "..."}` | 顶层 error 格式 |

---

## 15. 新增字段流程（3 步）

1. **更新本文档**：在对应事件节中增加字段行，标注类型/必填/含义，更新示例 JSON。
   经 WEB_CLAUDE 拍板后才能进入下一步。

2. **改 server 侧**：在 `lib/sse.py` 对应函数或 `litecode_server.py`/`core/multi_agent.py` 中
   输出新字段。字段必须可选（老消费者不能因缺字段而崩溃）。

3. **三端消费跟进**：
   - `litecodeext/cli.py`：在对应 `delta.get(...)` 处增加读取逻辑
   - `litecodeext/web_assets/app.js`：在 delta 解析段增加渲染逻辑
   - `litecodeext/wechat_bridge.py`：按需收集到 `result` 字典
   在本文档第 13 节（三端消费表）同步更新状态。

---

## msg_id 字段 (D4 新增, 2026-07-22)

每次 agent_stream 开始，第一个 chunk 携带 msg_id:

  data: {"choices":[{"delta":{"msg_id":"<12位hex>"}}]}

前端收到 msg_id 后，将当前 assistant 消息绑定此 id，用于后续 truncateAndResend 调用。

**产生位置**: `litecodeext/litecode_server.py` `agent_stream` 函数开头

**消费侧**: 生产前端 `web_assets/app.js`(SSE 解析)；`cli.py` / `litecli.py` / `wechat_bridge.py` 各自解析。〔2026-09-05 修正:原文写的 `web_assets2/net/sse.js` 属已流产的前端重写,从未接进生产〕

**字段冻结**: `choices[0].delta.msg_id` — string，12 位 hex，每次 stream 唯一
