#!/usr/bin/env python3
"""V6 smoke: gateway HTTP endpoints (TestClient — no port bind)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def main() -> int:
    from fastapi.testclient import TestClient
    from litecodeext.v3.gateway import create_app
    from litecodeext.v3.config_bridge import load_raw_config

    app = create_app()
    client = TestClient(app)

    cfg = load_raw_config()
    token = (cfg.get("server") or {}).get("token", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    print("=== V6 smoke: /v3/providers ===")
    r = client.get("/v3/providers", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    print(f"  providers: {data['providers']}")
    print(f"  models: {[m['id'] for m in data['models']]}")
    assert data["models"], "no models"

    print("\n=== V6 smoke: /v3/skills (first 3) ===")
    r = client.get("/v3/skills", headers=headers)
    assert r.status_code == 200, r.text
    skills = r.json()["skills"]
    print(f"  total skills: {len(skills)}, first: {[s['name'] for s in skills[:3]]}")
    assert len(skills) > 0

    print("\n=== V6 smoke: /v3/skills/{name} PUT (toggle) ===")
    name = skills[0]["name"]
    r = client.put(f"/v3/skills/{name}", json={"enabled": False}, headers=headers)
    assert r.status_code == 200, r.text
    print(f"  disabled {name}: {r.json()}")
    r = client.put(f"/v3/skills/{name}", json={"enabled": True}, headers=headers)
    assert r.status_code == 200

    print("\n=== V6 smoke: /v3/nodes (workflow node types) ===")
    r = client.get("/v3/nodes", headers=headers)
    assert r.status_code == 200
    nodes = r.json()["nodes"]
    print(f"  {len(nodes)} node types (first non-skill: {[t for t in nodes if not t.startswith('skill.')][:5]})")

    print("\n=== V6 smoke: /v3/chat non-stream (live LLM) ===")
    model_id = data["models"][0]["id"]
    r = client.post("/v3/chat", headers=headers, json={
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with only: OK"}],
        "stream": False,
        "max_tokens": 64,
        "temperature": 0.0,
        "thinking": {"effort": "off"},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    print(f"  content: {body.get('content')!r}")
    assert body.get("content"), "empty content"

    print("\n=== V6 smoke: /v1/chat/completions OpenAI-compat non-stream ===")
    r = client.post("/v1/chat/completions", headers=headers, json={
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with only: OK"}],
        "max_tokens": 64, "temperature": 0.0, "stream": False,
    })
    assert r.status_code == 200, r.text
    ob = r.json()
    print(f"  openai-format choice: {ob['choices'][0]['message']['content']!r}")
    assert ob["choices"][0]["message"]["content"]

    print("\n=== V6 smoke: /v3/chat stream (SSE) ===")
    with client.stream("POST", "/v3/chat", headers=headers, json={
        "model": model_id,
        "messages": [{"role": "user", "content": "Say only: pong"}],
        "stream": True, "max_tokens": 64, "temperature": 0.0,
        "thinking": {"effort": "off"},
    }) as resp:
        assert resp.status_code == 200
        got_content = False
        got_done = False
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            try:
                evt = json.loads(line[5:].strip())
            except Exception:
                continue
            if evt.get("kind") == "content" and evt.get("delta"):
                got_content = True
            if evt.get("kind") == "done":
                got_done = True
                break
    assert got_content, "no content chunk"
    assert got_done, "no done chunk"
    print("  streamed content + done: OK")

    print("\n=== V6 smoke: auth (bad token -> 401) ===")
    if token:
        r = client.get("/v3/providers", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401, f"expected 401 got {r.status_code}"
        print("  bad token rejected: OK")
    else:
        print("  no token configured — skip auth check")

    print("\nV6 SMOKE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
