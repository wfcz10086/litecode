# Changelog

[中文](CHANGELOG.md) · Back to [README](README.en.md)

### v2.0.14 "Desktop" (上桌) (2026-10-23)

- 🎵 **AI songs** `ai-song`: write lyrics → self-hosted open-source ACE-Step sings them → speech recognition checks the vocals.
- 🎬 **One-sentence MVs / voiced shorts** `anime-short`: listens to the song for lyric timestamps → maps lyrics to scenes → LLM-optimized prompts → Qwen-Image first frames (your photo can be the character reference) → MiniMax H3 video driven by the music (AniSora in parallel for instrumental parts) → per-segment vision review with automatic prompt rewrites → original-lyric subtitles, audio/video aligned. Realistic and full-length by default.
- 🎤 **Self-hosted open-source services**: SenseVoice ASR, IndexTTS 2.5 voice-over (emotion, cloning), ACE-Step 1.5 songs, AniSora anime video; deployment files and redeploy guide in [docs/GPU_SERVICES.md](docs/GPU_SERVICES.md).
- ⏳ **Background progress card**: long jobs show live progress, logs and a stop button under the chat, and no longer block the conversation.
- 🧭 **Fixes**: "New chat" stuck while a long job ran; faster session list; restarts wait for background jobs.
- 🔌 **Simpler integrations**: a card is a title plus plain text (how-to + curl); the system prompt lists connected services so TTS, music and video use them first.
- 🧩 **Custom installers** can ship a preset config (models, vision model, integrations).
- 🛠️ Fixes: multi-line curl parsing, no endless waits when a server drops a task, new skills found without restart, TTS uses the default voice card, remote desktop no longer floods the log.

### v2.0.13 "Desktop" (上桌) (2026-10-23)

- 🔌 **Integrations**: a new top-left panel to connect image, video, TTS/ASR, music, search and notification services; titles on the left, plain-text usage on the right, one-click test and defaults; type `@` in the input box to attach a service's full details to a message. 29 presets.
- 🧭 **11 decision-making skills**: premortem, decision matrix, red team, second-order effects, calibrated forecasts, bias check, after-action review, priority triage and more.
- 🌐 **Million-word translation** `long-translate`: glossary, concurrency, resume, keeps Word/Markdown/EPUB/subtitle formatting, QA report.
- 📐 **Diagram skill** `diagram`: renders Mermaid / Graphviz flowcharts, architecture and sequence diagrams locally; PlantUML on demand; used for decks and documents.
- 👁️ **Vision modes**: tables, UI mockups, reverse-engineered image prompts from references, chart reading, error screenshots; shared "transcribe faithfully" rules.
- 📖 **README rewritten**: step-by-step guides for all three ways to install (Windows installer / Linux Docker / Docker inside WSL) with troubleshooting tables; version history moved to [CHANGELOG.en.md](CHANGELOG.en.md); added the MIT LICENSE file and build notes in [docs/BUILD.md](docs/BUILD.md).
- 🏷️ **Image tags follow releases**: the Docker image is now `litecode:latest` plus `litecode:<version>` instead of a fixed `v12-r1`; an existing old image is retagged on start, no rebuild needed.
- 🪟 **New full installer**: tray code updates prefer GitHub; the start banner no longer shows a wrong default password.

### v2.0.12 "Desktop" (上桌) (2026-10-23)

> Desktop polish: offline voice-over and knowledge base, faster search, and clear reasons when remote control hits a locked or disconnected screen.

**v2.0.12**
- 🧹 **Pages close when done**: search and page reads close their page right away, and the browser exits when nothing is open.
- 🐳 **Clear message without Docker**: the desktop container panel no longer shows `ConnectError: [Errno 2]`; it says there is no local Docker and how to manage remote containers.
- ⬆️ **Tray code updates prefer GitHub**: reads `latest.json` from GitHub and downloads the tagged source tarball, falling back to the mirror if GitHub is unreachable.

**v2.0.11**
- 🔍 **Remote SearXNG**: set `search.searxng_url` in `config.json` (a token path works) so desktop installs without a local SearXNG still get the primary search engine.

**v2.0.10**
- 🌐 **Web fetch works behind a local proxy**: fake-IP addresses (198.18.x) and IPv6-mapped public addresses are no longer blocked as internal; real private ranges still are.

**v2.0.9**
- 🔄 **Sidebar auto-refresh**: chats created or updated from WeChat, WeCom or background jobs appear without reloading; the open chat updates when new messages arrive.
- 💬 **Adding a WeChat bot turns WeChat on**, so bots come back after a restart.
- 🪟 **Steadier PC control**: bundled OpenSSH first (Windows Update only as a time-limited fallback); Python and agent dependencies ship with the installer; inherited Python env vars are cleared; domestic mirrors by default in the distro.

