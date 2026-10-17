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
- **四段式子代理**:分析 → 计划 → 执行 → 验证;agent_type 有 coder / analyst / tester / writer / critic / explorer / shell / researcher / **vision** / **operator**(远程 Windows 操作员)。
- **写类子代理独立 git 工作树** (`lib/agent/subagent_worktree.py`):spawn_agent 带 `workdir`/`isolation`(auto|worktree|none)。auto = 本次派发有 ≥2 个写类子代理(allowed 含写工具的 coder/analyst/writer,加 tester/shell)并行或按 DAG 并发(两写步互不为祖先)且 workdir 在 git 仓库内 → 每个写类子代理 `git worktree add <WORKSPACE>/.agent_worktrees/<id> -b lc-agent/<id> HEAD`。挂法:派发处(`spawn_dispatch` / `handlers/agent.py`)用 `IsolationPlan.wrap` 包 run_subagent_fn,运行期经 ContextVar 挂 WorktreeCtx;`_run_subagent` 执行工具前调 `rewrite_tool_args`(原仓库根下绝对路径→工作树,相对路径以工作树中的 workdir 为基准,execute_shell 前置 `cd` + 绝对路径前缀替换),提示词告知工作目录,主代理与其他子代理不受影响。收尾:工作树 `add -A && commit` → 按仓库加 asyncio.Lock 串行 `merge --no-ff`;成功删工作树+分支,冲突 `merge --abort` 保留并列冲突文件;主工作区有已跟踪未提交改动则不自动合并;超时/中断/异常只提交不合并。后续由 `agent_worktree`(list/merge/discard/diff)处理,元数据在 `.agent_worktrees/_meta/`;启动(web_ui lifespan)与 list 时清理 24h 前已合并/已丢弃残留 + `git worktree prune`。所有 git 调用带超时与 `-c safe.directory=*`。局限:shell 里拼接出来的路径/`cd ..` 后访问映射不到;saved DAG(run_dag)路径未接入。
- **子代理交付证据** (`lib/agent/subagent_evidence.py`,开关 `agent.subagent_evidence` 默认 true):`_run_subagent` 只加 3 个调用点 —— 开头 `start_collector`、每次工具执行后 `observe(name, 原始 args, result)`、准备结束(无 tool_call)时 `nudge`。采集只看工具调用与结果,不信模型自述:execute_shell 分类为 test/build/run/syntax(py_compile 不算验证),退出码从执行器前缀推断(`ERROR[EXIT=N]`/`ERROR[TEST_FAIL]`/无前缀=0),测试计数解析 pytest/unittest/jest/vitest/mocha/go/cargo/TAP(管道吞掉退出码时以失败数为准),解析不了留尾部 20 行;写入文件(写工具 + shell `>`/tee/`sed -i`)及其后是否被 read_file / shell 提到(`git diff` 视为全部回读);windows_system inspect_file/list_dir 摘要;工作树提交/合并状态由 `subagent_worktree` 收尾后 `note_worktree` 送入。判定:写类角色(同 `is_write_role`)有改动 → 需 test/build/run 命令 exit 0 且失败数 0,或工具写入文件全部回读;其余 → 至少一次成功取证(非零退出的 shell 输出也算取证),零取证 = 模型自述 = 未验证。写类未验证准备结束时注入一次 `[SYSTEM-EVIDENCE]` user 消息(每子代理一次、最后一轮不注入,不进 SSE)。派发侧:`EvidencePlan.wrap` 包在 `IsolationPlan.wrap` 之外,经 ContextVar slot 拿到该次子代理的采集器与工作树报告;`decorate` 让主代理收到的结果开头为 `[已验证]` / `[未验证: 原因]`,末尾附「证据」小节(≤1500 字),多个子代理时给总标记并在「子代理证据判定」逐个列出(不改各步输出,免得破坏 pipeline/DAG 传值)。不经 spawn_agent 的 DAG 运行时只采集 + 补证 + 记日志。
- **远程操作员子代理 operator** (`lib/agent/subagent_operator.py`):让多台 Windows 节点并行干活。角色 allowed = windows_desktop / windows_system / read_file / vision_ocr(无 write_file/patch_file/execute_shell,模型越权直呼白名单外工具在子代理循环里直接拒绝),max_iter 60,单任务默认超时 1200s,不继承通用铁律而用自己的 GUI 规则(先截图、每步截图/OCR 确认、命令行优先、改文件必须 inspect_file 回读、只操作分配的 alias);结果不进 `_run_subagent_safe` 缓存(GUI 有副作用);`subagent_worktree.is_write_role` 对它为 False(不建服务器 git 工作树),证据模块单独把它算写类。**派发期**(`prepare_spawn_args`,spawn_dispatch 与 handlers/agent.py 两条路径):每个 operator 必须有 alias(任务项 `alias` 字段或顶层 `alias` 默认);parallel/competitive 中两个 operator 同 alias、或 DAG 中同 alias 且互不为祖先的步骤 → **整个派发拒绝**(ERROR,一个子代理都不跑,提示合并任务或改 pipeline/depends_on 串行;不选自动串行:并行编排器各任务超时独立计,排队会吃掉超时,且模型以为在并行);pipeline/batch 天然串行允许。alias 以 `[操作员节点 alias=X]` 行追加进 task 文本,编排器各路径(并行/管道/DAG/批量/重试)原样透传 task,子代理据此取 alias,不会串号。**运行期**(`_run_subagent` → `run_as_operator` → `_run_subagent_core(sid=...)`):按 alias 的 asyncio.Lock 独占,操作员之间(跨派发/跨会话)排队,最多等 `agent.operator_lock_wait`(默认 120s)仍拿不到 → `[子代理异常]` 结束;占用期间登记 `_OWNER[alias]`、`_SID_ALIAS[sid]`。`check_windows_call` 在子代理循环与 windows_desktop/windows_system handler 各调一次:操作员 alias 不符 → 拒绝、未填 → 补成分配的;非操作员调用方(主代理/其他会话)对被占 alias → **立即 ERROR 不等待**(主代理在等自己的子代理,排队只会死锁)。**截图**:子代理 sid 唯一(`subagent_operator_<hex>`),`_LAST_SHOT[(sid, alias)]` 天然按子代理隔离;拿锁时清掉其他会话该 alias 的旧截图、结束清掉本 sid 的。**视觉回流**:子代理循环在补齐 tool 消息后调 `lib.desktop_observation.append_latest_observation`(与主循环 DESKTOP-VISION 同一实现),用**未截断**的 windows_desktop 原始结果(子代理会把 >3000 字的结果截断,截断后 JSON 解析不出图片路径),只保留最新一张图;模型是否真看图由 transport 按配置决定(supports_vision → 直接看;否则配了 vision_fallback_id 临时路由视觉模型;都没有剥图留文字),与主循环一致。观察类动作(screenshot/ocr/windows/clipboard/scroll、windows_system 只读动作)对操作员不计入同参数死循环检测。**证据**:文件改动(`readback_gate.is_file_change`:保存/另存/粘贴/剪切/删除/重命名/拖拽,加 windows_system upload)之后必须有成功回读(inspect_file/list_dir/run_powershell/download,与回读闸 READBACK_ACTIONS 一致)才 verified,截图/OCR 不算;没改文件但做过界面动作 → 最后一次动作之后有 screenshot/ocr(或回读/processes 等)确认才 verified;只观察 → 通用取证规则。补证提示为操作员专用文案。
- **后台子代理 + 主代理补充指令** (`lib/agent/subagent_registry.py`):spawn_agent 带 `background=true`(只支持单个 task 或 parallel_tasks;dag/pipeline/competitive/batch 拒绝)→ 每项一个 `asyncio.ensure_future` 后台任务,立即返回 `sa-xxxxxx` id/角色/任务摘要,主循环不等待。每个后台子代理照旧套 `EvidencePlan.wrap(IsolationPlan.wrap(_run_subagent_safe))`(操作员锁在 `_run_subagent` 内),超时同单代理模式,结果 = `decorate(out + 工作树汇总)`,与阻塞模式格式一致;因与主代理同时改文件,`isolation=auto` 时写类后台子代理一律走独立工作树(workdir 是 git 仓库时)。**登记表**:阻塞模式的子代理也经 `track()` 登记(结果原样返回,行为不变,视为已取走);记录含 sid/角色/状态(running/done/failed/stopped)/开始时间/当前迭代/最近工具调用/结果,结束后保留 `agent.subagent_record_ttl`(默认 3600s),后台结果未取走保留 24h。**结果落盘(重启兜底)**:内存 `_RECORDS` 仍是主路径;**后台**子代理结束时(done/failed/stopped,仅结束态、非热路径)把记录快照原子写到 `<WORKSPACE>/telemetry/bg_agents/<sid_safe>/<id>.json`(result 超 64KB 截断并标注,内存里 result 保持完整),结果被取走时再写一次同步 `fetched`(改走较短 TTL);**进程首次使用 registry 时**从磁盘恢复未过期的已结束记录到内存(标 `restored`,状态原样),使 `agent_control result/list/status` 重启后仍能取回;残留的运行态文件(正常不该有)恢复后标 `interrupted`(服务重启、进程已不在),不显示成仍在跑;运行中的子代理不落盘(重启后进程已没,算中断)。**清理**:与内存 TTL 一致过期即删文件,每会话磁盘上限 `MAX_PER_SESSION`(50)条、超出按结束时间淘汰最旧;sid/id 清洗成安全文件名([A-Za-z0-9_-])按 sid 分目录,防路径穿越与跨会话读取。**按会话隔离**:`agent_control` 只认本会话 sid 的记录,别的会话的 id 一律"找不到"。**子代理侧钩子**(`_run_subagent_core`,经 ContextVar 拿到本子代理记录,无记录时全部短路):每轮迭代开始 `tick()` —— 更新迭代数;有停止请求 → 返回 `[子代理已中断] 主代理请求停止...`(工作树只提交不合并、证据判未正常结束,复用既有前缀逻辑);信箱有补充指令 → 以 `[来自主代理的补充指令]` 前缀注入 user 消息(上一条是 user 时合并进去)并记日志;准备收尾时信箱仍有指令 → 多跑一轮;工具执行后 `note_tool()`;后台子代理不检查父会话中断标记(不随主回合中断停);收到过补充指令的结果不进 `(agent_type, task)` 结果缓存。子代理消息不入会话历史、不推 SSE,所以注入不进用户可见内容与记忆提取。**agent_control** 工具:list / status / send(≤2000 字,已结束/已请求停止报错)/ wait(默认 300s 上限 1800s,0.2s 轮询,不给 id 等本会话全部在跑+未取的;超时或主会话被中断返回进度不报错)/ result / stop。**并发上限**:每会话同时运行的后台子代理 `agent.max_background_subagents`(默认 4),超过拒绝整个派发。**回合边界**:`_turn_finalize` 在 sse_usage 前调 `turn_end_note()` —— 还有后台子代理在跑则不强杀,在最终回复后追加一条简短系统备注(SSE 推送 + 写进最后一条 assistant 消息落库);下一轮 `turn_sources` 的 `agents` 来源(order 70)在本会话有已结束未取走的后台子代理时给一条 `[SYSTEM-AGENTS]` 提示(挂 turn_context 字段:界面不显示、记忆提取前 strip,同一提示历史里给过不重复;turn_context 关闭时进 system prompt)。局限:后台子代理**已结束的结果**已落盘(`telemetry/bg_agents/`),服务重启后仍可 `agent_control result` 取回,但**运行中**的后台子代理在重启时会丢(进程已不在,其工作树仍留在 `.agent_worktrees/` 可用 agent_worktree 处理);CLI 每轮 `asyncio.run` 的场景后台任务会随事件循环关闭被取消;滚动摘要(每 8 轮)会把较早的补充指令压成摘要行。
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
| 外部插件 Web 路由 | `plugin.py` 可选导出 `ROUTER`/`ROUTER_ORDER`/`ROUTER_PUBLIC`,默认要求登录 | 🟢 真插拔(启动时挂) | `routers/__init__.py` `discover_plugin_routers` |
| 外部插件前端面板/资源 | `plugin.py` 可选导出 `WEB_ASSETS`(目录)/`WEB_ENTRY`(.js/.css 列表);`/plugin-assets/{插件}/{path}` 只读托管,主页 `</body>` 前按插件名注入;JS 调 `window.LiteCodePlugins.registerPanel()` 在侧栏"接入"加面板 | 🟢 真插拔(拔 env 即消失) | `routers/plugin_assets_router.py`, `web_assets/plugin_panels.js` |
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
