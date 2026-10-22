#!/usr/bin/env python3
"""
wechat_bridge.py — 微信 Bot ↔ LiteCode Agent 桥接模块（wechatbot-sdk 原生版）

功能:
  • 基于 wechatbot-sdk 原生扫码登录（不再使用自定义 iLink HTTP 调用）
  • 收到微信消息 → 解析（含引用/转发/媒体占位符）→ 调用 litecode_server → 回复微信
  • 语音自动转文字 | 文件/图片 → 占位符描述
  • 保活机制：定期发送保活消息防止 24h token 过期
  • 聊天记录写入 web_sessions，web 端可见
  • Tool call 感知：流式消费 SSE stream，保持 typing 状态

依赖:
  pip install wechatbot-sdk aiohttp
"""

import asyncio
import json
import logging
import os
import random
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Optional, Dict, List, Any

import aiohttp

from lib.wechat_media import _sdk_download_media
from lib.wechat_attach import (
    _try_whisper_transcribe, _main_model_supports_vision, _image_to_data_url,
    _preprocess_attachment,
)
from lib.wechat_format import _split_message, _format_wx_reply

# ── [拆分 2026-10-16] 公共常量/配置/会话、消息解析、WeChatBotInstance 各职责方法
# 已搬到 lib/wechat/ (容器只单文件挂载本文件, lib/ 整目录挂载)。这里全部 re-export,
# 保持 `from wechat_bridge import xxx` 的外部用法不变。
from lib.wechat.common import (
    log, _WX_GREETINGS, _random_wx_greeting, _SDK_AVAILABLE,
    _BASE, _DEFAULT_KEEPALIVE_HOURS, _DEFAULT_KEEPALIVE_MSG,
    _DEFAULT_SEND_INTERVAL, _TYPING_REFRESH_INTERVAL,
    _IMG_RE, _SYSTEM_LEAK_RE, _filter_system_leak,
    _load_cfg, _save_cfg, _wx_cfg,
    _SESSIONS_DIR, _sf, _load_web_session, _save_web_session,
)
if _SDK_AVAILABLE:
    from lib.wechat.common import WeChatBot
from lib.wechat.extract import _extract_message_full
from lib.wechat.lifecycle import WeChatLifecycleMixin
from lib.wechat.message import WeChatMessageMixin
from lib.wechat.agent_call import WeChatAgentCallMixin


# ══════════════════════════════════════════════════════════════
# 单个微信 Bot 实例（基于 wechatbot-sdk 原生 API）
# ══════════════════════════════════════════════════════════════
class WeChatBotInstance(WeChatLifecycleMixin, WeChatMessageMixin, WeChatAgentCallMixin):
    """
    封装单个微信号的完整生命周期。
    使用 wechatbot-sdk 原生 WeChatBot 类：
      - 扫码登录由 SDK 内部完成（on_qr_url 回调获取二维码）
      - 消息轮询由 SDK 的 bot.start() 驱动
      - 发送用 bot.send() / bot.reply()
      - context_token 由 SDK 自动管理（~/.wechatbot/context_store.json）
    """

    def __init__(self, bot_id: str, manager: "WeChatBridgeManager"):
        self.bot_id = bot_id
        self.manager = manager

        # 状态
        self.status: str = "offline"       # offline / wait_scan / scanned / need_code / online / expired
        self.qr_url: Optional[str] = None

        # SDK 实例
        self._bot: Optional["WeChatBot"] = None
        self._run_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None

        # 统计 & 用户追踪
        self.msg_count: int = 0
        self.last_active: float = 0
        self.connected_users: Dict[str, dict] = {}   # {user_id: {name, last_ts}}

        # 用户最后交互时间（保活用）
        self._user_last_ts: Dict[str, float] = {}
        self._user_keepalive_sent: Dict[str, bool] = {}

        # 图片累计缓冲：用户发多张图时先存，等文字一起处理
        self._pending_images: Dict[str, list] = {}  # uid -> [local_path, ...]

        # 防止同一用户并发处理（消息排队）
        self._user_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        # [v1.1] 每个用户当前在跑的处理任务 — 新消息到达时可抢占取消旧任务,
        # 避免 "上一个请求卡死 10min, 下一个请求干等" 的情况
        self._user_tasks: Dict[str, asyncio.Task] = {}

        # 微信账号信息（登录成功后填充）
        self.wx_account: str = ""          # account_id from Credentials
        self.wx_display: str = ""          # 用于显示的短名称

        # 错误追踪
        self._last_error: Optional[str] = None
        self._last_hint: Optional[str] = None


