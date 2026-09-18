<div align="center">

# ⚔️ LiteCode

[中文](README.md) · **English**

**Run an all-in-one AI assistant on your own machine.** Chat in the browser, use it from the terminal, or ping it on WeChat. It talks to *your* model, keeps everything on *your* disk, and never sends a byte to any cloud you don't own.

![MIT](https://img.shields.io/badge/license-MIT-green) ![Docker](https://img.shields.io/badge/docker-one--container-blue) ![OpenAI](https://img.shields.io/badge/API-OpenAI--compatible-orange) ![Private](https://img.shields.io/badge/self--hosted-100%25-red) ![Bots](https://img.shields.io/badge/WeChat%2FWeCom-built--in-brightgreen)

![LiteCode Web UI](docs/screenshots/01_overview.jpg)

⭐ If it saves you some work, a star is the nicest thank-you.

</div>

---

## 1. Getting it running (just copy-paste)

You need: a Linux box (home server, VPS, old PC — anything) with Docker installed.

**Step 1 — grab the code**

```bash
git clone https://github.com/wfcz10086/litecode && cd litecode
```

**Step 2 — tell it where your model lives**

```bash
cp config.example.json config.json
```

Open `config.json`, find the `model` block, fill in two things:

- `backend_url`: your model endpoint. Self-hosted vLLM, Ollama, or any paid OpenAI-compatible API — anything whose URL looks like `http://xxx:8000/v1`.
- `api_key`: the key. Self-hosted with no key? Put `EMPTY`.

No model yet? Any OpenAI-compatible API reseller works — paste the URL and key, done.

**Step 3 — start it**

```bash
./start.sh
```

First run builds the image (takes a while). After that, startup is seconds.

**Step 4 — open the browser**

Go to `http://your-box-ip:18790`. See a login page? You're in business.

---

## 2. Logging in

Three places need auth, all dead simple:

| Where | How | Where to change it |
|---|---|---|
| **Web** (:18790) | type one password | `web_ui.auth.password` in `config.json` — replace `CHANGE_ME_PASSWORD` |
| **API** (:18789) | header `Authorization: Bearer <token>` | `server.token` in `config.json` — replace `CHANGE_ME_TOKEN` |
| **CLI** | `litecli login` once, then forget it | same password as the Web |

No signup, no multi-tenant anything. It's your machine: one password, one token, done.

> Do change both `CHANGE_ME` values before exposing anything to the internet.

---

## 3. What it can do for you

Working on day one:

- 📄 Drop a PDF into WeChat → get the summary back on your phone.
- 🗣️ Say *"scan this repo and write an architecture report"* → it splits the job into a 3-step pipeline (scan → analyze → write) and runs it end to end.
- ⏰ Set a timer: every morning at 9:00 it pulls your data and pushes a briefing to WeCom.
- 🖼️ Paste a screenshot, it reads it. Ask for slides, it renders them. Need an image — DALL·E / SD / ComfyUI are already wired.
- ⌨️ One line in the terminal: it writes files, runs commands, shows you the diff, and tells you what it cost in tokens.

---

## 4. Screenshots

<table>
<tr>
<td align="center" width="33%"><b>🏗️ DAG editor — AI pipelines, Jenkins-style</b><br><img src="docs/screenshots/03_dag_editor.jpg" width="100%"></td>
<td align="center" width="33%"><b>⌨️ CLI — one prompt, code written & run</b><br><img src="docs/screenshots/02_cli.jpg" width="100%"></td>
<td align="center" width="33%"><b>📱 WeChat — scan a QR and go</b><br><img src="docs/screenshots/04_wechat.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>🤖 WeCom bot — true streaming replies</b><br><img src="docs/screenshots/05_wecom.jpg" width="100%"></td>
<td align="center"><b>🧠 Memory — it remembers your chats</b><br><img src="docs/screenshots/06_memory.jpg" width="100%"></td>
<td align="center"><b>🔌 Models — hot-switch anytime</b><br><img src="docs/screenshots/07_models.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>⏰ Scheduled tasks</b><br><img src="docs/screenshots/08_timer.jpg" width="100%"></td>
<td align="center"><b>📦 Artifacts store</b><br><img src="docs/screenshots/09_artifacts.jpg" width="100%"></td>
<td align="center"><b>📊 Usage & cost at a glance</b><br><img src="docs/screenshots/10_stats.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>🐳 Manage Docker from chat</b><br><img src="docs/screenshots/11_docker.jpg" width="100%"></td>
<td align="center"><b>🗂️ Projects</b><br><img src="docs/screenshots/12_projects.jpg" width="100%"></td>
<td align="center"><b>⌨️ Full CLI command set</b><br><img src="docs/screenshots/14_cli_cmds.jpg" width="100%"></td>
</tr>
</table>

---

## 5. OK, now the bragging

### Numbers first

| 72 | 92 | 9 | 14 | 5 | 4 | 3 | 1 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| tools | skills | agent types | search engines | model backends | memory tiers | fronts | container |

No cluster, no subscription, no data leaving your box.

### 🧠 The memory is genuinely good

Most AIs go goldfish once the context fills up. LiteCode keeps four layers — recent chat, a standing profile, sorted notes, full-text search over everything — and compresses in the background when things get full, then keeps going. Say "answer in Chinese" once and it's remembered; open a new session and it still knows you. Even a power cut won't lose your history.

Put simply: other AIs are goldfish. This one's a butler with a filing cabinet.

### 📱 The WeChat / WeCom bots are real

Not a web wrapper. Scan a QR and your personal WeChat takes text, images, voice and files; the WeCom side streams replies word by word. Same brain as the Web and CLI — a chat you start on your phone continues on your desktop.

### 🏗️ Pipelines run like Jenkins

Don't dump a huge task in one prompt — split it into a pipeline: drag a few nodes (coder, analyst, vision, shell), wire them, hit Build. Breakpoints, resume, self-reflection retries, and a build history that sticks around. Best part: say *"build me a pipeline that does X"* and it assembles one itself.

### 🧩 Want a new capability? Drop in a file

Image generation is built in (DALL·E / SD / ComfyUI). Want video or music generation? Copy the existing skeleton into one `.py` file under `plugins/` — the system picks it up automatically, no core changes. External projects (like a PPT renderer) mount in from outside; unset one env var and they're gone.

### 🔍 Search that can't be blocked

Free APIs → a local SearXNG aggregator → 14 engines in parallel → a real browser as the last resort. And `deep_search` actually clicks into results and reads the text before answering — no title-skimming.

### 🎁 Things you won't find elsewhere

- 💞 **Companion mode** — say "I'm exhausted" and it won't lecture you about sleep; it just stays with you. Auto-triggers on emotional signals, zero preaching.
- 🎭 **Strategist mode** — one click flips on the "who benefits?" lens for news and markets.
- ✍️ **21-genre novel pipeline** — each genre its own skill, plus an anti-AI-tell audit so the prose doesn't read like a bot.
- 🖥️ **Talk-to-control desktop** — it opens a browser on a remote desktop, clicks around, and screenshots back to you.
- 📄 **Office suite** — Word / Excel / PPT / PDF in and out; drop a file into WeChat, get a file back.

### 🔗 Free upgrade for the AI tools you already use

The gateway speaks standard OpenAI. Point any client with a custom base_url (translation plugins, ChatBox, Dify, LangChain…) at `http://your-box:18789/v1` and its model suddenly has tools, search, memory and 92 skills. Pass a fixed `user` field to keep memory across requests.

---

## 6. Compared to the neighbors

| | **LiteCode** | Dify | LangChain | Open WebUI |
|---|:---:|:---:|:---:|:---:|
| Single self-hosted container | ✅ | ✅ | library | ✅ |
| OpenAI-compatible gateway | ✅ | partial | — | chat only |
| Web + CLI + chat bots, one brain | ✅ | Web | code | Web |
| WeChat / WeCom bots built in | ✅ | — | — | — |
| Visual DAG pipelines | ✅ | flows | code | — |
| Image / video / music plugin slots | ✅ | tools | tools | — |
| Talk-to-control desktop | ✅ | — | — | — |
| Four-tier persistent memory | ✅ | partial | — | basic |

_Not a scorecard — they're all great at their thing. LiteCode's bet: one machine of your own, everything on it._

---

## 7. Architecture (one picture)

```
   WeChat / WeCom bots ┐
   CLI                 ├──▶  Gateway :18789  (OpenAI-compatible — the brain)
   Browser ────────────┘            ▲
                                    │
                        Web UI :18790  (pipelines · timers · memory · admin panels)
```

Deep dive: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

<div align="center">

## ⭐ One last thing

This is hammered out by a tiny team. If it saved you an evening, a star takes 3 seconds and makes our whole day.

**[⭐ Star it here](https://github.com/wfcz10086/litecode)** · Issues welcome — we actually read them

</div>

## License

MIT
