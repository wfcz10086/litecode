<div align="center">

# ⚔️ LiteCode

### 最锋利的自托管 AI 兵器库 —— 一次部署,三端在场。

[English](README.md) · **中文**

*把一台自己的机器变成常驻的 AI 工程团队:Web 中枢指挥,CLI 贴身作业,微信 / 企业微信随身在线 —— 全部跑在你自己的容器里,接你自己的模型,数据不出门。*

`OpenAI 兼容` · `多模型` · `多模态` · `DAG 多代理` · `自托管` · `私有化`

![LiteCode Web UI](docs/screenshots/01_overview.jpg)

⭐ **如果它帮你省下一个周末的胶水代码,点个 star —— 这就是全部燃料。**

</div>

---

## 为什么是 LiteCode

- 🏠 **全私有化**:gateway(OpenAI 兼容 SSE)+ Web + CLI + bot 全在一个 Docker 容器,默认接自建 vLLM 或任意 OpenAI 兼容端点。密钥、会话、记忆、产物都落在本地磁盘。
- 🧩 **机架哲学**:放一个 `plugin.py` 即扩展,拔掉环境变量即卸载。外部项目零拷贝、零侵入挂进来。
- 🏗️ **AI 版 Jenkins**:Pipeline / Job / Build History / Artifact / 断点 / 续跑,心智直接照抄 Jenkins;AI 只做三件事——自然语言生成流水线、推参、汇总。
- 📱 **聊天即入口**:扫码把个人微信接进来,发张截图、丢个 PDF、说句话,背后是同一个 agent 主循环;企微侧还有真·流式回复。
- ⚡ **单机一台,火力全开**:为个人生产力而生的**单机暴力 AI 机器** —— 不废话、极致高效,专治"把事快速搞定"。

---

## 有什么不一样

不是又一个框架、又一个聊天前端 —— LiteCode 是**整台机器**:网关 + Web + CLI + 聊天 bot + 编排,全部自托管、接你自己的模型。

| | **LiteCode** | Dify | LangChain | Open WebUI |
|---|:---:|:---:|:---:|:---:|
| 单容器自托管 | ✅ | ✅ | 库 | ✅ |
| OpenAI 兼容网关 | ✅ | 部分 | — | 仅对话 |
| **Web + CLI + 聊天 bot** 同一个 agent 大脑 | ✅ | Web | 代码 | Web |
| 内置 **微信 / 企业微信 机器人** | ✅ | — | — | — |
| 可视化 **DAG 多代理**(Jenkins 式) | ✅ | flow | 代码 | — |
| **生图 / 视频 / 音乐生成** 插件机架 | ✅ | 工具 | 工具 | — |
| **对话操控桌面**(noVNC) | ✅ | — | — | — |
| 四级持久记忆 | ✅ | 部分 | — | 基础 |

_粗略定位,不是打分表 —— 上面每个工具在各自领域都很强。LiteCode 押的是"一台私有机器全都干"。_

---

## 刻意做得精简

Agent 框架总爱膨胀 —— 一堆要伺候的服务、一套要缠斗的权限、看不见的行为。LiteCode 反着来:

- **单容器,不是服务网格。** host 网络、`./start.sh`、完事。除了你的 agent 没别的要编排 —— 不臃肿、不繁琐。
- **权限不挡道。** 网关走 Bearer token、Web 走 cookie、按 IP 限流、产物按 owner 隔离,加轻量守卫(循环防护、只读 / plan 闸)。清爽极简 —— 不是让你缠斗一周的重型 RBAC。
- **没有黑盒。** trace、守卫注入统计、执行记录时间线 —— agent 到底干了什么、为什么,随时看得见。

---

## 功能一览

### 🔌 模型兼容 —— 想接什么接什么,想要什么模态给什么模态
- **OpenAI 兼容 `/v1/chat/completions`** + SSE 流式,LangChain / OpenAI SDK / Dify / Open WebUI 开箱直连。
- **一套传输层,五种后端栈**:`vllm` · `openai` · `anthropic` · `ollama` · `deepseek`。接同协议的新模型是纯配置。
- **运行时热切模型**(`/api/models/switch`)、Web 端每条消息可单独选模型/思考强度、思考参数适配器抹平各栈差异。
- **多模型 + 多模态**:文本、视觉、生成同在一个循环里。图片路由到任意视觉模型(如 Qwen-VL),视觉兜底可配。

### 🎨 生成式插件 —— 机架始终开放
插件化是**核心且保留的设计原则**:每一种生成能力都作为自成一体的文件插进来,core 一行不改。
- **生图 —— 已内置**:`image_gen` 开箱 4 个 provider(DALL·E 3 · Stable-Diffusion WebUI · ComfyUI · mock)。同协议纯配置;新协议 ~40 行 adapter。
- **视频 / 音乐 / 任意模态生成 —— 插件式插入**:加一个 `plugins/tools/*.py`(照抄生图骨架:分派→提交任务→轮询→取结果→emit 产物),config 里声明,registry 自动扫到。**不动 registry / 契约 / core / DAG** —— 这就是机架的意义。
- 插进来的能力自动成为 agent 可调的**一等工具**,也是可编排的 **DAG 步骤**。

### 🔍 多层搜索 —— 搜索是多重的,单个引擎被封也不卡
搜索**分层兜底**,一层被封不影响全局:
- **免费搜索 API** 打头 —— 快、不开浏览器。
- **本机 SearXNG sidecar** 聚合多引擎,私有 JSON 网关 —— 不撞限流、不吃 CAPTCHA。
- **多引擎 HTML 并行**(国内 7 + 国际 7 引擎)—— API 不够时上。
- **真 Playwright 浏览器**(单例、用完即关)—— 最后兜底,专治只有 JS 才渲染的页面。
- **`deep_search`** 把各层串起来:跑分层搜索 + 点进前几条结果 + 读正文原文(深度可配)。