**v2.0.8**
- 🪟 **Fixed Windows install failing with a localhost proxy on** ("invalid character 'w'"): WSL's proxy notice no longer leaks into the config and version the installer reads; when a localhost proxy is detected the installer switches WSL to mirrored networking so the distro can use it too (an existing `networkingMode` is left alone).

**v2.0.7**
- 💬 **WeChat bridge SDK 0.3.0**: when login asks for a pairing code, an input box appears in the WeChat panel; already-bound bots reuse their login; online/offline is reported to the server.
- 🔄 **Desktop updates straight from GitHub**: releasing means pushing a tag; the tray reads `latest.json` in the repo and installs that tag's source archive.

**v2.0.6**
- ⚙️ **Settings**: model-reference fields, search mode and log level are dropdowns; the current model's id/URL follow the model list; DAG tool steps offer tool and sub-DAG names in a dropdown.
- 📦 **Open-source build works out of the box**: the public repo ships noVNC and the SearXNG config; `./start.sh` creates the config on first run; the Windows installer's update URL is set with `LITECODE_UPDATE_URL`.

**v2.0.5**
- 🪟 **Full installer refresh**: tray fixes (model-pack import, working directory) are in the installer; the displayed version matches the release and is enforced at release time.

**v2.0.4**
- 🔍 **5× faster desktop search**: without SearXNG, Chinese queries go to bing / sogou first and failing engines cool down — ~45 s down to ~8 s.
- 🖥️ **Lock / disconnect diagnosis** when screenshots fail, with `tscon` for locked RDP sessions.
- 🖱️ **Coordinate system inferred** when a click doesn't say whether it uses screenshot or screen pixels.
- 🗣️ **Video voice-overs use the local CPU engine first**, online voices as fallback.
- 🧠 No reasoning leaking into replies on no-think turns; follow-up requests no longer get nagged to re-plan.
- 🎨 **ComfyUI reuses a remembered SSH jump host for the same subnet.**

**v2.0.3**
- 🧩 **Skill management**: `/` picker, skill manager (groups, toggles, safety scan, install), market (Anthropic / ClawHub / SkillHub / GitHub), create skills from chat.
- 📊 **Usage dashboard**: token ledger by hour / skill / tool, CSV and offline HTML export.
- 🔗 **Call timeline + friendly errors** with "continue from the failed step".
- 🔍 **Search**: pinned SearXNG, optional Doubao search, weak engines disabled; `stock_quote` live quotes.
- 🗣️ **Local CPU TTS** (sherpa-onnx + Kokoro) with pluggable engines; add, test and switch models from chat (`manage_models`).
- 🎨 **ComfyUI image / video skills**: give `host:port`; Qwen-Image 2.1 and all four MiniMax H3 modes verified.
- 📚 **Knowledge base**: PostgreSQL + pgvector + local Chinese embeddings, sources cited.
- 📄 **Document studio**: HTML-first with real photos and charts, exported to PDF / editable PPTX / DOCX / long image with QA.
- 🤖 **Sub-agent tree**: observable, enterable, steerable; worktree branches reviewed before merge.
- 💰 **Position-sizing persona** for A/HK/US stocks and Binance USDⓈ-M futures (leverage, liquidation estimate).
- 🖥️ **Desktop edition controls this PC**: OpenSSH installed and started on boot, local node `this-pc`; "this computer" always means Windows; programs it opens for you run unelevated.
- 🛡️ **Public upload gate**: no uploads to public file hosts unless you asked to share publicly.
- 📦 **Separate model pack** keeps the installer under 2 GB; voice and KB models download in the background.

<details>
<summary>v2.0.0 – v2.0.2 "Desktop" (2026-10-21)</summary>

> On your desktop: double-click to install on Windows, and steer it mid-task without starting over.

