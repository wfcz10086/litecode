# plugins/artifacts/store.py — 文件系统 artifact 仓库 (S1-N 基础层)
#
# 存储布局:
#   {root}/
#     {owner_safe}/
#       {aid}.bin           payload
#       {aid}.json          metadata (owner/kind/name/size/ts/tags)
#
# owner_safe: 冒号和斜杠替换成下划线; 例 "plugin:cad_generate:abc" → "plugin_cad_generate_abc"
# aid: 12 位 uuid hex, 全局唯一 (跨 owner 也不重).
#
# 线程/进程安全: 简单文件写入即可 (写入非原子, 但 aid 唯一, 读写不会串).
#
# 生命周期: 目前不做 GC. purge_owner() 一键删某 owner 全部产物;
# 后续如果磁盘满可加 TTL. 默认不设.

from __future__ import annotations
import os
import json
import time
import uuid
import re
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Union


_OWNER_UNSAFE = re.compile(r"[^A-Za-z0-9_\-.]")


def _safe(owner: str) -> str:
    s = _OWNER_UNSAFE.sub("_", owner or "unknown")
    # 折掉连续 . 防止 path traversal (.. → __)
    s = re.sub(r"\.\.+", lambda m: "_" * len(m.group(0)), s)
    return s[:120] or "unknown"


@dataclass
class ArtifactRecord:
    aid: str
    owner: str
    kind: str            # dxf / stl / pptx / png / log / txt / bytes ...
    name: str            # 人类可读名
    size: int
    ts: float
    tags: dict           # 任意 meta (name/pages/checksum 等)

    def to_public_dict(self) -> dict:
        d = asdict(self)
        d["ts_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.ts))
        return d


class ArtifactStore:
    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── 写 ─────────────────────────────────────
    def put(self,
            owner: str,
            kind: str,
            data: Union[bytes, str],
            name: str = "",
            meta: Optional[dict] = None) -> str:
        aid = uuid.uuid4().hex[:12]
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data or b"")
        odir = self.root / _safe(owner)
        odir.mkdir(parents=True, exist_ok=True)
        bin_p = odir / f"{aid}.bin"
        meta_p = odir / f"{aid}.json"

        rec = ArtifactRecord(
            aid=aid, owner=owner, kind=kind or "bytes",
            name=name or f"{aid}.{kind or 'bin'}",
            size=len(payload), ts=time.time(),
            tags=dict(meta or {}),
        )
        with self._lock:
            with open(bin_p, "wb") as f:
                f.write(payload)
            with open(meta_p, "w", encoding="utf-8") as f:
                json.dump(asdict(rec), f, ensure_ascii=False, indent=2)
        return aid

    # ── 读 ─────────────────────────────────────
    def meta(self, aid: str) -> Optional[ArtifactRecord]:
        for meta_p in self.root.glob(f"*/{aid}.json"):
            try:
                d = json.loads(meta_p.read_text(encoding="utf-8"))
                return ArtifactRecord(**d)
            except Exception:
                return None
        return None

    def blob(self, aid: str) -> Optional[bytes]:
        for bin_p in self.root.glob(f"*/{aid}.bin"):
            try:
                return bin_p.read_bytes()
            except Exception:
                return None
        return None

    def list_by_owner(self, owner: str) -> list[ArtifactRecord]:
        odir = self.root / _safe(owner)
        if not odir.is_dir():
            return []
        out = []
        for meta_p in odir.glob("*.json"):
            try:
                d = json.loads(meta_p.read_text(encoding="utf-8"))
                out.append(ArtifactRecord(**d))
            except Exception:
                continue
        return sorted(out, key=lambda r: r.ts, reverse=True)

    def list_all(self, prefix: Optional[str] = None,
                 limit: int = 200) -> list[ArtifactRecord]:
        out: list[ArtifactRecord] = []
        for meta_p in self.root.glob("*/*.json"):
            try:
                d = json.loads(meta_p.read_text(encoding="utf-8"))
                if prefix and not (d.get("owner", "").startswith(prefix)):
                    continue
                out.append(ArtifactRecord(**d))
            except Exception:
                continue
        out.sort(key=lambda r: r.ts, reverse=True)
        return out[:limit]

    def owners(self) -> list[str]:
        seen: set[str] = set()
        for meta_p in self.root.glob("*/*.json"):
            try:
                d = json.loads(meta_p.read_text(encoding="utf-8"))
                if d.get("owner"):
                    seen.add(d["owner"])
            except Exception:
                continue
        return sorted(seen)

    # ── 删 ─────────────────────────────────────
    def delete(self, aid: str) -> bool:
        removed = False
        for p in list(self.root.glob(f"*/{aid}.bin")) + list(self.root.glob(f"*/{aid}.json")):
            try:
                p.unlink()
                removed = True
            except Exception:
                pass
        return removed

    def purge_owner(self, owner: str) -> int:
        odir = self.root / _safe(owner)
        if not odir.is_dir():
            return 0
        n = 0
        for p in list(odir.glob("*")):
            try:
                p.unlink(); n += 1
            except Exception:
                pass
        try:
            odir.rmdir()
        except Exception:
            pass
        return n


# ── 全局单例 ─────────────────────────────────────
_STORE: Optional[ArtifactStore] = None
_STORE_LOCK = threading.Lock()

DEFAULT_ROOT_ENV = "LITECODE_ARTIFACT_ROOT"
DEFAULT_ROOT = "/tmp/litecode_artifacts"


def get_store(root: Optional[Union[str, Path]] = None) -> ArtifactStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            r = root or os.environ.get(DEFAULT_ROOT_ENV) or DEFAULT_ROOT
            _STORE = ArtifactStore(r)
        return _STORE


def reset_store_for_test():
    global _STORE
    with _STORE_LOCK:
        _STORE = None
