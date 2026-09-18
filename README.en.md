<div align="center">

# ⚔️ LiteCode

[中文](README.md) · **English**

**LiteCode isn't another chat window — it's the first AI employee that lives on your machine.** In the browser it's your strategist; in the terminal it's your engineer; on WeChat it's the assistant that always picks up. Same brain, real memory, real skills, works on its own schedule. Salary: zero. Data: yours. Cloud vendors don't make a cent off you.

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

And a side no other agent has:

- 💞 **Companion mode** — 2 a.m., you type *"can't sleep, feeling low"*. It won't prescribe a sleep schedule; it says *"I'm here. Say whatever you want."* Preaching is literally banned in this mode. Emotional signals trigger it automatically.
- 🎭 **Strategist mode** — ask *"can I trust this bullish news?"* and instead of repeating the headline, it dissects the game: who planted this, who profits, who's the counterparty, what are the variables — both sides argued, then a verdict. One toggle. Rule #1: **ask who benefits before asking if it's true.**
- ✍️ **Serious novel-writing** — 21 genres, each with its own skill, long-form outline engineering for consistency, and a final "de-AI-flavor" audit pass so the prose doesn't read like a bot.

---

## 4. Screenshots

**DAG pipeline editor** — draggable palette on the left (9 agent types + ready-made tools + decision branches + sub-pipelines), wire nodes up, hit Build:

![DAG editor](docs/screenshots/03_dag_editor.jpg)

**CLI** — one prompt in: files written, diff shown, commands run, tokens reported:

![CLI](docs/screenshots/02_cli.jpg)

**WeChat** — scan a QR to connect; a vision-fallback model auto-covers when your main model can't see images:

![WeChat](docs/screenshots/04_wechat.jpg)

<table>
<tr>
<td align="center" width="50%"><b>🤖 WeCom bot — true streaming replies</b><br><img src="docs/screenshots/05_wecom.jpg" width="100%"></td>
<td align="center" width="50%"><b>🧠 Memory — it remembers your chats</b><br><img src="docs/screenshots/06_memory.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>🔌 Models — hot-switch anytime</b><br><img src="docs/screenshots/07_models.jpg" width="100%"></td>
<td align="center"><b>⏰ Scheduled tasks + run history</b><br><img src="docs/screenshots/08_timer.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>📊 Usage & cost at a glance</b><br><img src="docs/screenshots/10_stats.jpg" width="100%"></td>
<td align="center"><b>🐳 Manage Docker from chat</b><br><img src="docs/screenshots/11_docker.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>📦 Artifacts store</b><br><img src="docs/screenshots/09_artifacts.jpg" width="100%"></td>
<td align="center"><b>⌨️ Full CLI command set</b><br><img src="docs/screenshots/14_cli_cmds.jpg" width="100%"></td>
</tr>
</table>

---

## 5. OK, now the bragging

### The positioning: an employee, not a tool

A tool only exists while you're holding it. An employee is *there*. LiteCode is built to the employee standard:

- **It remembers** — preferences you stated, things you discussed. New sessions don't start with introductions.
- **It has skills** — 92 of them, from code and reports to slides and novels; teach it a new trade by dropping in one skill file.
- **It works unsupervised** — timers fire on schedule, big jobs split themselves into pipelines, failures retry with lessons learned.
- **It's always reachable** — browser, terminal, WeChat; start a chat in one place, continue in another.
- **It serves only you** — your machine, your model, data never leaves. It has no other master.

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

### 🔗 Buff every AI tool you already own

The gateway speaks standard OpenAI. Take any client with a custom base_url — translation plugins, ChatBox, Dify, LangChain — and point it at `http://your-box:18789/v1`. Its model instantly learns to call tools, search the web, and remember you, plus 92 skills. **One URL change, everything upgraded, free.** Pass a fixed `user` field and it even remembers your last conversation.

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