- 🗣️ **Steer while it works**: Enter = add on, **⚡ Now** = redirect; notes appear in the step timeline.
- ⏹️ **Stop actually stops** within a second, including background processes.
- 🧠 **Correct it once, it remembers.**
- 🔍 **Search before downloading**; domestic mirrors; big downloads in the background.
- 📎 **Previews for delivered files.**
- 🪟 **Windows desktop edition**: offline installer with WSL, tray, autostart; ~10 MB code updates with automatic rollback.
- 🎬 **Screen recording** of each turn with captions.
- 🔗 **Sharing & stats**: read-only links; token usage and duration per turn.
- 🤔 **No more endless thinking**: cut off and act.
- 👁️ **Vision fallback** to a model that can see images.
- 🎨 **Redesign**: warm-white theme, unified input, grouped chats, collapsed tool steps, settings drawer, mobile-friendly.
- 🧠 **Human-like memory**: global profile, overwrites, paraphrase recall, inspectable and deletable.
- 🖱️ **Steadier remote control**: control-name clicks, window following, settle waits, final checklist; queueing; 1 GB transfers with sha256.
- 📦 **Large files**: 1 GB streamed uploads, resumable downloads, chunked reads.
- 🎬 **Douyin and audio/video**: link parsing, no-watermark download, script analysis, multi-track voice-over videos, music.
- 🛡️ **More reliable**; 💰 **half the tokens** (fixed prompt ~49k → ~25k); 🪟 **sturdier desktop edition**.

</details>

<details>
<summary>v1.10.16.91 "Gatekeeper" (2026-10-19)</summary>

- 🔑 Change the login password (Settings or `litecli passwd`).
- 🤖 Per-conversation model switching (`/model name`, `/model default`), test button per model.
- 🛡️ Prompt-injection defense: external content is data only; sensitive actions need approval.
- 🚪 Nothing without logging in: noVNC behind the web login, gateway stats need a token, share links up to 30 days.
- 🧹 Hardening: HTML sanitising, key masking, sandboxed SVG/HTML previews, path traversal fixes.

</details>

<details>
<summary>v1.10.16.90 "Brisk" (2026-10-19)</summary>

- 👥 Background helpers with a web panel; parallel coding in separate worktrees with automatic merge; evidence required.
- ⏱️ No idle waiting, no spinning: explicit completion signals, real timeouts with last output.
- 🎬 Richer videos: 8 script styles, script checks, sentence-level narration, subtitles timed to real speech.
- 🪟 Safer Windows control: read-back checks, shell switch as a real permission boundary, signed components, auto rollback of agent updates.
- ⚡ Smoother long chats.

</details>

<details>
<summary>v1.10.16.89 "Errand Runner" (2026-10-16)</summary>

- 🪟 One-command Windows onboarding; the PC connects out, no open ports; one-click full uninstall.
- 🤖 General desktop operation: WeChat, QQ, Chrome, live rooms — click, type, scroll, copy, read the clipboard.
- 🔄 PC agents auto-update and self-heal.
- 🛡️ Safer messaging: asks when several contacts match; never clears your draft; checks the window title.

</details>

<details>
<summary>v1.10.16.88 / v2026.10.16 "Night Shift" (2026-10-16)</summary>

- 🎬 One-line Douyin videos with fact, layout and frame checks.
- 🔌 Turns keep running when you close the page.
- 🧯 Quieter, more accurate system nudges.
- 🛡️ `disabled_tools` per request.
- 📏 SWE-bench Verified harness, offline by default with network-use detection.

</details>

<details>
<summary>v1.3 – v1.10 (2026-05 – 2026-10)</summary>

| Version | Theme |
|---|---|
| v1.3 | consistency, timers, telemetry, autonomy |
| v1.4 | tools & intelligence: memory, MCP, RAG, plan-then-act, projects |
| v1.5 | DAG orchestration, permissions, hooks, web E2E, complex scenarios |
| v1.6 | browser, LSP, skill governance, desktop |
| v1.7 | autonomy, SWE-bench, marketing |
| v1.8 | backtesting, telemetry, audit, three-client alignment |
| v1.9 | stability, performance, web E2E, domestic builds |
| v1.10 | router split, plugin mounts, DAG editor, memory panel, Windows nodes, ChatGPT-level web UX |

</details>

<details>
<summary>v1.1 / v1.1.1 long-form writing (2026-04-18)</summary>

- 21 novel-genre skills, `long-novel` (million-character projects), `outline-generation`, `longtext-processing`.
- `deep-report` (5,000–20,000 characters with citation checks) and `anti-ai-tell-audit`.

</details>

<details>
<summary>v12.x early internal builds (2026-04)</summary>

- v12.9: sub-agent interrupt propagation and live streaming; direct data sources for crypto / stocks / weather; front end split into local static assets.
- v12.7: "sub-task" keywords route to sub-agents.
- v12.6: three-layer zombie-process defense, self-learning loop, skill safety, prompt rewrite, real loop detection.
- v12.5: reasoning compatibility across backends, WeChat latency fix, reasoning shown in the web UI.
- v12.2: fixes from a 73-task end-to-end run (tool dispatch, multi-agent orchestration, memory compression).
- v12.1: server split; automatic reasoning-parameter adaptation for vLLM / OpenAI / Anthropic / Ollama / DeepSeek.

</details>
