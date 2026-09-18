<div align="center">

# ⚔️ LiteCode

### One box. Three fronts. Zero cloud.

**English** · [中文](README.zh.md)

**The sharpest self-hosted AI arsenal.** One `./start.sh` turns a machine you own into a full-time AI engineering team — a Web command center, a terminal that fights beside you, and WeChat / WeCom bots that never leave your pocket. Your models. Your disk. Your rules.

![MIT](https://img.shields.io/badge/license-MIT-green) ![Docker](https://img.shields.io/badge/docker-one--container-blue) ![OpenAI-compatible](https://img.shields.io/badge/API-OpenAI--compatible-orange) ![Self-hosted](https://img.shields.io/badge/self--hosted-100%25%20private-red) ![Bots](https://img.shields.io/badge/WeChat%20%2F%20WeCom-built--in-brightgreen)

![LiteCode Web UI](docs/screenshots/01_overview.jpg)

⭐ **If LiteCode ships one task for you today, pay it back with a star — 3 seconds for you, rocket fuel for us.**

</div>

---

## The numbers first

| 72 | 92 | 9 | 14 | 5 | 4 | 3 | 1 |
|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| native tools | skills | agent types | search engines | model backends | memory tiers | fronts (Web/CLI/bots) | container |

No cluster. No SaaS bill. No data leaving home.

---

## What it does for you — on day one

- 📄 **Drop a PDF into your WeChat** → get the summary back in chat, on your phone, from your own model.
- 🗣️ **Say** *"scan this repo and write an architecture report"* → LiteCode **builds a 3-step pipeline by itself** (scan → analyze → write) and runs it like Jenkins runs a build.
- ⏰ **Every morning at 9:00** a cron DAG pulls the data you care about and pushes a briefing to WeCom — while you're still on coffee.
- 🖼️ **Paste a screenshot** → the vision model reads it. **Ask for slides** → the pptx plugin renders them. **Need an image?** `image_gen` has DALL·E / SD / ComfyUI wired in.
- ⌨️ **One prompt in the terminal** → files written, diffs rendered, shell executed, tokens accounted. The CLI is a weapon, not an afterthought.

---

## 92 skills — it doesn't just work, it has personality

- 💞 **Companion mode** — when you say *"I'm exhausted"*, it doesn't lecture you about sleep. It listens, empathizes, and *sees* you — short, warm, zero preaching. Auto-triggers on emotional signals. Rare in AI tools, and people love it.
- 🎭 **Strategist mode** — one click flips on game-theory glasses: *who benefits, before what's true*. It dissects interests / players / variables / both sides — for markets, hype-checking, or any narrative someone's pushing.
- ✍️ **21-genre novel pipeline** — xianxia, wuxia, sci-fi, romance, palace-intrigue, system-flow, horror… each genre its own skill, plus long-form engineering and an **anti-AI-tell audit** that polishes prose until it stops sounding like a bot.
- 📊 **Deep reports** — 5,000–20,000 words, searching *while* writing, with citation-density checks.
- 💰 **Crypto tracking** with scheduled briefings to your WeChat / WeCom · data scraping & analysis · log forensics.
- 🛠️ **Engineer's kit** — Chinese code review, bug localization, API scaffolding, systematic debugging, TDD, Linux & remote-SSH ops.
- 📄 **Office suite** — docx / xlsx / pptx generation, PDF + OCR pipeline, HTML-to-report. Drop a file into WeChat, get a file back.

> Skills are **files-as-plugins**: write one `SKILL.md` (there's a `skill-creator` to help) and your AI learns a new trade.

---

## Screenshots

<table>
<tr>
<td align="center" width="33%"><b>🏗️ DAG editor — AI-flavored Jenkins</b><br><img src="docs/screenshots/03_dag_editor.jpg" width="100%"></td>
<td align="center" width="33%"><b>⌨️ CLI — prompt → tools → diff → done</b><br><img src="docs/screenshots/02_cli.jpg" width="100%"></td>
<td align="center" width="33%"><b>📱 WeChat integration + vision fallback</b><br><img src="docs/screenshots/04_wechat.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>🤖 WeCom (Enterprise) smart bot</b><br><img src="docs/screenshots/05_wecom.jpg" width="100%"></td>
<td align="center"><b>🧠 Four-tier memory</b><br><img src="docs/screenshots/06_memory.jpg" width="100%"></td>
<td align="center"><b>🔌 Model management — hot switch</b><br><img src="docs/screenshots/07_models.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>⏰ Scheduled tasks (cron / once)</b><br><img src="docs/screenshots/08_timer.jpg" width="100%"></td>
<td align="center"><b>📦 Artifacts store</b><br><img src="docs/screenshots/09_artifacts.jpg" width="100%"></td>
<td align="center"><b>📊 Usage & cost dashboard</b><br><img src="docs/screenshots/10_stats.jpg" width="100%"></td>
</tr>
<tr>
<td align="center"><b>🐳 Docker control from chat</b><br><img src="docs/screenshots/11_docker.jpg" width="100%"></td>
<td align="center"><b>🗂️ Projects — shared context</b><br><img src="docs/screenshots/12_projects.jpg" width="100%"></td>
<td align="center"><b>⌨️ Full CLI command surface</b><br><img src="docs/screenshots/14_cli_cmds.jpg" width="100%"></td>
</tr>
</table>

---

## Why LiteCode wins

- 🏠 **100% private, zero cloud.** Gateway (OpenAI-compatible SSE) + Web + CLI + bots in **one** Docker container. Point it at your own vLLM or any OpenAI-compatible endpoint. Keys, sessions, memory, artifacts — all on your disk.
- 🧩 **A chassis, not a monolith.** Drop in one `plugin.py` → new capability. Pull an env var → gone. `pptx` and `cad` mount in from outside with **zero copied code**.
- 🧠 **Dynamic memory compression — it actually remembers you.** Four tiers + truth-calibrated budget + auto-compress + crash recovery: long chats don't snap, new sessions start informed. Elephant-grade recall that makes this a **general-agent substrate**, not just a toolbox.
- 🏗️ **AI-flavored Jenkins.** Pipeline / Job / Build History / Artifact / breakpoint / resume — a mental model you already trust. AI adds exactly three things: **speak a pipeline into existence, thread the parameters, summarize the result.**
- 📱 **The only self-hosted agent with real WeChat + WeCom bots.** Scan a QR and your personal WeChat becomes an AI terminal; WeCom gets true streaming replies. Same brain as Web and CLI.
- ⚡ **A brute-efficiency machine for one person.** No meetings with your infrastructure. `./start.sh`, then work.

---

## How it's different

| | **LiteCode** | Dify | LangChain | Open WebUI |
|---|:---:|:---:|:---:|:---:|
| Single self-hosted container | ✅ | ✅ | library | ✅ |
| OpenAI-compatible gateway | ✅ | partial | — | chat only |
| One agent brain across **Web + CLI + chat bots** | ✅ | Web | code | Web |
| **WeChat / WeCom (Enterprise) bots** built in | ✅ | — | — | — |
| Visual **DAG multi-agent** (Jenkins-style) | ✅ | flows | code | — |
| Plugin chassis for **image / video / music gen** | ✅ | tools | tools | — |
| **Talk-to-control desktop** (noVNC) | ✅ | — | — | — |
| Four-tier persistent memory | ✅ | partial | — | basic |

_Rough positioning, not a scorecard — every tool above is great at what it's for. LiteCode's bet is "one private box that does all of it."_

---

## Built lean, on purpose

Agent frameworks love to sprawl — a mesh of services to babysit, a maze of permissions to fight, behavior you can't see. LiteCode goes the other way:

- **One container, not a service mesh.** Host-network, `./start.sh`, done.
- **Permissions that stay out of your way.** Bearer token + cookie + per-IP rate limits + owner-scoped artifacts + lightweight guard rails. Legible and minimal — not a week-long RBAC fight.
- **No black boxes.** Tracing, guard-injection stats, execution-record timeline — you can always see what the agent did and why.

---

## Feature tour

### 🔌 Any model, any modality
OpenAI-compatible SSE gateway (LangChain / OpenAI SDK / Dify / Open WebUI connect out of the box) · five backend stacks (`vllm / openai / anthropic / ollama / deepseek`) · hot model switch · per-message model & thinking-effort override · vision routing with configurable fallback.

### 🎨 Generative plugins — the chassis stays open
Image generation **built in** (DALL·E 3 · SD-WebUI · ComfyUI · mock). Video / music / any-modality: **one drop-in file** mirroring the image-gen skeleton — registry auto-discovers it, and it instantly becomes a tool the agent can call **and** a DAG step you can orchestrate. Core untouched.

### 🔍 Multi-layer search — never blocked
Free search APIs → **local SearXNG sidecar** (private JSON gateway, no CAPTCHAs) → 14 engines of parallel HTML → a **real Playwright browser** as last resort → `deep_search` clicks into results and reads the actual text. One blocked engine never stops you.

### 🏗️ DAG multi-agent orchestration
Topological scheduling · same-layer parallelism · Critic re-runs · checkpoint restore · failure self-reflection. Node types: agent / native tool / **sub-DAG** / **decision (2 outlets)**; `when` conditions + `${step:id:json:path}` value passing. **Natural-language pipeline ops**: generate / edit / delete / schedule by talking.

### ⌨️ CLI — three ends, one brain
Interactive REPL straight on the gateway: streaming, foldable thinking, two-stage `Ctrl-C` interrupt, live tool-call & diff rendering. Full command surface: `chat · dag · model · sessions · memory · timer · wechat · wecom · docker · plugins · projects · stats · ws · config · repl` + slash commands (`/think /interrupt /dag /memory /checkpoints …`). `--output-format stream-json` for CI.

### 🧠 Dynamic memory compression — an elephant among goldfish
Most agents get dumber as the chat gets longer; context fills, memory snaps. LiteCode's memory is a **live compression engine**:
- **Four tiers (L0–L3)**: sliding window → always-on `MEMORY.md` → typed memory files → SQLite FTS5 cross-session recall. Every new session starts already knowing who you are.
- **Truth-calibrated budget**: the context budget is anchored to the **real token usage reported upstream** — compress exactly when needed, lose nothing when not.
- **Auto-compress + auto-capture**: background compression at threshold; preferences written **by rule** (secrets auto-redacted) — no praying the model remembers to save.
- **Crash-proof**: session WAL recovery + task-boundary snapshots & rollback. Power cuts, restarts, fat-fingers — there's an undo.

This is why LiteCode dares to call itself a **general-agent substrate**: memory, tools, skills and orchestration are a pluggable foundation you can grow *any* agent on.

### 🖥️ Talk-to-control desktop & more
Drive a real browser/GUI on a noVNC desktop from chat (`desktop_exec / screenshot / key / type / click`) · scheduled tasks with history & notifications · artifacts store · Docker control · usage & cost accounting.

---

## Quick start — 60 seconds

```bash
git clone https://github.com/wfcz10086/litecode && cd litecode
cp config.example.json config.json     # point it at your model endpoint + key
./start.sh                             # builds if needed, then runs
```

| Port | What | Auth |
|---|---|---|
| `:18789` | Gateway API (OpenAI-compatible) | Bearer token |
| `:18790` | Web UI + CLI backend | Cookie password |
| `:18800` | noVNC remote desktop | — |

### Login & passwords (30 seconds)

- **Web UI**: open `http://<your-box>:18790`, type one password (`config.json → web_ui.auth.password` — change it before you start).
- **API / third-party calls**: send `Authorization: Bearer <token>` (`config.json → server.token`).
- **CLI**: `litecli login` once, cookie saved locally; `litecli repl` for the interactive shell.
- That's all three. **No signup, no multi-tenant RBAC maze** — it's your machine.

### Third-party hookup — buff every AI client you already use

The gateway speaks standard OpenAI protocol, so **any client with a custom base_url** (LangChain / OpenAI SDK / Dify / Open WebUI / translation plugins / any ChatBox) instantly upgrades:

> Same model, routed through LiteCode, now carries **tool calling + layered search + persistent memory + 92 skills + vision routing**. Your translator suddenly does research; your chat client suddenly remembers you.

```
base_url = http://<your-box>:18789/v1     api_key = <server.token>
pass a fixed `user` field = session id → memory persists across requests
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:18789/v1", api_key="YOUR_TOKEN")
r = client.chat.completions.create(
    model="your-model", stream=True, user="my_session",
    messages=[{"role": "user", "content": "Scan this repo and write a report"}],
)
for c in r:
    print(c.choices[0].delta.content or "", end="", flush=True)
```

---

## Architecture

```
   WeChat / WeCom bots ┐
   CLI (REPL)          ├──▶  Gateway :18789  (OpenAI-compatible SSE — the agent loop)
   Browser ────────────┘            ▲
                                    │ same-origin proxy
                        Web UI :18790  (DAG · timers · memory · model/plugin/container admin)
```

Web is the full hub; CLI is the fighting knife; WeChat/WeCom are your pocket terminals. One brain underneath. Deep dive: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

<div align="center">

## ⭐ Star it. Seriously.

LiteCode is hammered out in the open by a tiny team. Every star is oxygen: it's how the next person finds a private, all-in-one alternative to renting their AI life from a cloud.

**[⭐ Star this repo](https://github.com/wfcz10086/litecode)** · Watch for releases · Open an issue with what you'd build

</div>

## License

MIT
