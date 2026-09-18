---
name: system-selfcheck
description: >
  Run the full V3 system self-check: gateway smoke, 4 official APIs
  (Kimi/GLM/Qwen/DeepSeek), workflow DAG, vision OCR. Use this any time
  the user asks "系统还正常吗", "自测一下", "自检", "smoke", "everything OK",
  or after a config change / provider swap / new model added. Returns a
  pass/fail matrix and the exact failing script if any.
---

# System Self-Check Skill

Runs every smoke script in `scripts/` and reports one pass/fail per subsystem.

## When to invoke

- User asks "跑一下自检" / "system OK?" / "self-test" / "smoke"
- After editing `config.json` (added/removed a model or provider)
- After merging changes that touch `litecodeext/v3/`
- Before shipping a release
- As V10 end-to-end validation

## What it runs

| Subsystem            | Script                              | Pass criterion |
|----------------------|-------------------------------------|----------------|
| Provider registry    | `scripts/v3_smoke_providers.py`     | 6 models load |
| Gateway HTTP + SSE   | `scripts/v3_smoke_gateway.py`       | all endpoints 200, SSE done event |
| 4 official APIs      | `scripts/v3_smoke_official.py`      | 4/4 pass |
| Workflow DAG         | `scripts/v3_smoke_workflow.py`      | topological run OK |
| Vision OCR reuse     | `scripts/v3_smoke_ocr.py`           | Qwen3.6-35B answers image prompt |

Any single failure → skill returns exit 1 with the failing script's tail.

## Usage

```
system_selfcheck()
# 或者
system_selfcheck(subset="official,gateway")
```

## Output shape

```
=== System SelfCheck ===
  OK   providers          6 models / 7 providers registered
  OK   gateway            /v3/* + /v1/chat/completions
  OK   official           4/4 (kimi-k2.7-code, glm-5.2, qwen3.7-max, deepseek-chat)
  OK   workflow           5 nodes, DAG topo run 120ms
  OK   vision             Qwen3.6-35B OCR reused
=== ALL PASS ===
```

## Rationale

Consolidates the smoke-test lore in one invokable place so Claude Code (or
a user asking through the chat UI) can self-verify without remembering
6 different script paths. Also serves as the acceptance harness for V10.
