<div align="center">

# ⚔️ LiteCode

**中文** · [English](README.en.md)

**装在你自己电脑或服务器上的 AI 助手，会自己动手把活干完。**

你说一句话，它自己拆任务、跑命令、写代码、查资料、做文档和视频，做完把能打开的成品交给你。
网页、命令行、微信、企业微信用的是同一个助手。模型随你接（任意 OpenAI 兼容接口，包括你自己部署的），开源免费，数据留在你自己的机器上。

[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Release](https://img.shields.io/github/v/release/wfcz10086/litecode?label=%E4%B8%8B%E8%BD%BD)](https://github.com/wfcz10086/litecode/releases/latest)
![Windows](https://img.shields.io/badge/Windows-%E5%8F%8C%E5%87%BB%E5%AE%89%E8%A3%85-0078D6)
![Docker](https://img.shields.io/badge/Linux-Docker-blue)

[下载](https://github.com/wfcz10086/litecode/releases/latest) · [安装](#安装) · [第一次使用](#第一次使用) · [能做什么](#能做什么) · [常见问题](#常见问题) · [更新日志](CHANGELOG.md)

<img src="docs/screenshots/v2/01_chat_artifact.jpg" alt="LiteCode 对话界面" width="820">

</div>

## 安装

先选一种装法：

| 你的情况 | 选哪个 | 要多久 |
|---|---|---|
| 一台 Windows 10/11 电脑，只想赶紧用上 | **[方式 A：Windows 安装包](#方式-awindows-安装包推荐)** | 下载 + 约 10 分钟 |
| 有一台 Linux 服务器（云主机、家里的机器），想 24 小时开着 | **[方式 B：Linux + Docker](#方式-blinux--docker)** | 约 20 分钟（第一次要构建镜像） |
| Windows 电脑，但想跑和服务器一样的 Docker 版 | **[方式 C：Windows 里的 WSL + Docker](#方式-cwindows-里的-wsl--docker)** | 约 30 分钟 |

不管哪种方式，都需要一个**大模型接口**：OpenAI 兼容的地址 + 模型名（+ API Key）。自己用 vLLM / Ollama 部署的、买的 API 都行，见[接上模型](#接上模型)。

---

### 方式 A：Windows 安装包（推荐）

**准备**

- Windows 10 2004（19041）及以上，或 Windows 11，64 位。
- 磁盘留 **15 GB** 空闲。
- BIOS 里开着虚拟化（大多数电脑默认开着；任务管理器 →「性能」→「CPU」里看到「虚拟化：已启用」就行）。
- 安装用的 Windows 账户是管理员。

**步骤**

1. 打开 [Releases 下载页](https://github.com/wfcz10086/litecode/releases/latest)，下载 `LiteCode-Setup-<版本号>.exe`（约 1.9 GB，**离线安装包**，装的时候不用联网）。
2. 双击运行。Windows 弹出「已保护你的电脑」时，点「更多信息」→「仍要运行」（安装包没有购买代码签名证书）。
3. 一路「下一步」。中间会让你填三项：
   - **模型地址**：例如 `http://192.168.1.10:8000/v1`
   - **模型名称**：例如 `Qwen3.6-35B-A3B`
   - **API Key**：没有就留空
4. 电脑**从没开过 WSL** 的话，安装包会请求一次管理员权限去开启，然后提示重启。重启后它会自己接着装，不用再点。
5. 最后一步「接入本机操控」会再请求一次管理员权限：安装 OpenSSH 并设为开机自启，再装一个桌面代理，让 LiteCode 能在这台电脑上开软件、截图、点按钮。不想要就点「否」，以后托盘菜单里随时能补。
6. 装完桌面会出现 **LiteCode** 图标，右下角托盘常驻，开机自启。双击图标打开窗口。

**登录**：窗口地址是 `http://127.0.0.1:18790`，**只有这台电脑自己能访问**。默认密码 `admin`，进去后到「设置 → 修改密码」改掉。

**更新**：托盘每天检查一次。有新版会弹窗问你，点「是」就自动下载、校验、替换，大约 1 分钟，对话和设置都保留；新版起不来会自动回滚。代码更新优先从 GitHub 下载，GitHub 连不上时换备用源。手动检查：托盘右键 →「检查更新」。

**卸载**：「设置 → 应用」里找到 LiteCode 卸载。⚠️ 卸载会删掉 LiteCode 子系统，里面的对话和文件一起删掉，有要留的先导出。

<details>
<summary><b>安装包装了什么？WSL 是什么？</b></summary>

LiteCode 本体是 Linux 程序。Windows 版没有让你装 Docker Desktop，而是用 Windows 自带的 **WSL**（Windows 里跑 Linux 的官方功能）：

- 安装包里有一个打包好的 Linux 系统（根文件系统），安装时导入成一个名叫 **`LiteCode`** 的 WSL 子系统，所有程序、依赖、浏览器都在里面，跟你电脑上别的 Linux 环境互不干扰。
- 托盘程序 `LiteCode.exe` 负责启动、停止、更新这个子系统，以及弹出窗口。
- 数据目录：`%LOCALAPPDATA%\LiteCode`（子系统磁盘、日志、下载缓存都在这）。
- 本地配音和知识库用的模型包（约 900 MB）不在安装包里，装好后托盘在后台断点续传下载。

在 cmd 里可以这样看它：

```
wsl -l -v
```

能看到 `LiteCode   Running   2` 就说明正在运行。进去看看：

```
wsl -d LiteCode
```

彻底停掉（托盘会在需要时再拉起）：

```
wsl --terminate LiteCode
```

</details>

<details>
<summary><b>安装出错了怎么办</b></summary>

先看日志：`%LOCALAPPDATA%\LiteCode\launcher.log`（把这个路径粘到资源管理器地址栏回车就能打开）。下面是踩过的坑：

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `读默认配置失败: invalid character 'w'` | 你开着本机代理（Clash 之类），WSL 输出了一段代理警告，混进了安装程序读的结果 | 新安装包已修复。用旧包时：在 cmd 里依次执行下面三条，再重新运行安装包 |
| 提示要开启虚拟化 / WSL 起不来 | BIOS 里没开虚拟化 | 重启进 BIOS，打开 Intel VT-x 或 AMD SVM |
| 卡在「接入本机操控」很久 | 首次要准备 OpenSSH 和 Python 依赖 | 新安装包已自带，不再联网下载；失败也不影响主程序，之后托盘里「重新接入本机操控」 |
| `未能为 SSL/TLS 安全通道建立信任关系` | 系统证书库太旧 | 运行一次 Windows 更新再试 |
| 模型报 `Illegal header value b'Bearer '` | 老版本 API Key 留空的问题 | 新版已修复（留空自动当成 `EMPTY`） |

开着本机代理时给 WSL 打开镜像网络（cmd 里一条一条执行）：

```
echo [wsl2]> "%USERPROFILE%\.wslconfig"
```

```
echo networkingMode=mirrored>> "%USERPROFILE%\.wslconfig"
```

```
wsl --shutdown
```

更多原理和排障：[docs/DESKTOP_WINDOWS.md](docs/DESKTOP_WINDOWS.md)。

</details>

<details>
<summary><b>批量部署 / 无人值守安装</b></summary>

```
LiteCode-Setup-<版本>.exe /S
LiteCode.exe --setup --unattended --password <密码> --model-url <地址> --model-name <模型> --model-key <key> --no-tray
LiteCode.exe --selftest
LiteCode.exe --local-control
LiteCode.exe --setup --no-local-control
```

依次是：静默解包；无人值守配置；把环境检查结果写到 `selftest.txt`；只做本机操控接入；安装时不接入本机操控。退出码 `3010` = 需要重启（已登记重启后自动继续），`2` = 失败。

</details>

---

### 方式 B：Linux + Docker

适合一直开着的服务器：关掉自己的电脑，定时任务和微信机器人照样跑。

**准备**：64 位 Linux（Ubuntu 22.04 / 24.04 测得最多），4 核 8 GB 内存以上，磁盘留 30 GB。

**1. 装 Docker**（已经有 Docker 和 `docker compose` 的跳过）

```bash
curl -fsSL https://get.docker.com | sudo sh -s -- --mirror Aliyun
sudo usermod -aG docker $USER      # 之后退出重新登录一次, 就不用每条命令都带 sudo
docker compose version             # 能打印版本号就说明装好了
```

**2. 下载代码**

```bash
git clone https://github.com/wfcz10086/litecode.git
cd litecode
```

GitHub 慢的话，到 Releases 下载 `Source code (tar.gz)` 再解压也一样。

**3. 第一次启动**

```bash
./start.sh
```

第一次会做三件事：用 `config.example.json` 生成 `config.json`；给搜索引擎 SearXNG 生成随机密钥；构建镜像 `litecode:latest`，同时打上版本号标签（如 `litecode:v2.0.13`），约 **10~15 分钟**，已经换成国内源。

**4. 改配置**（必须）

```bash
nano config.json
```

至少改这四处（示例里都是占位的 `CHANGE_ME`）：

| 位置 | 改成什么 |
|---|---|
| `model.backend_url` | 你的模型接口，形如 `http://x.x.x.x:8000/v1` |
| `model.api_key` | 模型的 key，没有就写 `EMPTY` |
| `web_ui.auth.password` | 网页登录密码（把 `CHANGE_ME` 换掉） |
| `server.token` | API 网关口令（把 `CHANGE_ME` 换成一串随机字符，可用 `openssl rand -hex 16` 生成） |

改完让配置生效：

```bash
./start.sh --restart
```

**5. 打开**

浏览器访问 `http://服务器IP:18790`，看到登录页就成功了。打不开先在服务器上自查：

```bash
./start.sh --status
curl -s http://127.0.0.1:18789/health
```

本机能通、外面打不开，就是防火墙或云主机安全组没放行 `18790` 端口。

> ⚠️ 放到公网前一定改掉默认密码和 token；最好再套一层 HTTPS（nginx / Caddy 反向代理）。

**常用命令**

| 命令 | 作用 |
|---|---|
| `./start.sh --logs` | 看实时日志 |
| `./start.sh --restart` | 改完配置后重启 |
| `./start.sh --down` | 停止 |
| `./start.sh --build` | 依赖变了时重建镜像 |
| `./start.sh --shell` | 进到容器里 |
| `git pull && ./start.sh --restart` | 升级到最新版 |

**可选组件**

| 组件 | 怎么开 | 说明 |
|---|---|---|
| SearXNG 搜索 | 默认已随 compose 启动 | 开源元搜索，只监听本机 `127.0.0.1:18888` |
| 知识库 | `bash scripts/kb_setup.sh` | 起 PostgreSQL + pgvector（只监听本机、随机密码），自动写好配置；详见 [docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md) |
| 命令行 `litecli` | `sudo ln -sf $(pwd)/litecodeext/litecli /usr/local/bin/litecli` | 然后 `litecli login`，再输 `litecli` 进入对话 |

---

### 方式 C：Windows 里的 WSL + Docker

想在 Windows 上跑和服务器**完全一样**的 Docker 版（比如要自己改代码），就在 WSL 的 Ubuntu 里走方式 B。

1. 用**管理员**身份打开 cmd，装一个 Ubuntu（装完按提示重启，再设置 Linux 用户名和密码）：

   ```
   wsl --install -d Ubuntu-24.04
   ```

2. 开始菜单里打开「Ubuntu 24.04」，接下来的命令都在这个窗口里执行。
3. 按[方式 B](#方式-blinux--docker) 的第 1~4 步装 Docker、下载代码、启动、改配置。
4. Windows 浏览器打开 `http://localhost:18790`。

> 方式 A 和方式 C 可以同时装，但两者都用 18790 端口，不要同时运行。

---

## 接上模型

LiteCode 本身不带大模型，它用你提供的模型接口干活。

- **地址**：OpenAI 格式，以 `/v1` 结尾。自己部署的 vLLM / SGLang / Ollama（`http://IP:11434/v1`）、各家云 API、中转都可以。
- **模型名**：和接口里的名字一字不差。
- **Key**：没有就填 `EMPTY`。

不想改文件也行：登录后直接在对话里说「帮我加一个模型，地址是…，名字是…」，它会自己添加、测试连通再切过去。「设置 → 模型」里每个模型都有「测试」按钮。

> 工具调用越稳的模型效果越好。GLM-5.3-Flash、Qwen3.6 / 3.8、DeepSeek-V4 系列都实测能稳定跑完长任务；太小的模型容易绕圈。主模型不能看图时，截图核对和 OCR 会转给你配好的识图模型。

## 第一次使用

登录后，在输入框里试试这几句：

```text
帮我写一个 Python 命令行记账工具，带测试，跑通给我
```

```text
查一下今天的 A 股大盘，用表格总结，生成一份 Word 报告
```

```text
每天早上 9 点查一下 BTC 价格发到我微信
```

它会边做边显示每一步（调用了什么工具、花了多久，点开能看细节）。做完的文件在回复里直接预览、下载。跑的过程中可以随时插话纠正：回车是「补充」，点「⚡ 立即」是「打断并调整」。

**其他入口**

| 入口 | 怎么用 |
|---|---|
| 微信 | 工具箱 →「微信」→ 扫码绑定。注意一个微信号同一时间只能绑一个 LiteCode，最后扫码的生效 |
| 企业微信 | 工具箱 →「企业微信」，填机器人信息 |
| 命令行 | `litecli`：改代码前给你看 diff，统计 token 用量 |
| API | `http://地址:18789/v1` 是标准 OpenAI Chat Completions 接口，请求头带 `Authorization: Bearer <server.token>`。ChatBox、Dify、LangChain 等任何客户端接上，背后就有了工具、搜索、记忆和全部技能 |
| 操控另一台 Windows | 工具箱 →「Windows」→「添加」生成一条命令，在那台电脑的管理员终端粘贴执行。会自动装 OpenSSH、生成并授权专用密钥（只用密钥、不用密码），由那台电脑主动连回来，不用开端口 |

## 能做什么

<table>
<tr>
<td width="50%" valign="top">

**💻 写代码**
读仓库、改代码、跑测试，验证通过才交付；几个子代理分别在独立的工作副本里并行写，先审再合并。

**🔍 查资料**
SearXNG 元搜索为主，多引擎并行、真浏览器兜底；会点进网页读正文，回答带出处。另有 GitHub、包管理、Stack Overflow 搜索和 A 股 / 港股 / 美股实时行情。

**📄 做文档**
Word、Excel、PPT、PDF 的生成、读取和修改；5000~20000 字的深度报告；扫描件 OCR。

**🎬 做音视频**
本地纯 CPU 配音；文案→配音→字幕→画面→配乐一条龙出片；抖音链接解析、无水印下载、文案提取和二创；接 ComfyUI 生图生视频。

</td>
<td width="50%" valign="top">

**🖥️ 操控电脑**
在 Windows 上开软件、截图、OCR、按控件名点击、输入、跑 PowerShell、传文件；锁屏或断线时会说明原因和解决办法。

**🛠️ 管服务器**
SSH 执行命令、读写和同步文件，查看和重建远程 Docker 容器。

**⏰ 自动化**
定时任务（服务器版关了电脑也照跑）；可视化 DAG 流水线，支持条件分支、子流水线和断点续跑。

**🧠 记忆和知识库**
记得你的偏好，纠正一次就记住；上传文档建知识库，检索结果带出处；记忆随时可查可删。

</td>
</tr>
</table>

另外：110 个内置技能（输入框打 `/` 选择，可以从技能市场装更多）、用量看板、只读分享链接、每轮自动录屏、防提示注入（网页和搜索结果里的“指令”一律不执行，敏感操作要你确认）。

<details>
<summary><b>截图</b></summary>

**每一步做了什么、用了多久，展开就能看**

![调用链路](docs/screenshots/v2/02_chat_steps.jpg)

**两个子代理并行写 Python 和 Go 版本，主代理复核后汇总**

![子代理](docs/screenshots/v2/03_subagents.jpg)

<table>
<tr>
<td align="center" width="50%"><b>选技能</b><br><img src="docs/screenshots/v2/04_skill_picker.jpg" width="100%"></td>
<td align="center" width="50%"><b>技能管理和技能市场</b><br><img src="docs/screenshots/v2/05_skills.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>用量看板</b><br><img src="docs/screenshots/v2/06_dashboard.jpg" width="100%"></td>
<td align="center"><b>设置</b><br><img src="docs/screenshots/v2/07_settings.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>工具箱</b><br><img src="docs/screenshots/v2/08_toolbox.jpg" width="100%"></td>
<td align="center"><b>DAG 流水线</b><br><img src="docs/screenshots/v2/09_dag.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>Windows 托盘一键更新</b><br><img src="docs/screenshots/v2/11_win_update.jpg" width="100%"></td>
<td align="center"><b>微信接入</b><br><img src="docs/screenshots/04_wechat.jpg" width="100%"></td>
</tr>
</table>

**一句话做抖音二创**（成片抽帧，画面全部由 AI 生成）

![抖音二创](docs/screenshots/v2/12_douyin_remake.jpg)

</details>

## 和同类工具比

> 整理于 2026-10，对方信息来自官方仓库和公开资料，可能随版本变化。

| | LiteCode | OpenClaw | DeepSeek-TUI（Codewhale） | 腾讯 WorkBuddy |
|---|---|---|---|---|
| 是什么 | 自托管的全能助手：网页 + 命令行 + 机器人 + API | 自托管个人助手，在聊天软件里用 | 终端里的编程助手 | 商业桌面 App + 云服务 |
| 开源 / 收费 | MIT，免费 | MIT，免费 | MIT，免费 | 闭源，按积分收费 |
| 模型 | 任意 OpenAI 兼容，含自建 | 多家云模型 + Ollama | 40+ 家，默认 DeepSeek | 指定的几家 |
| Windows | 离线安装包，托盘 + 自动更新 | 要 WSL2 + Node | 单个可执行文件 | 安装包 |
| 微信 / 企业微信 | ✅ 内置 | 以海外渠道为主 | ❌ | 企微 / QQ / 飞书 / 钉钉 |
| 操控 Windows 桌面 | ✅ | 在主机上执行 | 仅 macOS，实验 | 仅 macOS，实验 |
| 可视化流水线 | ✅ DAG | ❌ | ❌ | ❌ |
| 定时任务关机照跑 | ✅（服务器版） | ✅ | ❌ | ❌ |
| 手机 App | ❌（网页手机可用） | ✅ | ❌ | ✅ |
| macOS 原生 | ❌（走 Docker） | ✅ | ✅ | ✅ |
| 系统级沙箱 | ❌（靠确认 + 权限开关） | 可配置 | ✅ | ✅ |
| 社区规模 | 小 | 最大 | 大 | 用户量大 |

**选 LiteCode 的理由**：在国内开箱即用（微信、国内源、国内搜索），有完整网页工作台，能操控 Windows 和管服务器，模型和数据都在自己手里。
**不选的理由**：要手机 App、要 macOS 原生、要系统级沙箱，或者看重社区规模。

## 常见问题

**没有显卡能用吗？**
能。模型用 API 就行；本地配音和知识库的向量模型都是纯 CPU。

**数据会上传吗？**
不会主动上传。对话、记忆、文件都在你的机器上；只有你配置的模型接口，以及你让它搜索、访问的网站会收到请求。没经你明确同意，它不会把文件传到公网。

**桌面版和服务器版选哪个？**
自己一台电脑用，选桌面版。要 24 小时在线（定时任务、微信机器人），选服务器版。两个都装也可以。

**能用 Claude / GPT 吗？**
能，只要是 OpenAI 兼容接口，或者经兼容网关转发。

**锁屏了还能操控电脑吗？**
文件、进程、装软件这类命令行操作可以。要看屏幕的操作，锁屏时会告诉你原因和解决办法。

**怎么自己编译 / 打 Windows 安装包？**
见 [docs/BUILD.md](docs/BUILD.md)。

## 项目结构

```
 微信 / 企微 ┐
 命令行      ├──▶ 网关 :18789（OpenAI 兼容；智能体循环、工具、记忆）
 浏览器 ─────┘           ▲
                  网页 :18790（对话、产物、看板、流水线、设置）
                         │
      SearXNG · 知识库 · Windows 节点 · SSH 主机 · ComfyUI
```

| 目录 | 内容 |
|---|---|
| `litecodeext/` | 主程序：网关 `litecode_server.py`、网页 `web_ui.py`、`lib/` 核心库、`plugins/` 工具插件、`skills/` 技能、`web_assets/` 前端 |
| `packaging/` | Windows 桌面版（Go 托盘 + NSIS 安装包 + WSL 系统包）和发布脚本 |
| `docs/` | 架构、桌面版、知识库、编译说明 |

工具是插件（一个文件），技能是一个 `SKILL.md` 加脚本，都可以自己加。架构细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 最新版本

### v2.0.14 「上桌」 (2026-10-23)

- 🎵 **AI 写歌** `ai-song`：写歌词 → 自建开源 ACE-Step 生成带人声的歌 → 语音识别核对唱词；古风、DJ、民谣、说唱都行。
- 🎬 **一句话做 MV / 配音短片** `anime-short`：先听歌拿每句唱词的时间点 → 歌词映射到场景和画面 → 大模型优化提示词 → Qwen-Image 出首帧（可用你给的人物照片当定妆照）→ MiniMax H3 跟着音乐生成视频（AniSora 并行做伴奏段）→ 识图逐段审阅、不合格自动重写提示词重做 → 原歌词字幕、音画对齐。默认真人写实、整首歌。
- 🎤 **自建开源服务**：语音识别 SenseVoice、配音 IndexTTS 2.5（情绪、克隆）、写歌 ACE-Step 1.5、动漫视频 AniSora；部署文件和二次部署文档见 [docs/GPU_SERVICES.md](docs/GPU_SERVICES.md)。
- ⏳ **后台进行中卡片**：长任务放后台后，对话底部实时显示进度、可展开日志、可停止；长任务不再堵住对话。
- 🧭 **修复**：长任务进行中点「新对话」卡住；会话列表加载提速；重启前会等后台长任务跑完。
- 🔌 **能力接入极简版**：一张卡 = 标题 + 一段明文（大白话 + curl）；系统提示常驻"已接入能力"清单，配音 / 音乐 / 视频优先用接入的服务。
- 🧩 **定制安装包**：安装包可带预置配置（模型、识图模型、能力接入），装完即用。
- 🛠️ 修复：多行 curl 解析、服务端丢任务时不再死等、新加技能不重启也能发现、配音走默认配音卡、远程桌面刷爆日志。

### v2.0.13 「上桌」 (2026-10-23)

- 🔌 **能力接入**：左上角新入口，统一接入生图、视频、语音合成/识别、音乐、搜索、通知等外部服务；左侧列表、右侧明文用法，一键测试、设默认；输入框打 `@` 引用某个服务，完整明文随消息带给 LiteCode。预设 29 个常用服务模板。
- 🧭 **决策思考技能 11 个**：事前验尸、决策矩阵、红队反驳、二阶后果、概率校准、偏差检查、复盘、优先级分诊等。
- 🌐 **百万字长文翻译** `long-translate`：术语表、并发、断点续跑、保留 Word/Markdown/EPUB/字幕格式、质检报告。
- 📐 **画图技能** `diagram`：Mermaid / Graphviz 本地渲染流程图、架构图、时序图，PlantUML 按需安装；PPT 和文档里的示意图都用它。
- 👁️ **识图场景模式**：表格、UI 原型、参考图反推生图提示词、图表读数、报错截图诊断；统一"如实识别"规则。
- 📖 **README 重写**：三种装法（Windows 安装包 / Linux Docker / WSL + Docker）按步骤写清楚，附排障表；版本历史挪到 [CHANGELOG.md](CHANGELOG.md)；补上 MIT 许可证文件和编译说明 [docs/BUILD.md](docs/BUILD.md)。
- 🏷️ **镜像版本号跟着发版走**：Docker 镜像改为 `litecode:latest` + `litecode:<版本号>`，不再固定叫 `v12-r1`；老镜像启动时自动改名接上，不用重建。
- 🪟 **完整安装包更新**：托盘代码更新优先走 GitHub；启动横幅不再显示错误的默认密码。

**v2.0.12**：查完网页即关浏览器；桌面版没有 Docker 时给出中文提示；托盘代码更新优先走 GitHub。

全部版本见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

[MIT](LICENSE)。觉得有用就点个 ⭐；有问题欢迎提 [issue](https://github.com/wfcz10086/litecode/issues)。
