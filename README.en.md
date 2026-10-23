<div align="center">

# ⚔️ LiteCode

[中文](README.md) · **English**

**A self-hosted AI assistant that gets the work done on your own computer or server.**

Describe a task and it plans the steps, runs commands, writes code, searches the web, and produces documents or videos, then hands you files you can open.
The web app, the command line, WeChat and WeCom all talk to the same assistant. Bring any OpenAI-compatible model (including ones you host yourself). Free and open source, and your data stays on your machine.

[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Release](https://img.shields.io/github/v/release/wfcz10086/litecode?label=download)](https://github.com/wfcz10086/litecode/releases/latest)
![Windows](https://img.shields.io/badge/Windows-installer-0078D6)
![Docker](https://img.shields.io/badge/Linux-Docker-blue)

[Download](https://github.com/wfcz10086/litecode/releases/latest) · [Install](#install) · [First steps](#first-steps) · [What it does](#what-it-does) · [FAQ](#faq) · [Changelog](CHANGELOG.en.md)

<img src="docs/screenshots/v2/01_chat_artifact.jpg" alt="LiteCode chat" width="820">

</div>

## Install

Pick one:

| Your situation | Choose | Time |
|---|---|---|
| A Windows 10/11 PC and you just want it working | **[A: Windows installer](#a-windows-installer-recommended)** | download + ~10 min |
| A Linux server (cloud or home) that stays on 24/7 | **[B: Linux + Docker](#b-linux--docker)** | ~20 min (first image build) |
| Windows, but you want the same Docker setup as a server | **[C: Docker inside WSL](#c-docker-inside-wsl)** | ~30 min |

Every option needs a **model endpoint**: an OpenAI-compatible URL and model name (plus an API key if it has one). Self-hosted vLLM / Ollama or a paid API both work, see [Connect a model](#connect-a-model).

---

### A: Windows installer (recommended)

**Requirements**

- Windows 10 2004 (build 19041) or later, or Windows 11, 64-bit.
- **15 GB** of free disk space.
- Virtualization enabled in the BIOS (on by default on most PCs; Task Manager → Performance → CPU should say "Virtualization: Enabled").
- An administrator Windows account.

**Steps**

1. Download `LiteCode-Setup-<version>.exe` (about 1.9 GB) from the [Releases page](https://github.com/wfcz10086/litecode/releases/latest). It is an **offline installer**; installing needs no network.
2. Run it. If Windows shows "Windows protected your PC", click "More info" → "Run anyway" (the installer is not code-signed).
3. Click through. It asks for three things:
   - **Model URL**, e.g. `http://192.168.1.10:8000/v1`
   - **Model name**, e.g. `Qwen3.6-35B-A3B`
   - **API key**, leave empty if there is none
4. If WSL has **never been enabled** on this PC, the installer asks for admin rights once to turn it on and then asks you to reboot. After the reboot it continues on its own.
5. The last step, "Local control", asks for admin rights again. It installs OpenSSH (set to start with Windows) and a desktop agent so LiteCode can open apps, take screenshots and click on this PC. Click "No" to skip; you can add it later from the tray menu.
6. A **LiteCode** icon appears on the desktop and in the tray, and it starts with Windows. Double-click the icon to open the window.

**Sign in**: the window is `http://127.0.0.1:18790` and **only this PC can reach it**. The default password is `admin`; change it under Settings → Change password.

**Updates**: the tray checks once a day and asks before updating. An update downloads, verifies and swaps the code in about a minute, keeping your chats and settings, and rolls back automatically if the new version fails to start. Code updates come from GitHub first and fall back to a mirror. To check now: right-click the tray icon → Check for updates.

**Uninstall**: Settings → Apps → LiteCode. ⚠️ Uninstalling deletes the LiteCode subsystem together with the chats and files inside it, so export anything you want to keep first.

<details>
<summary><b>What does the installer set up? What is WSL?</b></summary>

LiteCode itself is a Linux program. Instead of making you install Docker Desktop, the Windows edition uses **WSL**, the built-in Windows feature for running Linux:

- The installer contains a packaged Linux system (a root filesystem) and imports it as a WSL distro called **`LiteCode`**. All programs, dependencies and the browser live inside it, separate from any other Linux setup on your PC.
- The tray app `LiteCode.exe` starts, stops and updates that distro and opens the window.
- Data folder: `%LOCALAPPDATA%\LiteCode` (distro disk, logs, download cache).
- The model pack for local voice-over and the knowledge base (about 900 MB) is not in the installer; the tray downloads it in the background with resume support.

Check on it from cmd:

```
wsl -l -v
```

`LiteCode   Running   2` means it is running. Open a shell inside:

```
wsl -d LiteCode
```

Stop it completely (the tray starts it again when needed):

```
wsl --terminate LiteCode
```

</details>

<details>
<summary><b>The install failed</b></summary>

Check the log first: `%LOCALAPPDATA%\LiteCode\launcher.log` (paste the path into the Explorer address bar). Known issues:

| Symptom | Cause | Fix |
|---|---|---|
| `读默认配置失败: invalid character 'w'` | A local proxy (Clash and the like) makes WSL print a proxy warning that got mixed into the installer's output | Fixed in current installers. With an old one, run the three commands below in cmd and rerun the installer |
| Virtualization / WSL will not start | Virtualization is off in the BIOS | Reboot into the BIOS and enable Intel VT-x or AMD SVM |
| Stuck at "Local control" for a long time | OpenSSH and Python dependencies were being prepared | Current installers bundle them. A failure here does not affect the main app; retry later from the tray |
| `Could not establish trust relationship for the SSL/TLS secure channel` | Outdated certificate store | Run Windows Update and retry |
| Model error `Illegal header value b'Bearer '` | Old versions with an empty API key | Fixed (an empty key is treated as `EMPTY`) |

Turn on mirrored networking for WSL when using a local proxy (one command at a time in cmd):

```
echo [wsl2]> "%USERPROFILE%\.wslconfig"
```

```
echo networkingMode=mirrored>> "%USERPROFILE%\.wslconfig"
```

```
wsl --shutdown
```

Details: [docs/DESKTOP_WINDOWS.md](docs/DESKTOP_WINDOWS.md) (Chinese).

</details>

<details>
<summary><b>Silent / batch install</b></summary>

```
LiteCode-Setup-<version>.exe /S
LiteCode.exe --setup --unattended --password <pw> --model-url <url> --model-name <model> --model-key <key> --no-tray
LiteCode.exe --selftest
LiteCode.exe --local-control
LiteCode.exe --setup --no-local-control
```

In order: silent unpack; unattended setup; write environment checks to `selftest.txt`; local control only; set up without local control. Exit code `3010` means a reboot is needed (setup resumes after it), `2` means failure.

</details>

---

### B: Linux + Docker

For a server that stays on: scheduled tasks and the WeChat bot keep running when your own PC is off.

**Requirements**: 64-bit Linux (mostly tested on Ubuntu 22.04 / 24.04), 4 cores and 8 GB RAM or more, 30 GB of disk.

**1. Install Docker** (skip if `docker` and `docker compose` already work)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # log out and back in so you don't need sudo
docker compose version             # prints a version when it works
```

In mainland China add `-s -- --mirror Aliyun` after `sudo sh`.

**2. Get the code**

```bash
git clone https://github.com/wfcz10086/litecode.git
cd litecode
```

**3. First start**

```bash
./start.sh
```

The first run creates `config.json` from `config.example.json`, generates a random secret for the SearXNG search engine, and builds the image `litecode:latest`, also tagged with the version number (e.g. `litecode:v2.0.13`) (**10–15 minutes**).

**4. Edit the config** (required)

```bash
nano config.json
```

Change at least these four (all placeholders are `CHANGE_ME` in the example):

| Key | Set it to |
|---|---|
| `model.backend_url` | Your model endpoint, like `http://x.x.x.x:8000/v1` |
| `model.api_key` | The model's key, or `EMPTY` |
| `web_ui.auth.password` | Web sign-in password |
| `server.token` | API gateway token, any random string (`openssl rand -hex 16`) |

Apply it:

```bash
./start.sh --restart
```

**5. Open it**

Browse to `http://SERVER_IP:18790`; a sign-in page means it works. If not, check on the server:

```bash
./start.sh --status
curl -s http://127.0.0.1:18789/health
```

If it works locally but not from outside, open port `18790` in the firewall or cloud security group.

> ⚠️ Change the default password and token before exposing it to the internet, and preferably put HTTPS in front (nginx / Caddy).

**Everyday commands**

| Command | Does |
|---|---|
| `./start.sh --logs` | Follow logs |
| `./start.sh --restart` | Restart after config changes |
| `./start.sh --down` | Stop |
| `./start.sh --build` | Rebuild the image when dependencies change |
| `./start.sh --shell` | Shell into the container |
| `git pull && ./start.sh --restart` | Upgrade |

**Optional parts**

| Part | How | Notes |
|---|---|---|
| SearXNG search | Starts with compose by default | Open-source metasearch, listens on `127.0.0.1:18888` only |
| Knowledge base | `bash scripts/kb_setup.sh` | PostgreSQL + pgvector (local only, random password), writes the config for you; see [docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md) |
| `litecli` | `sudo ln -sf $(pwd)/litecodeext/litecli /usr/local/bin/litecli` | Then `litecli login` and `litecli` |

---

### C: Docker inside WSL

To run exactly the server setup on Windows (for example to hack on the code), follow option B inside an Ubuntu WSL distro.

1. Open cmd **as administrator** and install Ubuntu (reboot if asked, then set a Linux user name and password):

   ```
   wsl --install -d Ubuntu-24.04
   ```

2. Open "Ubuntu 24.04" from the Start menu and run everything below in that window.
3. Follow steps 1–4 of [option B](#b-linux--docker).
4. Open `http://localhost:18790` in your Windows browser.

> Options A and C can be installed side by side, but both use port 18790, so don't run them at the same time.

---

## Connect a model

LiteCode does not ship a model; it works through the endpoint you give it.

- **URL**: OpenAI format ending in `/v1`. Self-hosted vLLM / SGLang / Ollama (`http://IP:11434/v1`), cloud APIs and relays all work.
- **Model name**: exactly as the endpoint names it.
- **Key**: `EMPTY` if there is none.

You can also just say in chat "add a model, URL …, name …"; it adds it, tests it, and switches to it. Every model under Settings → Models has a Test button.

> Models with reliable tool calling work best. GLM-5.3-Flash, Qwen3.6 / 3.8 and the DeepSeek-V4 family finish long tasks reliably in our tests; very small models tend to go in circles. If the main model cannot read images, screenshots and OCR go to the vision model you configure.

## First steps

After signing in, try:

```text
Write a Python command-line expense tracker with tests and make them pass
```

```text
Summarize today's stock market in a table and make a Word report
```

```text
Every morning at 9, check the BTC price and send it to my WeChat
```

Each step shows up as it runs (which tool, how long, expandable details). Finished files preview and download right in the reply. You can steer while it works: Enter adds a note, "⚡ Now" interrupts and redirects.

**Other ways in**

| Channel | How |
|---|---|
| WeChat | Toolbox → WeChat → scan the QR code. A WeChat account can be bound to one LiteCode at a time; the latest scan wins |
| WeCom | Toolbox → WeCom, enter the bot details |
| Command line | `litecli` shows diffs before editing and reports token usage |
| API | `http://HOST:18789/v1` is a standard OpenAI Chat Completions endpoint with `Authorization: Bearer <server.token>`. Point ChatBox, Dify, LangChain or any client at it to get tools, search, memory and all skills behind your model |
| Control another Windows PC | Toolbox → Windows → Add generates a command; paste it into an admin terminal on that PC. It installs OpenSSH, creates and authorizes a dedicated key (key-only, no password), and the PC connects out to LiteCode, so no inbound port is needed |

## What it does

<table>
<tr>
<td width="50%" valign="top">

**💻 Code**
Reads the repo, edits, runs tests and only delivers once they pass; sub-agents work in parallel in separate worktrees, reviewed before merging.

**🔍 Research**
SearXNG metasearch first, several engines in parallel, a real browser as fallback; reads pages and cites sources. Also GitHub, package, Stack Overflow search and live quotes for China A-shares, Hong Kong and US stocks.

**📄 Documents**
Create, read and edit Word, Excel, PPT and PDF; 5,000–20,000-word research reports; OCR for scans.

**🎬 Audio and video**
CPU-only local voice-over; script → voice → subtitles → visuals → music into a finished video; Douyin link parsing, watermark-free download and remixing; ComfyUI image and video generation.

</td>
<td width="50%" valign="top">

**🖥️ Computer control**
On Windows: open apps, screenshot, OCR, click controls by name, type, run PowerShell, transfer files; explains why when the screen is locked or disconnected.

**🛠️ Servers**
Run commands over SSH, read, write and sync files, inspect and recreate remote Docker containers.

**⏰ Automation**
Scheduled tasks (the server edition runs them with your PC off); a visual DAG pipeline editor with conditions, sub-pipelines and resume.

**🧠 Memory and knowledge base**
Remembers your preferences after one correction; upload documents into a knowledge base with cited retrieval; memory can be viewed and deleted.

</td>
</tr>
</table>

Also: 110 built-in skills (type `/` in the input box; install more from the skill market), a usage dashboard, read-only share links, per-turn screen recordings, and prompt-injection defense (instructions found in web pages or search results are never followed, and sensitive actions need your confirmation).

<details>
<summary><b>Screenshots</b></summary>

**Every step and how long it took, one click to expand**

![Steps](docs/screenshots/v2/02_chat_steps.jpg)

**Two sub-agents write Python and Go versions in parallel; the main agent reviews and merges**

![Sub-agents](docs/screenshots/v2/03_subagents.jpg)

<table>
<tr>
<td align="center" width="50%"><b>Skill picker</b><br><img src="docs/screenshots/v2/04_skill_picker.jpg" width="100%"></td>
<td align="center" width="50%"><b>Skills and market</b><br><img src="docs/screenshots/v2/05_skills.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>Usage dashboard</b><br><img src="docs/screenshots/v2/06_dashboard.jpg" width="100%"></td>
<td align="center"><b>Settings</b><br><img src="docs/screenshots/v2/07_settings.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>Toolbox</b><br><img src="docs/screenshots/v2/08_toolbox.jpg" width="100%"></td>
<td align="center"><b>DAG pipelines</b><br><img src="docs/screenshots/v2/09_dag.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>Windows tray update</b><br><img src="docs/screenshots/v2/11_win_update.jpg" width="100%"></td>
<td align="center"><b>WeChat</b><br><img src="docs/screenshots/04_wechat.jpg" width="100%"></td>
</tr>
</table>

**A Douyin remix from one sentence** (frames from the result; all visuals AI-generated)

![Douyin remix](docs/screenshots/v2/12_douyin_remake.jpg)

</details>

## Compared with similar tools

> As of 2026-10, from each project's official repository and public material; details may change between versions.

| | LiteCode | OpenClaw | DeepSeek-TUI (Codewhale) | Tencent WorkBuddy |
|---|---|---|---|---|
| What it is | Self-hosted all-round assistant: web + CLI + bots + API | Self-hosted personal assistant used from chat apps | Coding agent in the terminal | Commercial desktop app + cloud |
| License / price | MIT, free | MIT, free | MIT, free | Closed source, credit-based |
| Models | Any OpenAI-compatible, including self-hosted | Several cloud providers + Ollama | 40+ providers, DeepSeek by default | A fixed set |
| Windows | Offline installer, tray, auto-update | Needs WSL2 + Node | Single executable | Installer |
| WeChat / WeCom | ✅ built in | Mostly non-Chinese channels | ❌ | WeCom / QQ / Feishu / DingTalk |
| Windows desktop control | ✅ | Runs actions on the host | macOS only, experimental | macOS only, experimental |
| Visual pipelines | ✅ DAG | ❌ | ❌ | ❌ |
| Scheduled tasks with PC off | ✅ (server edition) | ✅ | ❌ | ❌ |
| Mobile app | ❌ (web works on phones) | ✅ | ❌ | ✅ |
| Native macOS | ❌ (Docker) | ✅ | ✅ | ✅ |
| OS-level sandbox | ❌ (confirmations + permission switches) | Configurable | ✅ | ✅ |
| Community | Small | Largest | Large | Large user base |

**Pick LiteCode** if you work in China (WeChat, domestic mirrors and search engines out of the box), want a full web workspace, need to control Windows or manage servers, and want models and data under your control.
**Look elsewhere** if you need a mobile app, native macOS, an OS-level sandbox, or a big community.

## FAQ

**Does it need a GPU?**
No. Use any API for the model; local voice-over and the knowledge base's embedding model run on CPU.

**Does it upload my data?**
Not on its own. Chats, memory and files stay on your machine; only the model endpoint you configure and the sites you ask it to visit receive requests. It will not upload files to public hosts without your explicit consent.

**Desktop or server edition?**
Desktop for one personal PC; server if it must be online 24/7 (scheduled tasks, WeChat bot). You can run both.

**Can it use Claude / GPT?**
Yes, through any OpenAI-compatible endpoint or gateway.

**Can it control the PC while the screen is locked?**
Command-line work (files, processes, installs) yes. Anything that needs the screen reports why it can't and how to fix it.

**How do I build it or make the Windows installer?**
See [docs/BUILD.md](docs/BUILD.md) (Chinese).

## Project layout

```
 WeChat / WeCom ┐
 litecli        ├──▶ Gateway :18789 (OpenAI-compatible; agent loop, tools, memory)
 Browser ───────┘          ▲
                    Web :18790 (chat, artifacts, dashboard, pipelines, settings)
                           │
       SearXNG · knowledge base · Windows nodes · SSH hosts · ComfyUI
```

| Folder | Contents |
|---|---|
| `litecodeext/` | The app: gateway `litecode_server.py`, web `web_ui.py`, core `lib/`, tool plugins `plugins/`, `skills/`, front end `web_assets/` |
| `packaging/` | Windows edition (Go tray + NSIS installer + WSL system image) and release scripts |
| `docs/` | Architecture, desktop edition, knowledge base, build notes |

A tool is a single plugin file and a skill is a `SKILL.md` plus scripts, so both are easy to add. More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Latest release

### v2.0.13 "Desktop" (上桌) (2026-10-23)

- 🔌 **Integrations**: a new top-left panel to connect image, video, TTS/ASR, music, search and notification services; titles on the left, plain-text usage on the right, one-click test and defaults; type `@` in the input box to attach a service's full details to a message. 29 presets.
- 🧭 **11 decision-making skills**: premortem, decision matrix, red team, second-order effects, calibrated forecasts, bias check, after-action review, priority triage and more.
- 🌐 **Million-word translation** `long-translate`: glossary, concurrency, resume, keeps Word/Markdown/EPUB/subtitle formatting, QA report.
- 📐 **Diagram skill** `diagram`: renders Mermaid / Graphviz flowcharts, architecture and sequence diagrams locally; PlantUML on demand; used for decks and documents.
- 👁️ **Vision modes**: tables, UI mockups, reverse-engineered image prompts from references, chart reading, error screenshots; shared "transcribe faithfully" rules.
- 📖 **README rewritten**: step-by-step guides for all three ways to install (Windows installer / Linux Docker / Docker inside WSL) with troubleshooting tables; version history moved to [CHANGELOG.en.md](CHANGELOG.en.md); added the MIT LICENSE file and build notes in [docs/BUILD.md](docs/BUILD.md).
- 🏷️ **Image tags follow releases**: the Docker image is now `litecode:latest` plus `litecode:<version>` instead of a fixed `v12-r1`; an existing old image is retagged on start, no rebuild needed.
- 🪟 **New full installer**: tray code updates prefer GitHub; the start banner no longer shows a wrong default password.

**v2.0.12**: pages and the browser close when done; a clear message when the desktop has no Docker; tray code updates prefer GitHub.

Full history in [CHANGELOG.en.md](CHANGELOG.en.md).

## License

[MIT](LICENSE). A ⭐ helps; issues are welcome [here](https://github.com/wfcz10086/litecode/issues).