# ══════════════════════════════════════════════════════════════
# 全局管理器
# ══════════════════════════════════════════════════════════════
class WeChatBridgeManager:
    """管理微信 Bot 实例，对接 web_ui.py 的 API 路由"""

    def __init__(self):
        self.bots: Dict[str, WeChatBotInstance] = {}
        self._started = False

    @property
    def send_interval(self) -> float:
        cfg = _wx_cfg()
        return cfg.get("send_interval", _DEFAULT_SEND_INTERVAL)

    # ── Bot 状态持久化 ─────────────────────────────────────────
    _BOTS_STATE_FILE = Path.home() / ".wechatbot" / "bots_state.json"

    def _save_bots_state(self):
        """把当前所有 bot_id 保存到磁盘，重启后用于自动恢复"""
        try:
            self._BOTS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            state = {"bot_ids": list(self.bots.keys()), "saved_at": time.time()}
            self._BOTS_STATE_FILE.write_text(
                json.dumps(state, ensure_ascii=False, indent=2)
            )
        except Exception as e:
            log.warning(f"[wechat_bridge] 保存 bot 状态失败: {e}")

    def _load_bots_state(self) -> list:
        """读取上次保存的 bot_id 列表；若不存在则扫描凭证文件自动发现"""
        try:
            if self._BOTS_STATE_FILE.exists():
                state = json.loads(self._BOTS_STATE_FILE.read_text())
                ids = state.get("bot_ids", [])
                if ids:
                    log.info(f"[wechat_bridge] 从状态文件恢复 bot: {ids}")
                    return ids
        except Exception as e:
            log.warning(f"[wechat_bridge] 读取 bot 状态失败: {e}")

        # 兜底：扫描 ~/.wechatbot/*_creds.json 自动发现
        cred_dir = Path.home() / ".wechatbot"
        bot_ids = []
        if cred_dir.exists():
            for cred_file in sorted(cred_dir.glob("*_creds.json")):
                bot_id = cred_file.stem.replace("_creds", "")
                if bot_id and bot_id not in ("bots_state",):
                    bot_ids.append(bot_id)
        if bot_ids:
            log.info(f"[wechat_bridge] 扫描凭证文件发现 bot: {bot_ids}")
        return bot_ids

    async def startup(self):
        """FastAPI startup 时调用 —— 自动恢复上次的 Bot 实例"""
        if self._started:
            return
        self._started = True

        if not _SDK_AVAILABLE:
            log.warning("[wechat_bridge] wechatbot-sdk 未安装，微信桥接不可用")
            return

        cfg = _wx_cfg()
        if not cfg.get("enabled", False):
            log.info("[wechat_bridge] 微信桥接未启用（可在 Web UI → WeChat 面板中开启）")
            return

        # 自动恢复：读取上次保存的 bot_id，用已有凭证登录（无需重新扫码）
        bot_ids = self._load_bots_state()
        if not bot_ids:
            log.info("[wechat_bridge] 无已保存的 Bot，等待用户添加")
            return

        log.info(f"[wechat_bridge] 自动恢复 {len(bot_ids)} 个 Bot: {bot_ids}")
        for bot_id in bot_ids:
            cred_file = Path.home() / ".wechatbot" / f"{bot_id}_creds.json"
            if not cred_file.exists():
                log.warning(f"[wechat_bridge] 凭证不存在，跳过恢复: {bot_id}")
                continue
            try:
                bot = WeChatBotInstance(bot_id, self)
                self.bots[bot_id] = bot
                # 后台启动，不阻塞 startup；SDK 用缓存凭证，无需扫码
                asyncio.create_task(bot.login())
                log.info(f"[wechat_bridge] ✅ 已提交恢复任务: {bot_id}")
            except Exception as e:
                log.error(f"[wechat_bridge] 恢复 Bot {bot_id} 失败: {e}")

    async def add_bot(self, bot_id: str = None) -> dict:
        """添加新微信实例并开始登录"""
        if not _SDK_AVAILABLE:
            return {"ok": False, "error": "wechatbot-sdk 未安装",
                    "hint": "pip install wechatbot-sdk"}

        if not bot_id:
            bot_id = f"wx{uuid.uuid4().hex[:6]}"

        if bot_id in self.bots:
            bot = self.bots[bot_id]
            if bot.status == "online":
                return {"ok": True, "bot_id": bot_id, "status": "already_online"}
            result = await bot.login()
            return {"bot_id": bot_id, **result}

        bot = WeChatBotInstance(bot_id, self)
        self.bots[bot_id] = bot
        result = await bot.login()
        self._save_bots_state()   # 持久化
        return {"bot_id": bot_id, **result}

    async def remove_bot(self, bot_id: str) -> dict:
        """移除微信实例"""
        bot = self.bots.pop(bot_id, None)
        if bot:
            await bot.shutdown()
            self._save_bots_state()   # 持久化
            return {"ok": True}
        return {"ok": False, "error": "not found"}

    async def relogin(self, bot_id: str) -> dict:
        """重新登录（重新获取二维码）"""
        bot = self.bots.get(bot_id)
        if not bot:
            return {"ok": False, "error": "not found"}
        if bot.status == "online":
            await bot.shutdown()
        result = await bot.login()
        return {"bot_id": bot_id, **result}

    def submit_verify_code(self, bot_id: str, code: str) -> dict:
        """网页提交手机微信上显示的配对码 (wechatbot-sdk 0.3.0 扫码登录多出的一步)"""
        bot = self.bots.get(bot_id)
        if not bot:
            return {"ok": False, "error": "not found"}
        return bot.submit_verify_code(code)

    def list_bots(self) -> List[dict]:
        return [bot.info() for bot in self.bots.values()]

    def get_config(self) -> dict:
        cfg = _wx_cfg()
        return {
            "enabled": cfg.get("enabled", False),
            "keepalive_hours": cfg.get("keepalive_hours", _DEFAULT_KEEPALIVE_HOURS),
            "keepalive_message": cfg.get("keepalive_message", _DEFAULT_KEEPALIVE_MSG),
            "send_interval": cfg.get("send_interval", _DEFAULT_SEND_INTERVAL),
            "vision_model": cfg.get("vision_model", ""),
            "sdk_available": _SDK_AVAILABLE,
        }

    def update_config(self, updates: dict) -> dict:
        cfg = _load_cfg()
        wx = cfg.setdefault("wechat", {})
        for k in ("enabled", "keepalive_hours", "keepalive_message",
                   "send_interval", "vision_model"):
            if k in updates:
                wx[k] = updates[k]
        _save_cfg(cfg)
        return self.get_config()

    async def shutdown_all(self):
        for bot in self.bots.values():
            await bot.shutdown()


# ── 全局单例 ──────────────────────────────────────────────────
_manager: Optional[WeChatBridgeManager] = None


def get_manager() -> WeChatBridgeManager:
    global _manager
    if _manager is None:
        _manager = WeChatBridgeManager()
    return _manager
