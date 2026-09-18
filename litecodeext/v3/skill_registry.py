"""V3 Skill registry — scan legacy `skills/*/SKILL.md`, register as `skill.<name>` nodes.

Zero change to the skill directory format. The registry provides:
  - list / enable / disable / reload
  - persistent enable state in litecode_state/skill_flags.json
  - auto-registration of each skill as a workflow node type

The `skill.<name>` node is a **prompt template** node: it feeds the skill's
markdown body into an LLM call with the user's input.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .workflow.registry import node_registry
from .workflow.spec import NodeContext

_BASE = Path(__file__).resolve().parent.parent.parent  # /opt/litecode
SKILLS_DIR = _BASE / "litecodeext" / "skills"
FLAGS_PATH = _BASE / "litecode_state" / "skill_flags.json"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


@dataclass
class SkillMeta:
    name: str
    description: str = ""
    body: str = ""
    path: Path | None = None
    frontmatter: dict = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "path": str(self.path) if self.path else None,
        }


def _parse_skill_md(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_raw, body = m.group(1), m.group(2)
    fm: dict = {}
    for line in fm_raw.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, body


class SkillRegistry:
    def __init__(self, skills_dir: Path = SKILLS_DIR, flags_path: Path = FLAGS_PATH) -> None:
        self._dir = Path(skills_dir)
        self._flags_path = Path(flags_path)
        self._skills: dict[str, SkillMeta] = {}
        self._flags: dict[str, bool] = self._load_flags()

    def _load_flags(self) -> dict[str, bool]:
        if self._flags_path.exists():
            try:
                return json.loads(self._flags_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_flags(self) -> None:
        self._flags_path.parent.mkdir(parents=True, exist_ok=True)
        self._flags_path.write_text(json.dumps(self._flags, ensure_ascii=False, indent=2), encoding="utf-8")

    def scan(self) -> int:
        """(Re)scan skills directory. Returns count of registered skills."""
        self._skills.clear()
        if not self._dir.exists():
            return 0
        for md in sorted(self._dir.glob("*/SKILL.md")):
            try:
                text = md.read_text(encoding="utf-8")
            except Exception:
                continue
            fm, body = _parse_skill_md(text)
            name = fm.get("name") or md.parent.name
            enabled = self._flags.get(name, True)
            meta = SkillMeta(
                name=name,
                description=fm.get("description", ""),
                body=body,
                path=md,
                frontmatter=fm,
                enabled=enabled,
            )
            self._skills[name] = meta
            self._ensure_node_registered(name)
        return len(self._skills)

    def _ensure_node_registered(self, name: str) -> None:
        node_type = f"skill.{name}"
        if node_type in node_registry.list():
            return

        async def _handler(ctx: NodeContext, _name: str = name) -> dict:
            meta = self._skills.get(_name)
            if meta is None or not meta.enabled:
                raise RuntimeError(f"skill {_name!r} not available (disabled or missing)")
            from .config_bridge import build_provider, list_models
            model_id = ctx.config.get("model") or (list_models()[0].get("id") if list_models() else None)
            if not model_id:
                raise RuntimeError("no LLM model available in config.json for skill execution")
            user_input = ctx.inputs.get("input") or ctx.inputs.get("prompt") or ""
            from .providers.base import ThinkingSpec, UnifiedMessage, UnifiedRequest
            messages = [
                UnifiedMessage(role="system", content=f"# Skill: {meta.name}\n\n{meta.body[:8000]}"),
                UnifiedMessage(role="user", content=str(user_input)),
            ]
            req = UnifiedRequest(
                model=model_id,
                messages=messages,
                thinking=ThinkingSpec(effort=ctx.config.get("effort", "off")),
                temperature=ctx.config.get("temperature", 0.2),
                max_tokens=ctx.config.get("max_tokens", 2048),
                stream=True,
            )
            provider = build_provider(model_id)
            text = ""
            async for ch in provider.stream(req):
                if ch.kind == "content" and ch.delta:
                    text += ch.delta
                elif ch.kind == "error":
                    raise RuntimeError(ch.delta or "skill execution failed")
                elif ch.kind == "done":
                    break
            return {"text": text.strip(), "out": text.strip(), "skill": _name}

        node_registry.register(
            node_type,
            inputs={"input": "str"},
            outputs={"text": "str"},
            description=self._skills[name].description,
        )(_handler)

    def list(self, *, only_enabled: bool = False) -> list[SkillMeta]:
        items = list(self._skills.values())
        if only_enabled:
            items = [s for s in items if s.enabled]
        return sorted(items, key=lambda s: s.name)

    def get(self, name: str) -> SkillMeta:
        if name not in self._skills:
            raise KeyError(f"skill {name!r} not found")
        return self._skills[name]

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._skills:
            raise KeyError(name)
        self._skills[name].enabled = enabled
        self._flags[name] = enabled
        self._save_flags()

    def as_json(self) -> list[dict]:
        return [s.to_dict() for s in self.list()]


skill_registry = SkillRegistry()
