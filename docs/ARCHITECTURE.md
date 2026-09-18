# LiteCode 架构与框架思维

> **定位**: 本文沉淀 LiteCode 的**慢变量**——设计哲学、整体架构、子系统职责、埋点/测试/迭代方法。
> 易变的数字(行数、工具数、版本号)一律以 `git log` + `BACKLOG.md` 为准,本文不复述以免过时。
> **最后对齐**: 2026-09-05(一次跨 CLI/Web/企微 + 死代码 + 插件 + 埋点 + 文档的全景审计后)。

---

## 1. 框架思维(为什么这样设计)

三条贯穿全项目的原则,改任何东西前先对齐它们:

### 1.1 LiteCode 是"机架"(chassis),不是集成怪兽
外部能力(`/opt/pptx`、`/opt/cad` 等)**留在原地当独立项目**,靠 `LITECODE_PLUGIN_PATH` + 各自 `plugin.py` adapter 挂载进来,**拔掉环境变量即卸载**。不把别人的项目源码拷进本仓、不写"一体化 tool 内部串 outline+comfy+OCR"那种巨石——要串,拆成 DAG 步骤,每步走外部 API。

### 1.2 插件化是核心,不是可选项
新增能力的默认姿势是**加一个文件**,而不是改主干。三个层次(详见 §5):
- 🟢 **真插件** — 放文件/拔文件即装卸(`plugins/tools/*.py` 被 registry 自动扫描)。
- 🟡 **配置驱动** — 改 `config.json`/preset 即生效(模型后端、子代理配置、when 算子)。
- 🟠 **插入式分支** — 加一个节点类型/回调,不动引擎(DAG 节点类型、决策节点)。

### 1.3 DAG = AI 版 Jenkins
心智直接照抄 Jenkins:Pipeline / Job / Stage / Artifact / Build History。AI 的加值只做三件事:**生成**(自然语言 → DAG)、**推参**(步骤间传值)、**汇总**(把各步产物收敛成结论)。不做图形化数据流编程(那条路线见 §9 已废弃的 V3_ARCHITECTURE)。

---

## 2. 整体架构

### 2.1 三端拓扑(不是三份对等实现)
```
                    ┌─────────────────────────────────────────┐
   微信 bot ───┐    │  gateway  litecode_server.py  :18789      │
   企微 bot ───┼──▶ │  /v1/chat/completions (OpenAI 兼容, SSE)  │
   cli.py REPL ┘    │  ← agent 主循环 / 工具分派 / 记忆 / 埋点   │
                    └───────────────▲───────────────────────────┘
                                    │ 同源代理
                    ┌───────────────┴───────────────────────────┐
   浏览器 ─────────▶│  web_ui  web_ui.py  :18790  (cookie 鉴权)  │
   litecli.py ─────▶│  DAG API / 定时 / 记忆写 / 模型&容器&插件面 │
                    └───────────────────────────────────────────┘
```
- **Web 是全功能中枢**:对话 + DAG(19 端点,含断点/中止/续跑/产物)+ 定时任务 + 记忆读写 + 模型/容器/插件/artifacts 管理面。
- **CLI 有两个入口**:`cli.py` 是交互式 REPL,**直连 gateway**(打字机/中断/toolcall 体验的主体);`litecli.py` 是 **Web REST 的薄客户端**(管理命令)。CLI 能力 = Web 的子集,且部分新端点(memory 写、DAG 运行时控制、artifacts、每条消息模型参数)**尚未跟上**。
- **微信 + 企微是纯对话 bot**:直连 gateway,**不做管理面**(设计如此)。企微是微信 router 的超集(多 contacts/messages/upload);企微入站目前只认 image/voice,file 消息会被丢弃(已知缺口)。

