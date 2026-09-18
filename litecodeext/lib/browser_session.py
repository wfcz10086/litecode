"""
browser_session.py — Playwright Cookie 持久化 (P6-a)
======================================================
极速版: storage_state JSON 读写 + lazy import playwright.
"""
import json
import time
from pathlib import Path
from typing import Optional


def storage_path(workspace, domain: str) -> Path:
    p = Path(workspace) / "browser_profiles" / f"{domain}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_storage(workspace, domain: str, state: dict) -> Path:
    p = storage_path(workspace, domain)
    p.write_text(json.dumps({"_saved": time.time(), "state": state}, ensure_ascii=False, indent=2))
    return p


def load_storage(workspace, domain: str) -> Optional[dict]:
    p = storage_path(workspace, domain)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(errors="replace")).get("state")
    except Exception:
        return None


def list_domains(workspace) -> list:
    p = Path(workspace) / "browser_profiles"
    if not p.exists():
        return []
    return [f.stem for f in p.glob("*.json")]
