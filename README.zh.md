<div align="center">

# ⚔️ LiteCode

### 一台机器。三条战线。零云依赖。

[English](README.md) · **中文**

**最锋利的自托管 AI 兵器库。** 一句 `./start.sh`,把一台你自己的机器变成常驻 AI 工程团队 —— Web 是指挥部,终端是贴身武器,微信 / 企业微信是揣在兜里的随身终端。你的模型、你的硬盘、你说了算。

![MIT](https://img.shields.io/badge/license-MIT-green) ![Docker](https://img.shields.io/badge/docker-%E5%8D%95%E5%AE%B9%E5%99%A8-blue) ![OpenAI](https://img.shields.io/badge/API-OpenAI%E5%85%BC%E5%AE%B9-orange) ![Private](https://img.shields.io/badge/%E7%A7%81%E6%9C%89%E5%8C%96-100%25-red) ![Bots](https://img.shields.io/badge/%E5%BE%AE%E4%BF%A1%2F%E4%BC%81%E5%BE%AE-%E5%86%85%E7%BD%AE-brightgreen)

![LiteCode Web UI](docs/screenshots/01_overview.jpg)

⭐ **如果 LiteCode 今天帮你干成了一件事,请回它一颗 star —— 你花 3 秒,我们满血。**

</div>

---

## 先看数字

| 72 | 92 | 9 | 14 | 5 | 4 | 3 | 1 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 原生工具 | 技能 | agent 类型 | 搜索引擎 | 模型后端栈 | 级记忆 | 端(Web/CLI/bot) | 个容器 |

不用集群、没有 SaaS 账单、数据一个字节都不出门。

---

## 装好第一天,它就能帮你干这些

- 📄 **往微信里丢个 PDF** → 手机上直接收到摘要,跑在你自己的模型上。
- 🗣️ **说一句** *"扫描这个仓库,写份架构报告"* → LiteCode **自己搭出一条 3 步流水线**(扫描 → 分析 → 写作),像 Jenkins 跑 build 一样跑完。
- ⏰ **每天早上 9 点**,cron DAG 自动拉你关心的数据,推一份简报到企业微信 —— 你咖啡还没喝完。
- 🖼️ **贴张截图** → 视觉模型直接读出来;**要 PPT** → pptx 插件现场渲染;**要配图** → `image_gen` 已接好 DALL·E / SD / ComfyUI。
- ⌨️ **终端里一句话** → 写文件、出 diff、跑 shell、报 token 账。CLI 是武器,不是摆设。

---

## 特色玩法 —— 92 项技能,不止干活,还有性格

### 💞 陪伴模式(Companion)
你说"今天好累",它不会甩你一句"要多运动早点睡"。陪伴模式**只倾听、共情、具体地看见你**——"你能撑到现在,是真的用尽力气了"。不说教、不灌鸡汤、短句慢聊,emo / 失眠 / 被卷 / 心累时**自动切入**。AI 里少见的、真给情绪价值的模式。

### 🎭 权谋推演(Strategist)
一键开启"多方博弈"视角:**先问谁得利,再信是不是真的**。自动拆解 利益方 / 博弈结构 / 关键变量 / 正反两面 / 情景推演——看行情、鉴别 AI 泡沫、分析一条新闻谁在推,都不再照单全收表面叙事。

### ✍️ 21 种题材的小说流水线
玄幻 · 修真 · 武侠 · 都市 · 诡异(克苏鲁/规则怪谈)· 科幻 · 言情 · 宫斗 · 系统流 · 无限流 · 穿越 · 重生 · 悬疑推理 · 军事 · 历史/架空 · 末世 · 电竞 · 西幻 · 轻小说……**每种题材一个专门 skill**,配上长篇工程化(大纲 → 章节 → 连贯性)和 **anti-ai-tell 去 AI 味审计**(61 → 100 分的打磨流水线),写出来的不是"AI 腔"。

### 📊 深度报告(deep-report)
5000–20000 字,**边写边搜**,引用密度自动检查——不是一口气编出来的,是一段一段查证出来的。

### 💰 行情与数据
crypto-tracker 盯盘 + 定时简报推微信/企微;data-analysis / data-scraper / log-analyzer,数据抓取、分析、日志排查一条龙。

### 🛠️ 工程师套装
中文 code review · bug 定位 · API 脚手架 · 系统化调试 · TDD · Linux 运维 · 远程 SSH 运维(linux-admin / remote-ops)——从"帮我看看这段代码"到"上那台服务器把容器参数改了",全是一句话的事。

### 📄 办公全家桶
docx / xlsx / pptx 读写生成 · PDF 智能读取 + OCR 管线 · HTML 转报告 · 长文本处理——微信里丢个文件,处理完还你一个文件。

> 还有彩蛋:头脑风暴、产品经理模式、主题工厂、甚至一个"毛泽东思想"顾问 skill。技能是**文件即插件**——照着 `skill-creator` 写一个 `SKILL.md` 丢进去,你的 AI 就多一门手艺。

---

## 截图

<table>
<tr>
<td align="center" width="33%"><b>🏗️ DAG 编辑器 —— AI 版 Jenkins</b><br><img src="docs/screenshots/03_dag_editor.jpg" width="100%"></td>
<td align="center" width="33%"><b>⌨️ CLI —— 一句话→工具→diff→搞定</b><br><img src="docs/screenshots/02_cli.jpg" width="100%"></td>
<td align="center" width="33%"><b>📱 微信接入 + 视觉兜底</b><br><img src="docs/screenshots/04_wechat.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>🤖 企业微信智能机器人</b><br><img src="docs/screenshots/05_wecom.jpg" width="100%"></td>
<td align="center"><b>🧠 四级记忆</b><br><img src="docs/screenshots/06_memory.jpg" width="100%"></td>
<td align="center"><b>🔌 模型管理 —— 热切换</b><br><img src="docs/screenshots/07_models.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>⏰ 定时任务(cron / 单次)</b><br><img src="docs/screenshots/08_timer.jpg" width="100%"></td>
<td align="center"><b>📦 产出物仓库</b><br><img src="docs/screenshots/09_artifacts.jpg" width="100%"></td>
<td align="center"><b>📊 用量与成本看板</b><br><img src="docs/screenshots/10_stats.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>🐳 对话里管 Docker</b><br><img src="docs/screenshots/11_docker.jpg" width="100%"></td>
<td align="center"><b>🗂️ 项目 —— 共享上下文</b><br><img src="docs/screenshots/12_projects.jpg" width="100%"></td>
<td align="center"><b>⌨️ CLI 完整命令面</b><br><img src="docs/screenshots/14_cli_cmds.jpg" width="100%"></td>
</tr>
</table>

---

## 凭什么是 LiteCode

- 🏠 **100% 私有,零云依赖。** 网关(OpenAI 兼容 SSE)+ Web + CLI + bot **全在一个** Docker 容器。接自建 vLLM 或任意 OpenAI 兼容端点。密钥、会话、记忆、产物 —— 全落你的盘。
- 🧩 **机架,不是怪兽。** 放一个 `plugin.py` → 新能力上线;拔掉环境变量 → 干净卸载。`pptx`、`cad` 从外部挂进来,**一行代码不用拷**。
- 🧠 **动态记忆压缩,越聊越懂你。** 四级记忆 + 真值校准预算 + 自动压缩 + 崩溃恢复——长对话不断片、跨会话不失忆,同级 agent 里罕见的"大象级记性"。这让它不止是工具箱,而是一台**通用智能体载体**。
- 🏗️ **AI 版 Jenkins。** Pipeline / Job / Build History / Artifact / 断点 / 续跑 —— 你早就信任的心智模型。AI 只加三件事:**一句话生成流水线、自动推参、汇总结果。**
- 📱 **全网罕见:真·微信 + 企业微信机器人内置。** 扫个码,个人微信秒变 AI 终端;企微是真流式回复。和 Web / CLI 同一个大脑。
- ⚡ **单人火力全开的暴力效率机器。** 不用跟基础设施开会。`./start.sh`,开干。

---

## 有什么不一样

| | **LiteCode** | Dify | LangChain | Open WebUI |
|---|:---:|:---:|:---:|:---:|
| 单容器自托管 | ✅ | ✅ | 库 | ✅ |
| OpenAI 兼容网关 | ✅ | 部分 | — | 仅对话 |
| **Web + CLI + 聊天 bot** 同一个大脑 | ✅ | Web | 代码 | Web |
| 内置 **微信 / 企业微信机器人** | ✅ | — | — | — |
| 可视化 **DAG 多代理**(Jenkins 式) | ✅ | flow | 代码 | — |
| **生图 / 视频 / 音乐** 插件机架 | ✅ | 工具 | 工具 | — |
| **对话操控桌面**(noVNC) | ✅ | — | — | — |
| 四级持久记忆 | ✅ | 部分 | — | 基础 |

_粗略定位,不是打分表 —— 每个工具在自己领域都很强。LiteCode 押的是"一台私有机器,全都干"。_

---

## 刻意做得精简

Agent 框架总爱膨胀 —— 一堆要伺候的服务、一套要缠斗的权限、看不见的黑盒。LiteCode 反着来:

- **单容器,不是服务网格。** host 网络、`./start.sh`、完事。
- **权限不挡道。** Bearer + cookie + 限流 + owner 隔离 + 轻量守卫闸。清爽极简 —— 不是让你缠斗一周的重型 RBAC。
- **没有黑盒。** trace、守卫注入统计、执行记录时间线 —— agent 干了什么、为什么,随时看得见。

---

## 功能纵览

### 🔌 想接什么模型接什么,要什么模态有什么模态
OpenAI 兼容 SSE 网关(LangChain / OpenAI SDK / Dify / Open WebUI 开箱直连)· 五种后端栈(`vllm / openai / anthropic / ollama / deepseek`)· 热切模型 · 每条消息单独选模型/思考强度 · 视觉路由 + 可配兜底。

### 🎨 生成式插件 —— 机架始终开放
生图**已内置**(DALL·E 3 · SD-WebUI · ComfyUI · mock)。视频 / 音乐 / 任意模态:**一个文件插进来**(照抄生图骨架),registry 自动发现,立刻成为 agent 可调工具 **兼** DAG 步骤。core 一行不动。

### 🔍 多层搜索 —— 永不被卡死
免费搜索 API → **本机 SearXNG**(私有 JSON 网关,不吃 CAPTCHA)→ 14 引擎并行 HTML → **真 Playwright 浏览器**兜底 → `deep_search` 点进结果读正文。封掉一个引擎,伤不到你分毫。

### 🏗️ DAG 多代理编排
拓扑排序 · 同层并行 · Critic 回炉 · checkpoint 恢复 · 失败自反思重试。节点:agent / 原生工具 / **子DAG** / **决策(2 出口)**;`when` 条件 + `${step:id:json:path}` 传值。**自然语言四件套**:说句话就能 生成 / 修改 / 删除 / 排期 流水线。

### ⌨️ CLI —— 三端一个大脑
直连网关的交互 REPL:流式、思考折叠、两段式 `Ctrl-C` 中断、tool-call 与 diff 实时渲染。完整命令面:`chat · dag · model · sessions · memory · timer · wechat · wecom · docker · plugins · projects · stats · ws · config · repl` + 斜杠命令(`/think /interrupt /dag /memory /checkpoints …`)。`--output-format stream-json` 供 CI。

### 🧠 动态记忆压缩 —— 同级 agent 里最能"记"的
对话越长,一般 agent 越傻——上下文一满就断片。LiteCode 的记忆是一台**动态压缩机**:
- **四级分层 L0–L3**:滑动窗口 → 永驻 `MEMORY.md` → 分类记忆文件 → SQLite FTS5 跨会话全文召回。新会话自动带着"你是谁、聊过什么"上场。
- **真值校准的上下文预算**:用上游回报的**真实 token 用量**锚定预算,不靠拍脑袋估算——该压缩时精准压缩,不该压时一个字不丢。
- **自动压缩 + 自动捕获**:聊到阈值后台自动压缩;你的偏好 / 配置**按规则自动写入**(密钥自动脱敏),不指望模型"记得存"。
- **崩了也不丢**:会话 WAL 崩溃恢复 + task 边界快照回滚——断电、重启、误操作,都有后悔药。

一句话:别的 agent 是金鱼,LiteCode 是**带档案柜的老管家**。这也是它敢自称**通用智能体载体**的底气——记忆、工具、技能、编排全是可插拔的地基,上面长什么智能体都行。

### 🖥️ 对话操控桌面 & 更多
聊天里驱动 noVNC 真桌面(`desktop_exec / screenshot / key / type / click`)· 定时任务 + 执行历史 + 通知 · 产出物仓库 · Docker 管理 · 用量成本记账。

---

## 60 秒上手

```bash
git clone https://github.com/wfcz10086/litecode && cd litecode
cp config.example.json config.json     # 填你的模型端点 + key
./start.sh                             # 没镜像自动构建, 然后跑起来
```

| 端口 | 用途 | 鉴权 |
|---|---|---|
| `:18789` | 网关 API(OpenAI 兼容) | Bearer token |
| `:18790` | Web UI + CLI 后端 | Cookie 密码 |
| `:18800` | noVNC 远程桌面 | — |

### 登录与密码(30 秒搞懂)

- **Web UI**:浏览器开 `http://<你的机器>:18790`,输一个密码就进(密码在 `config.json → web_ui.auth.password`,启动前改成你自己的)。
- **API / 第三方调用**:请求头带 `Authorization: Bearer <token>`(token 在 `config.json → server.token`)。
- **CLI**:`litecli login` 一次,cookie 自动存本地;交互 REPL 直接 `litecli repl`。
- 就这三样,没有第四样。**没有注册、没有多租户绕晕你的 RBAC** —— 这是你一个人的机器。

### 第三方接入 —— 给你现有的 AI 客户端加 Buff

LiteCode 的网关是标准 OpenAI 协议,所以**任何支持自定义 base_url 的客户端**(LangChain / OpenAI SDK / Dify / Open WebUI / 沉浸式翻译 / 各类 ChatBox)把地址一改,就等于**给它们背后的大模型开挂**:

> 同一个模型,过一遍 LiteCode,自动带上 **工具调用 + 多层搜索 + 持久记忆 + 92 项技能 + 视觉路由**。你的翻译插件突然会查资料了,你的聊天客户端突然记得你是谁了。

```
base_url = http://<你的机器>:18789/v1     api_key = <server.token>
user 字段传固定值 = 会话 ID → 记忆跨请求延续
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:18789/v1", api_key="YOUR_TOKEN")
r = client.chat.completions.create(
    model="your-model", stream=True, user="my_session",
    messages=[{"role": "user", "content": "扫描这个仓库并写份报告"}],
)
for c in r:
    print(c.choices[0].delta.content or "", end="", flush=True)
```

---

## 架构

```
   微信 / 企微 bot ┐
   CLI (REPL)      ├──▶  网关 :18789  (OpenAI 兼容 SSE —— agent 主循环)
   浏览器 ─────────┘            ▲
                              │ 同源代理
                  Web UI :18790  (DAG · 定时 · 记忆 · 模型/插件/容器管理面)
```

Web 是指挥部,CLI 是贴身刀,微信/企微是随身终端 —— 底下同一个大脑。深入:[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

<div align="center">

## ⭐ 认真的,给个 star

LiteCode 由一个小团队在开源里一锤一锤打磨。每颗 star 都是氧气 —— 下一个不想把 AI 生活租给云厂商的人,就是靠它找到这里的。

**[⭐ Star 这个仓库](https://github.com/wfcz10086/litecode)** · Watch 关注版本 · 提 issue 告诉我们你想造什么

</div>

## 许可证

MIT
