"""lib/key_store.py — 控制端私钥目录 (放在工作区之外).

[sec 2026-10-19] Windows 节点的代码签名私钥 (signing_key.pem) 与 shell 能力 HMAC 密钥
(capability.key.bin) 以前放在 WORKSPACE/.secrets/windows_nodes/, 模型的文件/命令工具都以工作区
为主场, 被注入的模型可以读走私钥给能力开关重签、启用 run_powershell。现在挪到工作区之外:

    $LITECODE_KEY_DIR (默认 ~/.litecode/keys) / <工作区路径摘要> / windows_nodes/

- 按工作区路径摘要分子目录: 测试里每个临时工作区各有一套密钥, 互不串用。
- 一次性迁移: 新目录还没有某个密钥文件而旧位置有 → 原样 move 过去 (字节不变, 节点继续认同一把公钥)。
- 持久化保护: 容器里如果新目录不是挂载点 (docker-compose 还没加 ./litecode_state/keys 卷, 重建
  容器就会丢), 且未显式设置 LITECODE_KEY_DIR, 则暂不迁移、继续用旧位置并告警一次 —— 宁可晚一点
  生效也不能把密钥挪进会随容器重建消失的目录。

残余风险: 容器内 execute_shell 以 root 运行, 仍能主动 find 到这些文件; 这里是纵深防御
(配合 core/executor_v4 拒绝引用密钥目录的命令), 不是强隔离。
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

WINDOWS_KEY_FILES = ("signing_key.pem", "capability.key.bin")
_warned = set()


def key_base() -> Path:
    env = os.environ.get("LITECODE_KEY_DIR", "").strip()
    return Path(env) if env else Path.home() / ".litecode" / "keys"


def _persistent(base: Path) -> bool:
    if os.environ.get("LITECODE_KEY_DIR", "").strip():
        return True                      # 运维显式指定, 视为已持久化
    if not Path("/.dockerenv").exists():
        return True                      # 非容器 (宿主机/测试) 目录本身就持久
    try:
        return os.path.ismount(base)
    except OSError:
        return False


def legacy_windows_dir(workspace) -> Path:
    return Path(workspace) / ".secrets" / "windows_nodes"


def windows_key_dir(workspace) -> Path:
    """返回存放 Windows 控制端密钥的目录 (必要时完成一次性迁移)。"""
    ws = Path(workspace)
    legacy = legacy_windows_dir(ws)
    base = key_base()
    if not _persistent(base):
        if str(base) not in _warned:
            _warned.add(str(base))
            log.warning("密钥目录 %s 不是持久化挂载, 暂留旧位置 %s; docker-compose 加卷 "
                        "./litecode_state/keys:/root/.litecode/keys 并重建容器后自动迁移", base, legacy)
        return legacy
    digest = hashlib.sha256(str(ws.resolve()).encode("utf-8")).hexdigest()[:16]
    folder = base / digest / "windows_nodes"
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        for p in (base, base / digest, folder):
            try:
                os.chmod(p, 0o700)
            except OSError:
                pass
        for name in WINDOWS_KEY_FILES:
            old, new = legacy / name, folder / name
            if old.is_file() and not new.exists():
                shutil.move(str(old), str(new))
                os.chmod(new, 0o600)
                log.warning("已把 %s 迁移到工作区外: %s", name, new)
    except OSError as e:  # 新目录不可写 → 不中断业务, 退回旧位置
        log.warning("密钥目录 %s 不可用 (%s), 退回旧位置 %s", folder, e, legacy)
        return legacy
    return folder
