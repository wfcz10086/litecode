<div align="center">

# ⚔️ LiteCode

**中文** · [English](README.en.md)

**在你自己的电脑上跑一个全能 AI 助手。** 网页上能聊,终端里能用,微信里也能找它干活。模型接你自己的,数据全在你自己盘上,不用交钱给任何云。

![MIT](https://img.shields.io/badge/license-MIT-green) ![Docker](https://img.shields.io/badge/docker-%E5%8D%95%E5%AE%B9%E5%99%A8-blue) ![OpenAI](https://img.shields.io/badge/API-OpenAI%E5%85%BC%E5%AE%B9-orange) ![Private](https://img.shields.io/badge/%E7%A7%81%E6%9C%89%E5%8C%96-100%25-red) ![Bots](https://img.shields.io/badge/%E5%BE%AE%E4%BF%A1%2F%E4%BC%81%E5%BE%AE-%E5%86%85%E7%BD%AE-brightgreen)

![LiteCode Web UI](docs/screenshots/01_overview.jpg)

⭐ 觉得好用的话,顺手点个 star,就是对我们最大的支持。

</div>

---

## 一、怎么跑起来(照抄就行)

你需要:一台 Linux 机器(家里服务器、云主机、旧电脑都行)+ 装好 Docker。

**第 1 步:把代码下下来**

```bash
git clone https://github.com/wfcz10086/litecode && cd litecode
```

**第 2 步:告诉它你的模型在哪**

```bash
cp config.example.json config.json
```

然后打开 `config.json`,找到 `model` 那一段,填两个东西:

- `backend_url`:你的模型地址。自己搭的 vLLM、Ollama,或者买的中转 API 都行,只要是 OpenAI 格式的接口(就是地址长得像 `http://xxx:8000/v1` 那种)。
- `api_key`:模型的 key。自己搭的没设 key 就填 `EMPTY`。

没有模型?随便找个卖 OpenAI 兼容 API 的中转站,把地址和 key 填进来就能用。

**第 3 步:启动**

```bash
./start.sh
```

第一次会自动构建镜像,慢一点,等它跑完。之后再启动就是几秒钟的事。

**第 4 步:打开浏览器**

访问 `http://你机器的IP:18790`,看到登录页就成功了。

---

## 二、怎么登录

一共就三个地方要认证,都很简单:

| 在哪 | 怎么进 | 密码在哪改 |
|---|---|---|
| **网页**(:18790) | 输一个密码 | `config.json` 里的 `web_ui.auth.password`,把 `CHANGE_ME_PASSWORD` 改成你自己的 |
| **API**(:18789) | 请求头带 `Authorization: Bearer 你的token` | `config.json` 里的 `server.token`,把 `CHANGE_ME_TOKEN` 改成你自己的 |
| **命令行** | 先 `litecli login` 输一次密码,之后就不用管了 | 跟网页共用一个密码 |

没有注册、没有多账号那套复杂东西。这是你一个人的机器,一个密码、一个 token,完事。

> 提醒:启动前记得把上面两个 `CHANGE_ME` 改掉,别用默认值裸奔公网。

---

## 三、它能帮你干什么

装好第一天就能用的:

- 📄 微信里丢个 PDF 给它,手机上直接收到总结。
- 🗣️ 跟它说"扫描这个项目,写份架构报告",它自己拆成三步(扫描 → 分析 → 写),像流水线一样跑完给你。
- ⏰ 设个定时任务,每天早上 9 点自动查数据、发一份简报到你的企业微信。
- 🖼️ 贴张截图它能看懂;要 PPT 它现场做;要配图,画图接口(DALL·E / SD / ComfyUI)都接好了。
- ⌨️ 终端里一句话,它写文件、跑命令、给你看改了哪几行,最后连花了多少 token 都告诉你。

---

## 四、截图

<table>
<tr>
<td align="center" width="33%"><b>🏗️ DAG 编辑器,像 Jenkins 一样跑 AI 流水线</b><br><img src="docs/screenshots/03_dag_editor.jpg" width="100%"></td>
<td align="center" width="33%"><b>⌨️ 命令行:一句话,写码跑码全搞定</b><br><img src="docs/screenshots/02_cli.jpg" width="100%"></td>
<td align="center" width="33%"><b>📱 微信接入,扫码就能用</b><br><img src="docs/screenshots/04_wechat.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>🤖 企业微信机器人,真流式回复</b><br><img src="docs/screenshots/05_wecom.jpg" width="100%"></td>
<td align="center"><b>🧠 记忆面板,聊过的它都记得</b><br><img src="docs/screenshots/06_memory.jpg" width="100%"></td>
<td align="center"><b>🔌 模型管理,随时热切换</b><br><img src="docs/screenshots/07_models.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>⏰ 定时任务</b><br><img src="docs/screenshots/08_timer.jpg" width="100%"></td>
<td align="center"><b>📦 产出物仓库</b><br><img src="docs/screenshots/09_artifacts.jpg" width="100%"></td>
<td align="center"><b>📊 用量和成本,花了多少一目了然</b><br><img src="docs/screenshots/10_stats.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>🐳 聊天里就能管 Docker</b><br><img src="docs/screenshots/11_docker.jpg" width="100%"></td>
<td align="center"><b>🗂️ 项目管理</b><br><img src="docs/screenshots/12_projects.jpg" width="100%"></td>
<td align="center"><b>⌨️ CLI 全部命令</b><br><img src="docs/screenshots/14_cli_cmds.jpg" width="100%"></td>
</tr>
</table>

---

## 五、好,下面开始吹

### 先甩数字

| 72 | 92 | 9 | 14 | 5 | 4 | 3 | 1 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| 个工具 | 项技能 | 种 agent | 个搜索引擎 | 种模型后端 | 级记忆 | 个入口 | 个容器 |

不用集群,没有月费,数据一个字节都不出你的机器。

### 🧠 记性是真的好

一般的 AI 聊久了就断片,上下文一满,前面说的全忘。LiteCode 的记忆是分四层的:近期对话、常驻档案、分类笔记、全库检索,聊到快满时它自己在后台压缩,压完接着聊。你说过的偏好(比如"回答用中文")它自动记下来,新开会话它还认识你。就算机器断电重启,聊天记录也丢不了。

说白了:别的 AI 是金鱼,这个是带档案柜的老管家。

### 📱 微信/企业微信是真能用

不是网页套壳。扫码把你的微信接上,发文字、发图、发语音、发文件它都接得住;企业微信那边更爽,回复是真·流式的,一个字一个字往外蹦。背后跟网页、命令行是同一个大脑,微信里聊的,回到网页上还能接着聊。

### 🏗️ 流水线像 Jenkins 一样跑

复杂活别一句话怼给 AI,拆成流水线:拖几个节点(写码的、分析的、看图的、跑命令的),连上线,点 Build。它有断点、能续跑、失败自己反思重试,跑完的记录一条条留着。最爽的是——你可以直接说"帮我建一条 xx 流水线",它自己搭。

### 🧩 想加功能?丢个文件进去

生图已经内置(DALL·E / SD / ComfyUI)。想要视频生成、音乐生成?照着现成的样子写一个 py 文件丢进 `plugins/` 就行,系统自动认,不用改核心代码。外部项目(比如 PPT 生成器)也是挂载进来的,拔掉环境变量就干净卸载。

### 🔍 搜索封不死

免费 API → 本机 SearXNG 聚合 → 14 个引擎并行 → 实在不行开真浏览器去爬。一层被封还有下一层,而且 `deep_search` 会点进搜索结果把正文读了再回答,不是拿标题糊弄你。

### 🎁 还有些别人没有的

- 💞 **陪伴模式**:你说"今天好累",它不会教你早点睡,它就陪着你聊。emo 的时候自动切进来,不说教、不灌鸡汤。
- 🎭 **权谋推演**:一键开"谁在获利"视角,看新闻、看行情不再被表面叙事带节奏。
- ✍️ **21 种题材的小说流水线**:玄幻修真武侠都市科幻言情宫斗系统流……每种一个专门技能,还带"去 AI 味"审计,写出来不像机器写的。
- 🖥️ **对话操控桌面**:让它在远程桌面里开浏览器、点按钮、截图发回来给你看。
- 📄 **办公全家桶**:Word / Excel / PPT / PDF 读写生成,微信丢文件进去,处理完还你文件。

### 🔗 顺手升级你现在用的 AI 工具

LiteCode 的接口是标准 OpenAI 格式。你现在用的任何 AI 客户端(翻译插件、ChatBox、Dify、LangChain……),把地址改成 `http://你的机器:18789/v1`,它们背后的模型立刻多了工具调用、搜索、记忆和 92 项技能。等于免费开挂。

---

## 六、跟同类比一下

| | **LiteCode** | Dify | LangChain | Open WebUI |
|---|:---:|:---:|:---:|:---:|
| 单容器自托管 | ✅ | ✅ | 是个库 | ✅ |
| OpenAI 兼容网关 | ✅ | 部分 | — | 仅对话 |
| 网页 + 命令行 + 聊天机器人同一个大脑 | ✅ | 仅网页 | 写代码 | 仅网页 |
| 内置微信 / 企业微信机器人 | ✅ | — | — | — |
| 可视化 DAG 流水线 | ✅ | flow | 写代码 | — |
| 生图 / 视频 / 音乐插件位 | ✅ | 工具 | 工具 | — |
| 对话操控桌面 | ✅ | — | — | — |
| 四级持久记忆 | ✅ | 部分 | — | 基础 |

_不是打分表,人家各有各的强项。LiteCode 赌的是:一台自己的机器,全都要。_

---

## 七、架构(一张图)

```
   微信 / 企微机器人 ┐
   命令行 CLI        ├──▶  网关 :18789  (OpenAI 兼容, AI 大脑在这)
   浏览器 ──────────┘            ▲
                               │
                   Web UI :18790  (流水线 · 定时 · 记忆 · 各种管理面板)
```

想深入了解看 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

<div align="center">

## ⭐ 最后

这项目是小团队一锤一锤敲出来的。要是它帮你省了事,点个 star 呗——你花 3 秒,我们能高兴一天。

**[⭐ 点这里](https://github.com/wfcz10086/litecode)** · 有想法就提 issue,我们真的看

</div>

## 许可证

MIT