### 2.2 进程与端口
| 进程 | 端口 | 说明 |
|---|---|---|
| `litecode_server.py` | 18789 | gateway,OpenAI 兼容,agent 主循环所在 |
| `web_ui.py` | 18790 | Web UI + 管理 API + 定时器,cookie `lc_auth` 鉴权 |
| `wechat_bridge.py` / `wecom_*bridge.py` | — | 桥接进程,连 gateway |

容器 `litecode` 走 **host network**;单文件 bind-mount(如 `litecode_server.py`/`dag_state.py`)改完必须 **`docker restart`** 重建 inode + 重载进程(Python 只在启动 import 一次,且单文件 mount 有 inode 陈旧陷阱)。

### 2.3 代码组织
顶层入口 4 个(`litecode_server.py` / `web_ui.py` / `wechat_bridge.py` / `cli.py`)+ 按职责分组的 `core/` + 共享 `lib/` + 模块化 `routers/` + `plugins/`(工具/adapter/artifacts)+ `prompts/` + `skills/` + `tests/`。具体文件数以实际代码为准(PROJECT_MAP.md 的计数已冻结在 5 月,勿引用)。

---

## 3. Agent 主循环
- **四段式子代理**:分析 → 计划 → 执行 → 验证;agent_type 有 coder / analyst / tester / writer / critic / explorer / shell / researcher / **vision**。
- **收尾闸 (Exec-Gate)** + **回合收尾埋点 [TURN-END]**:拦"规划→执行断链",让 A/B 可解释。
- **循环防护**:同 (tool,args) 连调注入 SYSTEM 阻断;reasoning 累积超限中断(有对用户友好的文案化空间,见 BACKLOG)。
- **task 边界快照 + 回滚**:给中大型任务一个安全网(`lib/snapshot.py`)。

## 4. 记忆 L0–L3
- **L0** = 滑动窗口 `messages[]`(token budget 硬裁)。
- **L1** = `MEMORY.md` 永驻 sys_prompt 顶部,行数硬上限自动归档。
- **L2** = `memory/*.md` 按类型分文件(feedback/user/project/reference)。
- **L3** = `.memory_index.db` SQLite FTS5,跨 session 全文检索,新 session 自动注入。
- **偏好自动捕获**:规则触发写 L1+L3,不依赖 LLM 主动调 `save_memory`;token/key/password 自动脱敏。
- ⚠️ 记忆压缩(compress)**必须关思考**——开思考曾导致记忆全面失效(退化到几百字节)。

## 5. 插件体系(项目核心)与如何接新能力

| 维度 | 机制 | 成色 | 位置 |
|---|---|---|---|
| plugin 挂载 | registry 扫 `tools/*.py` + 外部 `LITECODE_PLUGIN_PATH` 下 `plugin.py` | 🟢 真插拔 | `plugins/registry.py`, `_contract.py` |
| 外部项目 adapter | `/opt/cad`、`/opt/pptx` 靠环境变量挂入 | 🟢 真插拔 | `registry._external_candidates` |
| 生图 provider | config 实例 + `_DISPATCH` 类型 dict | 🟡 半插拔 | `plugins/tools/imagegen.py` |
| 模型后端 | `config.models[]`,按 backend_type/api_format 分派 | 🟡 配置驱动 | `config.json`, `lib/thinking_adapter.py` |
| web 搜索引擎 | `config.search.*[]` URL 模板 | 🟡 半插拔 | `plugins/tools/web_search.py` |
| DAG 节点类型 | agent_type / tool / dag(子图)/ 决策节点 | 🟠 插入式 | `core/orchestrator/*` |

### 接生图 / 生成视频(可插拔式)
- **生图**:接同协议服务商 = 纯 config 加实例、零代码;接新协议(Midjourney/可灵异步任务流)= `imagegen.py` 加 ~30-50 行 adapter + 登记 `_DISPATCH` + config,单文件闭环。
  - ⚠️ 加 provider 时注意 `_image_gen` 里按 `prov_type` 硬判断调用签名的 if 分支(2参 vs 3参),值得顺手统一成单一签名。