### 🖥️ 命令行 CLI —— 三端一个大脑
终端是一等前端,不是附属品。`cli.py` 是直连网关的交互 REPL(流式、思考可折叠、两段式 `Ctrl-C` 中断、tool-call 实时渲染);`litecli.py` 是薄 REST 客户端,命令面完整 —— `chat · dag · model · sessions · memory · timer · wechat · wecom · docker · plugins · projects · stats · ws · config · repl`,外加 REPL 斜杠命令(`/think /interrupt /dag /memory /checkpoints /model …`)。`--output-format stream-json` 供 CI 消费。

### 🤖 微信 & 企业微信智能机器人
- **个人微信**:扫码登录 + 保活;支持图片、语音(Whisper 兜底)、文件、视频、引用消息,附件先缓冲待指令。
- **企业微信智能机器人**:真流式回复,图 + 文件 + markdown 出站,媒体分片上传 + 3 天缓存。
- 和 Web / CLI 同一个 agent 大脑 —— 一个循环,三副面孔。

### 🏗️ DAG 多代理编排(AI-Jenkins)
- 拓扑排序 + 同层并行 + Critic 回炉 + checkpoint 恢复 + 失败 self-reflection 重试。
- 节点类型:agent 步 / 原生工具步 / **子图 DAG** / **决策节点(2 出口)**;步骤间条件 `when` + `${step:id:json:path}` 传值。
- **自然语言 DAG**:一句话生成 / 修改 / 删除 / 排期一整条流水线。
- Web 端 drawflow 可视化编辑器 + 实时 Build 视图(运行中步、心跳、执行记录时间线)。

### ⏰ 定时任务
- cron / 单次定时器,可视化编辑 + 执行历史 + 通知。
- 定时触发 shell 命令、prompt,或一整条 DAG。

### 🧠 四级记忆(L0–L3)
- 滑动窗口 → 永驻 `MEMORY.md` → 分类型 `memory/*.md` → SQLite FTS5 跨会话全文召回。
- 规则触发自动写记忆(密钥自动脱敏)—— 不依赖模型主动调工具。

### 🖥️ 对话操控桌面
- `desktop_exec` / `screenshot` / `key` / `type` / `click` 在 noVNC 桌面里跑真浏览器/GUI,截图返聊天渲染。

### 📊 可观测性 & 工程纪律
- 迭代追踪、工具/子代理/DAG 埋点、守卫注入统计、token 记账、执行记录时间线。
- **静默失败可见化** + **每次守卫注入都计数** —— 让 agent 行为可解释,而不是黑盒。

---

## 快速开始

```bash
git clone <你的仓库地址> litecode && cd litecode
cp config.example.json config.json     # 填你的模型端点 + key
./start.sh                             # 自动检测,没镜像则构建
```

| 端口 | 用途 | 鉴权 |
|---|---|---|
| `:18789` | 网关 API(OpenAI 兼容) | Bearer token |
| `:18790` | Web UI + CLI 后端 | Cookie 密码 |
| `:18800` | noVNC 远程桌面 | 无 |

> 全部 bind-mount —— 改代码、`docker restart`、生效。单文件挂载改完要 `docker restart` 重建(inode 陷阱)。

---

## 截图

**DAG 编辑器 —— AI 版 Jenkins。** 拖 agent / 工具节点(coder · analyst · vision · `pptx_render` · `image_gen` · 决策 · 子DAG …),连线,按 **Build**。

![DAG 编辑器](docs/screenshots/03_dag_editor.jpg)

**CLI —— 终端也是一等前端。** 一句话 → 写文件、出 diff、跑 shell、报 token 用量。

![CLI](docs/screenshots/02_cli.jpg)

<details>
<summary><b>▸ 更多截图 —— 微信 / 企微机器人 · 记忆 · 模型 · 定时 · 产出物 · Docker · 项目 · CLI 命令</b>(点击展开)</summary>

| 微信接入 | 企业微信机器人 | 四级记忆 |
|:---:|:---:|:---:|
| ![](docs/screenshots/04_wechat.jpg) | ![](docs/screenshots/05_wecom.jpg) | ![](docs/screenshots/06_memory.jpg) |
| **模型管理** | **定时任务** | **产出物** |
| ![](docs/screenshots/07_models.jpg) | ![](docs/screenshots/08_timer.jpg) | ![](docs/screenshots/09_artifacts.jpg) |
| **用量成本** | **Docker 管理** | **项目** |
| ![](docs/screenshots/10_stats.jpg) | ![](docs/screenshots/11_docker.jpg) | ![](docs/screenshots/12_projects.jpg) |
| **Pipeline 列表** | **CLI 命令** | |
| ![](docs/screenshots/13_dag_list.jpg) | ![](docs/screenshots/14_cli_cmds.jpg) | |

</details>

---

## 架构

```
   微信 / 企微 bot ┐
   CLI (REPL)      ├──▶  网关 :18789  (OpenAI 兼容 SSE, agent 主循环)
   浏览器 ─────────┘            ▲
                              │ 同源代理
                  Web UI :18790  (DAG · 定时 · 记忆 · 模型/插件/容器 管理面)
```

不是三份对等实现 —— **Web 是全功能中枢,CLI 是(正在追赶的)子集,微信/企微是纯对话 bot**。三端真正重叠的只有"对话"。深入见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## 给个 star ⭐

LiteCode 由一个小团队在开源里打磨。如果它有用,点个 star 是对我们最实在的帮助 —— 别人就是这么找到它的。Watch + Star,再告诉我们你想拿它造什么。

## 许可证

MIT
