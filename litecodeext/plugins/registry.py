# plugins/registry.py — 机架扫描器 + 动态注册表
#
# 扫描来源:
#   1. 内部:  litecodeext/plugins/tools/*.py (跟着 LiteCode 走的核心工具)
#   2. 外部:  环境变量 LITECODE_PLUGIN_PATH="/opt/cad:/opt/pptx" 里每个目录下的 plugin.py
#             或子目录 plugins/*.py
#
# 使用:
#   from plugins.registry import registry
#   registry.scan()                   # 启动时调一次
#   spec = registry.get("web_search")
#   result = await spec.run(args, ctx)
#   for spec in registry.list():
#       ...
#
# 卸载:
#   unset LITECODE_PLUGIN_PATH → 下次 scan() 时该外部插件消失
#   registry.reload() 触发热重扫

from __future__ import annotations
import os
import sys
import importlib.util
import logging
from pathlib import Path
from typing import Optional, Iterable

try:
    from ._contract import PluginSpec, validate_module, specs_from_module, PluginContractError
except ImportError:
    from _contract import PluginSpec, validate_module, specs_from_module, PluginContractError  # type: ignore

log = logging.getLogger("plugins.registry")

# 环境变量约定
ENV_PATH = "LITECODE_PLUGIN_PATH"
INTERNAL_TOOLS_DIR = Path(__file__).parent / "tools"


class Registry:
    def __init__(self) -> None:
        self._plugins: dict[str, PluginSpec] = {}
        self._load_errors: list[tuple[str, str]] = []   # (path, err)

    # ── 查询 ─────────────────────────────────────────
    def get(self, name: str) -> Optional[PluginSpec]:
        return self._plugins.get(name)

    def list(self) -> list[PluginSpec]:
        return list(self._plugins.values())

    def names(self) -> set[str]:
        return set(self._plugins.keys())

    def openai_tools(self) -> list[dict]:
        return [s.to_openai_tool() for s in self._plugins.values() if not s.load_error]

    def load_errors(self) -> list[tuple[str, str]]:
        return list(self._load_errors)

    # ── 扫描 ─────────────────────────────────────────
    def scan(self) -> int:
        """全量重扫. 返回加载成功的插件数量."""
        self._plugins.clear()
        self._load_errors.clear()

        for p in self._internal_candidates():
            self._try_load(p, origin="internal")

        for p in self._external_candidates():
            self._try_load(p, origin="external")

        log.info("plugin scan complete: %d ok, %d error", len(self._plugins), len(self._load_errors))
        return len(self._plugins)

    reload = scan

    def _internal_candidates(self) -> Iterable[Path]:
        if not INTERNAL_TOOLS_DIR.is_dir():
            return
        for f in sorted(INTERNAL_TOOLS_DIR.glob("*.py")):
            if f.name.startswith("_"):
                continue
            yield f

    def _external_candidates(self) -> Iterable[Path]:
        raw = os.environ.get(ENV_PATH, "").strip()
        if not raw:
            return
        for token in raw.split(":"):
            token = token.strip()
            if not token:
                continue
            root = Path(token)
            if not root.is_dir():
                log.warning("plugin path missing: %s", token)
                continue
            single = root / "plugin.py"
            if single.is_file():
                yield single
            sub = root / "plugins"
            if sub.is_dir():
                for f in sorted(sub.glob("*.py")):
                    if f.name.startswith("_"):
                        continue
                    yield f

    def _try_load(self, path: Path, origin: str) -> None:
        try:
            mod_name = f"_lc_plugin_{path.stem}_{abs(hash(str(path))) & 0xFFFFFFFF:x}"
            spec = importlib.util.spec_from_file_location(mod_name, str(path))
            if spec is None or spec.loader is None:
                raise PluginContractError("cannot build importlib spec")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        except Exception as e:
            self._load_errors.append((str(path), f"import failed: {e!r}"))
            log.exception("plugin import failed: %s", path)
            return

        # 未声明 NAME 也未声明 TOOLS → 辅助模块 (如 guard.py), 静默跳过
        if not hasattr(mod, "NAME") and not hasattr(mod, "TOOLS"):
            log.debug("skipping helper module (no NAME/TOOLS): %s", path)
            return

        ok, err = validate_module(mod)
        if not ok:
            self._load_errors.append((str(path), f"contract: {err}"))
            log.warning("plugin contract violated at %s: %s", path, err)
            return

        specs = specs_from_module(mod, str(path), origin)
        for pspec in specs:
            if pspec.name in self._plugins:
                existing = self._plugins[pspec.name]
                log.warning(
                    "plugin name collision: %s already at %s, ignoring %s",
                    pspec.name, existing.source_path, path,
                )
                self._load_errors.append((str(path), f"name collision: {pspec.name} taken by {existing.source_path}"))
                continue
            self._plugins[pspec.name] = pspec

    # ── 调用 ─────────────────────────────────────────
    async def call(self, name: str, args: dict, ctx: dict) -> dict:
        spec = self.get(name)
        if not spec:
            return {"ok": False, "error": f"plugin not found: {name}"}
        ctx = dict(ctx or {})
        # 注入 emit_artifact (owner-scoped, plugin 用完即写)
        if "emit_artifact" not in ctx:
            try:
                from .artifacts import build_emit_artifact, new_plugin_owner
            except ImportError:
                from artifacts import build_emit_artifact, new_plugin_owner  # type: ignore
            owner = ctx.get("owner") or new_plugin_owner(name, ctx.get("job_id"))
            ctx["owner"] = owner
            ctx["emit_artifact"] = build_emit_artifact(owner)
        try:
            result = await spec.run(args or {}, ctx)
        except Exception as e:
            log.exception("plugin %s crashed", name)
            return {"ok": False, "error": f"{name} crashed: {e!r}"}
        return result if isinstance(result, dict) else {"ok": True, "result": result}


# 全局单例 — 让业务代码 import 一处
registry = Registry()


def scan_once_if_empty() -> None:
    """启动路径的懒扫描 — 不重复扫."""
    if not registry.names():
        registry.scan()