- **生成视频**(当前零能力,可一个文件插进来):新建 `plugins/tools/videogen.py` 照抄 imagegen 骨架(`_DISPATCH`={kling/runway/comfyui_video/mock},视频多是"提交task→轮询→取url"复用 comfy poll 写法,`TOOLS=[video_gen]` emit `video/mp4`)+ config 加 `videogen` 段。registry `_internal_candidates` 自动扫到,**不动 registry/contract/core/DAG**。唯一额外:前端加个 `<video>` 预览分支。

## 6. 上下文 / token(优化重灾区,见 §8)
- **usage 锚点**:请求带 `include_usage`,抓上游真实 `prompt_tokens` 校准预算,不再纯估算;并回写 token_stats(真值优先,消除思考模型账单偏差)。
- 历史里的 `reasoning_content` 是撑爆窗口主因 → 剥离 + 计入预算。
- 上下文窗口(`max-model-len`)是**天花板不是速度旋钮**:调低 → 更多 KV 并发。当前自建 96000。

---

## 7. 埋点 / 可观测性(9 套机制)

> 现状:**写得多、连得散、读不了**。全局开关 `config.iteration_trace.enabled`(当前 true/VERBOSE)。

| 机制 | 落点 | 记什么 |
|---|---|---|
| itrace / Tracer | `logs/iteration_full.log` | trace 树:iteration/tool/llm/DAG_STEP/critic/循环检测/TRACE_END |
| telemetry.emit | `workspace/telemetry/*.jsonl`(tools/subagent/skills/rag/mcp/plan_act/dag/sse) | 5元组 ts/trace_id/session/event/latency |
| GuardStats | `workspace/guard_stats.json` | 各守卫注入次数(SYSTEM-TEST/BATCH/READONLY/PLAN-GATE) |
| token stats | `workspace/token_stats.json` | 累计 token(现已真值优先)+ 提问历史 |
| usage 锚点 | 内存(单请求) | 真实 vs 估算 prompt_tokens 比值 |
| audit_log | `~/.litecode/audit.log` | 管理操作审计(sessions/dag router) |
| exec_log | DAG job dict + `/api/dags/jobs/{id}/log` | DAG step_begin/done 时间线 |
| Python logging | stderr | 自由文本 |
| 散点 jsonl | `empty_response.jsonl` / `wechat.jsonl` | 空响应 / 微信事件 |

**已知盲区**(排查前先知道查不到的地方):
- 记忆子系统(compress/save)只有 log,无 trace/telemetry。
- itrace 的 `get_dashboard_data`/`memory_state`/`checkpoint_event`/`data_chain` 是**死代码**(零调用者)。
- token:SSE 给客户端的 usage 仍是估算,`stats` 已改真值优先(两口径待统一)。
- 半数管理路由(model/memory/docker/timer/wecom)**无 audit**。
- itrace 与 telemetry 两套 trace_id **不互通**,无法 join。
- **无读取面**:除 `/api/dags/jobs/{id}/log` 外没有端点读日志,排查靠 SSH grep。

> **2026-09-05 修复**:独立 DAG job 此前不传 tracer(→NullTracer no-op,build-history 全盲),现已接真 tracer,`DAG_STEP_START/END` 正常落 `iteration_full.log`。

---

## 8. 优化经验(踩过的坑,别重演)
- **token 低估撑爆上下文**:中文 token 低估 + 固定开销(system prompt + tools schema)盲区,是"明明没多少内容却爆窗口"的真因 → usage 锚点校准。
- **thinking on/off**:关思考主伤多步推理(实测 100%→71%),提速仅 ~25% 且难题反而更慢,**agent 任务别关**;唯独记忆压缩必须关。
- **68k 数据喂一步**:一步塞大数据会让 agent 空转几十轮 → 拆"提取(脚本)"与"推理(LLM)"两段。
- **checkpoint 按 plan_id(步骤内容哈希)缓存**:同内容重跑 instant-restore,写测试要塞 nonce 否则测的是缓存。
- **相对路径静默拼进 WORKSPACE** 导致代码散落 → 现在显式告知。
- **部署漏挂**(wechat_bridge/prompts/skills 没进容器)是"微信 181s 截断"的真因 → 挂载清单要全。

## 9. 迭代流程
- **真相源 = `BACKLOG.md`**(扁平、按优先级:BLOCKERS → P0 → P1 → P2 → Inbox)。`TODO.md`/`HANDOFF.md` 已弃用。
- **`/iterate`**:读 BACKLOG 选第一个未勾项 → CODER → TESTER → REVIEWER 五道闸 → commit → 勾选归档。全程不问用户,歧义写 BLOCKER。
- **`/refactor`**:⛔ **已停用(2026-09-05)**——它驱动的 `web_assets2/` 原生 ES Modules 前端重写从未接进生产(生产回落 `web_assets/app.js`),`docs/FRONTEND_REFACTOR.md` 已标流产 + BLOCKERS,skill 触发即退。
- **已废弃方向**:`docs/V3_ARCHITECTURE.md`(ComfyUI 节点式图形编程 RFC)被 `DAG_PRODUCT_PLAN.md`(AI-Jenkins)取代。

## 10. 测试
- **一键全覆盖回归**:`bash reports/ui_audit/playwright/verify.sh`——真浏览器 playwright,`full_verify.py`(16 项:DAG 新功能 / 实时可观测性 / 定时 / 对话)+ `edge_verify.py`(11 项:刷新持久 / 中止 / 3并行 exec_log / 多条件 skip)= **27 项**。用 task nonce 绕开 checkpoint 缓存。
- **单测**:`litecodeext/tests/`(scaffold/DAG/CLI 专属测试)+ 顶层 `tests/`(插件/artifacts/smoke)。
- **交付纪律**:前端改动必须 playwright 模拟真人点击/输入,`curl`/`TestClient` 不算过。

---

## 11. 历史版本迭代(时间线)

> 项目历史上并存过两套版本号——早期 `v12.x`(见 `CHANGELOG.md`,停在 v12.7 / 2026-04)与后来的 `v1.x`(见 README,停在 v1.14 / 2026-05)。**两者均已冻结、互相矛盾,现改用 git log 为准**。下表按主题归纳近期演进(非严格版本号):

| 阶段 | 主题 | 代表性改动 |
|---|---|---|
| ~v1.13 (05-09) | E2E 自治回放 | 81/81 PASS;SSE 契约 `/api/chat/{sid}`;DAG 卡片化编辑 |
| ~v1.14 (05-20) | 桌面操控 + L1/L3 | 4 桌面工具(noVNC chromium);偏好自动捕获;browser_server 路径修复 |
| 05 下旬 | 自建线路切换 | DSv4 下线 → Qwen3.8-Flash-Next;思想方法论进 prompt |
| 06–07 | 评测与 SWE-bench | 20/20 任务集;SWE-bench harness 两层缺陷修复 + diff-vs-golden 评分;易经 Go 复刻 |
| 07–08 | 微信可靠性 + 记忆 | "指令做不完"三真因;记忆压缩关思考;人设模板进生产;计量口径统一 |
| 08 下旬 | 上下文治理 | 中文 token 低估 + 固定开销盲区;reasoning_content 剥离;vision 图到即描述落库 |
| 09 初 | 可观测性 + 安全网 | 静默失败可见化 + 守卫统计;usage 锚点校准预算;task 快照回滚;strategist 权谋模式;reasoning_effort 兼容 |
| 09-05 | 全景审计后修复 | 幻影"推理中"工具行;/refactor 死线停用;独立 DAG job 接 itrace;token 真值回写;CLI 退出码边界;卫生清理(reports/_archive 取消跟踪、孤儿清理、文档对齐) |

> 完整、权威的迭代记录请 `git log`;进行中/待办请看 `BACKLOG.md`。
