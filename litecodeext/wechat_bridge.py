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

log = logging.getLogger("wechat_bridge")

# ── 首条消息随机问候语 ─────────────────────────────────────────
_WX_GREETINGS = [
    "你好！有什么可以帮你的？",
    "嗨，你好！请问有什么需要帮忙的吗？",
    "你好，很高兴认识你！有什么想聊的？",
    "你好！请说，我在听。",
    "嗨！今天有什么需要我协助的？",
    "你好！随时告诉我需要什么帮助。",
    "你好，欢迎！有问题直接问我就行。",
    "嗯，你好！有什么我能帮上忙的？",
]

def _random_wx_greeting() -> str:
    return random.choice(_WX_GREETINGS)

# ── 尝试导入 wechatbot-sdk ────────────────────────────────────
try:
    from wechatbot import WeChatBot
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False
    log.warning("wechatbot-sdk 未安装，请执行: pip install wechatbot-sdk")

# ── 常量 ───────────────────────────────────────────────────────
_BASE = Path(__file__).parent
_DEFAULT_KEEPALIVE_HOURS = 22
_DEFAULT_KEEPALIVE_MSG = "🤖 会话即将过期，回复任意消息即可续期~"
_DEFAULT_SEND_INTERVAL = 1.5          # 秒，两条消息之间最小间隔
_TYPING_REFRESH_INTERVAL = 8          # 每 8 秒刷新一次 typing 状态

# ── Base64 内嵌图片识别（Agent截图回复）──────────────────────
_IMG_RE = re.compile(
    r'!\[([^\]]*)\]\(data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\s]{20,})\)',
    re.DOTALL
)

# [2026-08-25 P0-1] 内部控制消息泄漏过滤 — litecode_server.py 会往 content 流里注入
# [系统: ...] / [系统提示...] / [SYSTEM...] / [SYSTEM-XXX] 这类给模型自己看的调试/纠偏
# 提示 (推理超限中断、死循环检测、强制行动提醒等), Web 前端会特殊渲染这些, 但微信是纯文本
# 直通, 不过滤的话真实微信用户会收到这些内部调试语法. 逐行匹配 (这些消息构造时都是单行,
# 前后带 \n), 匹配到就整行剔除.
_SYSTEM_LEAK_RE = re.compile(r'\[(?:系统|SYSTEM)[^\]\n]*\][^\n]*\n?')


def _filter_system_leak(text: str) -> str:
    """剔除文本里泄漏的内部 [系统:...]/[SYSTEM...] 控制消息, 不影响其余正文."""
    if not text:
        return text
    return _SYSTEM_LEAK_RE.sub("", text).strip()


# ── 配置加载 ──────────────────────────────────────────────────
def _load_cfg() -> dict:
    p = _BASE / "config.json"
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _save_cfg(cfg: dict):
    p = _BASE / "config.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=4))


def _wx_cfg(cfg: dict = None) -> dict:
    if cfg is None:
        cfg = _load_cfg()
    return cfg.get("wechat", {})


# ── Web Session 读写（复用 web_ui 的格式）─────────────────────
_SESSIONS_DIR = Path.home() / ".litecode" / "web_sessions"
_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _sf(sid: str) -> Path:
    return _SESSIONS_DIR / f"{sid}.json"


def _load_web_session(sid: str) -> dict:
    f = _sf(sid)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {
        "id": sid, "name": "WeChat", "created": time.time(),
        "last_used": time.time(), "messages": [], "source": "wechat"
    }


def _save_web_session(d: dict):
    tmp = _sf(d["id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    tmp.replace(_sf(d["id"]))


# ══════════════════════════════════════════════════════════════
# 消息内容解析（参考 bot.py 的 parse_message_content）
# ══════════════════════════════════════════════════════════════
async def _extract_message_full(msg, bot_id: str = "", bot=None) -> Optional[str]:
    """
    提取消息内容：
    - 文字 → 直接用
    - 图片/文件/语音/视频 → 用 SDK bot.download_raw(media, aes_key) 下载
      ⚠️  微信 CDN 有 encrypt_query_param 加密参数，必须走 SDK，不能裸 HTTP GET
    - 语音 → 尝试 Whisper 转写
    - 转发聊天记录 → 解析 XML 文本
    """
    parts = []

    # ── 主文本 ──
    text = (getattr(msg, "text", "") or "").strip()
    if text:
        parts.append(text)

    # ── 图片 ──
    img_paths = []
    for img in getattr(msg, "images", []):
        local = await _sdk_download_media(
            bot, img, ext="jpg", bot_id=bot_id,
            fallback_url=getattr(img, "url", "") or ""
        )
        if local:
            img_paths.append(local)
        else:
            parts.append("[图片下载失败]")
    if img_paths:
        parts.append("[收到图片]\n" + "\n".join(f"图片路径: {p}" for p in img_paths))

    # ── 语音 ──
    for v in getattr(msg, "voices", []):
        duration = getattr(v, "duration_ms", 0)
        transcript = (getattr(v, "text", "") or "").strip()
        if transcript:
            parts.append(f"[语音 {duration//1000}s 转写]: {transcript}")
            continue
        local = await _sdk_download_media(
            bot, v, ext="silk", bot_id=bot_id,
            fallback_url=getattr(v, "url", "") or ""
        )
        if local:
            whisper_text = _try_whisper_transcribe(local, "")
            if whisper_text:
                parts.append(f"[语音 {duration//1000}s 转写]: {whisper_text}")
            else:
                parts.append(f"[语音已保存: {local}]（{duration//1000}s，转写失败）")
        else:
            parts.append(f"[语音 {duration//1000}s 下载失败]")

    # ── 文件 ──
    for f in getattr(msg, "files", []):
        fname = getattr(f, "file_name", "unknown")
        fsize = getattr(f, "size", 0)
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "bin"
        local = await _sdk_download_media(
            bot, f, ext=ext, filename=fname, bot_id=bot_id,
            fallback_url=getattr(f, "url", "") or ""
        )
        sz = f"{fsize//1024}KB" if fsize < 1048576 else f"{fsize//1048576}MB"
        if local:
            parts.append(f"[收到文件: {fname} ({sz})]\n文件路径: {local}")
        else:
            parts.append(f"[文件: {fname} ({sz}) 下载失败]")

    # ── 视频 ──
    for v in getattr(msg, "videos", []):
        local = await _sdk_download_media(
            bot, v, ext="mp4", bot_id=bot_id,
            fallback_url=getattr(v, "url", "") or ""
        )
        if local:
            parts.append(f"[收到视频]\n视频路径: {local}")
        else:
            parts.append("[视频下载失败]")

    # ── 引用 / 转发聊天记录 ──
    quoted = getattr(msg, "quoted_message", None)
    if quoted:
        title   = (getattr(quoted, "title",   "") or "").strip()
        content = (getattr(quoted, "content", "") or "").strip()
        raw_q   = getattr(quoted, "raw", {}) or {}
        record_lines = ["[转发聊天记录]"]
        if title:
            record_lines.append(f"  标题: {title}")
        if content:
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                line = re.sub(r"<img[^/]*/?>",               "[图片]",       line, flags=re.I)
                line = re.sub(r"<video[^/]*/?>",             "[视频]",       line, flags=re.I)
                line = re.sub(r"<voice[^/]*/?>",             "[语音]",       line, flags=re.I)
                line = re.sub(r"<attach[^>]*>(.*?)</attach>", r"[文件:\1]",  line, flags=re.I)
                line = re.sub(r"<[^>]+>", "",                 line)
                line = line.strip()
                if line:
                    record_lines.append(f"  {line}")
        for key in ("appname", "fromusername", "description", "des", "digest"):
            val = (raw_q.get(key, "") or "").strip()
            if val and val not in (title, content):
                record_lines.append(f"  [{key}] {val}")
        record_lines.append("[/转发聊天记录]")
        parts.append("\n".join(record_lines))

    if not parts:
        return None
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════
# 单个微信 Bot 实例（基于 wechatbot-sdk 原生 API）
# ══════════════════════════════════════════════════════════════
class WeChatBotInstance:
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
        self.status: str = "offline"       # offline / wait_scan / scanned / online / expired
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

    # ── 登录 ─────────────────────────────────────────────────
    async def login(self) -> dict:
        """创建 WeChatBot 实例并启动扫码登录（后台非阻塞）"""
        if not _SDK_AVAILABLE:
            self._last_error = "wechatbot-sdk 未安装"
            self._last_hint = "请执行: pip install wechatbot-sdk"
            return {"ok": False, "error": self._last_error, "hint": self._last_hint}

        # 停掉旧实例
        await self._stop_tasks()

        self.status = "wait_scan"
        self.qr_url = None
        self._last_error = None
        self._last_hint = None

        try:
            cred_dir = Path.home() / ".wechatbot"
            cred_dir.mkdir(parents=True, exist_ok=True)
            cred_path = str(cred_dir / f"{self.bot_id}_creds.json")
            self._bot = WeChatBot(
                cred_path  = cred_path,
                on_qr_url  = self._on_qr_url,
                on_scanned = self._on_scanned,
                on_expired = self._on_expired,
                on_error   = self._on_error,
            )

            # 注册消息处理器
            @self._bot.on_message
            async def _on_msg(incoming):
                await self._handle_message(incoming)

            # 后台启动登录 + 消息轮询（login 阻塞到扫码完成，start 阻塞轮询）
            self._run_task = asyncio.create_task(self._login_and_run())

            return {"ok": True, "status": "wait_scan"}

        except Exception as e:
            self.status = "offline"
            self._last_error = str(e)
            self._last_hint = "WeChatBot 初始化失败，请检查 wechatbot-sdk 版本"
            log.error(f"[wx:{self.bot_id}] 初始化失败: {e}")
            return {"ok": False, "error": str(e), "hint": self._last_hint}

    async def _login_and_run(self):
        """后台协程：login() 等待扫码确认 → start() 进入消息轮询"""
        try:
            log.info(f"[wx:{self.bot_id}] 等待扫码登录...")
            await self._bot.login()

            # login() 返回即表示扫码成功 — 读取账号信息
            try:
                creds = self._bot.get_credentials()
                if creds:
                    self.wx_account = creds.account_id or creds.user_id or ""
                    raw = self.wx_account.split("@")[0]
                    self.wx_display = raw[-12:] if len(raw) > 12 else raw
                    log.info(f"[wx:{self.bot_id}] account={self.wx_account}")
            except Exception as _ce:
                log.warning(f"[wx:{self.bot_id}] get_credentials error: {_ce}")
            self.status = "online"
            self._last_error = None
            self._last_hint = None
            log.info(f"[wx:{self.bot_id}] ✅ 登录成功 account={self.wx_display or self.bot_id}")

            # 启动保活循环
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

            # 进入消息轮询（阻塞直到断开）
            await self._bot.start()

        except asyncio.CancelledError:
            log.info(f"[wx:{self.bot_id}] 任务被取消")
        except Exception as e:
            err_str = str(e)
            log.error(f"[wx:{self.bot_id}] 运行异常: {e}")
            self._last_error = err_str
            if "expired" in err_str.lower() or "过期" in err_str or "-14" in err_str:
                self.status = "expired"
                self._last_hint = "会话已过期（errcode -14），请点击「重新登录」重新扫码"
                # 清除旧凭证
                cred_file = Path.home() / ".wechatbot" / f"{self.bot_id}_creds.json"
                if cred_file.exists():
                    try:
                        cred_file.unlink()
                        log.info(f"[wx:{self.bot_id}] cleared expired creds")
                    except Exception:
                        pass
            elif "404" in err_str or "unexpected mimetype" in err_str:
                # 旧凭证失效，清除后提示重登
                cred_file = Path.home() / ".wechatbot" / f"{self.bot_id}_creds.json"
                if cred_file.exists():
                    try:
                        cred_file.unlink()
                        log.info(f"[wx:{self.bot_id}] cleared stale creds on 404")
                    except Exception:
                        pass
                self.status = "offline"
                self._last_hint = "认证失败(404)，已清除旧凭证，请点击「重新登录」"
            elif "NoContext" in err_str:
                self._last_hint = "对方未发过消息，无法主动发送"
            else:
                self.status = "offline"
                self._last_hint = "请检查网络连接，或点击「重新登录」"

    # ── SDK 回调 ─────────────────────────────────────────────
    def _on_qr_url(self, url: str):
        """SDK 回调：二维码 URL 已就绪"""
        self.qr_url = url
        self.status = "wait_scan"
        self._last_error = None
        log.info(f"[wx:{self.bot_id}] 📱 二维码已生成，等待扫码")

    def _on_scanned(self):
        """SDK 回调：用户已扫码，等待手机确认"""
        self.status = "scanned"
        log.info(f"[wx:{self.bot_id}] ✅ 已扫码，等待确认")

    def _on_expired(self):
        """SDK 回调：二维码已过期"""
        self.status = "expired"
        self.qr_url = None
        self._last_hint = "二维码已过期，请点击「重新登录」"
        log.info(f"[wx:{self.bot_id}] ⏰ 二维码过期")

    def _on_error(self, err):
        """SDK 回调：发生错误"""
        err_str = str(err)
        self._last_error = err_str
        log.warning(f"[wx:{self.bot_id}] SDK error: {err}")
        # errcode -14 = 会话过期
        if "-14" in err_str or "expired" in err_str.lower():
            self.status = "expired"
            self._last_hint = "会话已过期，请点击「重新登录」重新扫码"
            cred_file = Path.home() / ".wechatbot" / f"{self.bot_id}_creds.json"
            if cred_file.exists():
                try:
                    cred_file.unlink()
                    log.info(f"[wx:{self.bot_id}] cleared expired creds: {cred_file}")
                except Exception:
                    pass
        # 404 / JSON mimetype 错误 = 旧凭证失效，清除后提示重新登录
        elif "404" in err_str or "unexpected mimetype" in err_str:
            cred_file = Path.home() / ".wechatbot" / f"{self.bot_id}_creds.json"
            if cred_file.exists():
                try:
                    cred_file.unlink()
                    log.info(f"[wx:{self.bot_id}] cleared stale creds: {cred_file}")
                except Exception:
                    pass
            self.status = "offline"
            self._last_hint = "认证失败(404)，已清除旧凭证，请点击「重新登录」"

    # ── 消息处理 ─────────────────────────────────────────────
    async def _handle_message(self, msg):
        """
        收到微信消息:
          - 不同用户完全并行 (各自独立 asyncio.Task)
          - 同一用户若旧任务还在跑 → 取消旧任务, 优先处理新消息
            (避免旧请求卡死导致新请求干等)
        """
        try:
            uid = getattr(msg, "user_id", "")
            if not uid:
                return

            # 群聊消息忽略
            if "@chatroom" in uid:
                log.debug(f"[wx:{self.bot_id}] 群聊消息忽略")
                return

            # [v1.1] 抢占式调度: 用户已有未完成任务 → 取消旧的
            old_task = self._user_tasks.get(uid)
            if old_task and not old_task.done():
                log.info(f"[wx:{self.bot_id}] 用户 {uid[:8]} 已有在跑任务, 取消旧任务处理新消息")
                old_task.cancel()
                try:
                    await asyncio.wait_for(old_task, timeout=3)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass

            # 每用户独立任务, 不同用户互不阻塞
            new_task = asyncio.create_task(self._safe_process(msg, uid))
            self._user_tasks[uid] = new_task

        except Exception as e:
            log.error(f"[wx:{self.bot_id}] handle_message error: {e}", exc_info=True)

    async def _safe_process(self, msg, uid: str):
        """包装 _process_message: 保证异常不污染 event loop, 完成后清理 task 引用"""
        try:
            # 同一用户内部仍用 lock 防止并发写 session 文件
            async with self._user_locks[uid]:
                await self._process_message(msg, uid)
        except asyncio.CancelledError:
            log.info(f"[wx:{self.bot_id}] 用户 {uid[:8]} 任务被取消 (新消息抢占)")
            raise
        except Exception as e:
            log.error(f"[wx:{self.bot_id}] _safe_process error for {uid[:8]}: {e}", exc_info=True)
        finally:
            # 只在引用仍指向当前任务时清理, 避免把新任务的引用擦掉
            cur = self._user_tasks.get(uid)
            if cur is asyncio.current_task():
                self._user_tasks.pop(uid, None)

    async def _process_message(self, msg, uid: str):
        """实际处理逻辑（在 user lock 内执行）"""
        msg_type = getattr(msg, "type", "text")

        # 获取发送者信息
        raw_text = (getattr(msg, "text", "") or "").strip()
        sender_name = uid[:8]
        sender_obj = getattr(msg, "sender", None)
        if sender_obj:
            sender_name = getattr(sender_obj, "nickname", None) or uid[:8]

        has_images  = bool(getattr(msg, "images", []))
        has_voices  = bool(getattr(msg, "voices", []))
        has_files   = bool(getattr(msg, "files",  []))
        has_videos  = bool(getattr(msg, "videos", []))
        has_quoted  = bool(getattr(msg, "quoted_message", None))

        # ── SDK 伪文本过滤 ─────────────────────────────────────────
        # 微信SDK在图片/文件等消息里把 text 设为 [image][文件]等占位符
        # 这些不是用户真实输入，不能触发AI处理
        _SDK_PLACEHOLDERS = {
            "[image]", "[图片]", "[文件]", "[视频]", "[语音]",
            "[表情]", "[动画表情]", "[小程序]", "[链接]",
            "[red packet]", "[红包]", "[转账]", "[位置]",
        }
        user_text = raw_text
        if user_text.lower() in _SDK_PLACEHOLDERS:
            user_text = ""
        # 形如 [xxx] 的单行括号内容也过滤（SDK生成）
        elif re.match(r'^\[[\w\s\u4e00-\u9fff]+\]$', user_text):
            user_text = ""

        has_real_text = bool(user_text)
        # 语音若已转写则算文字，否则算附件
        has_voice_no_text = has_voices and not any(
            (getattr(v, "text", "") or "").strip()
            for v in getattr(msg, "voices", [])
        )
        # 任何附件（图片/文件/视频/未转写语音）
        has_attachments = has_images or has_files or has_videos or has_voice_no_text

        log.info(f"[wx:{self.bot_id}] 来自 {sender_name} type={msg_type} "
                 f"text={raw_text[:60] or '(空)'} user_text={bool(user_text)} "
                 f"img={has_images} file={has_files} voice={has_voices} quoted={has_quoted}")

        # 更新用户记录
        self.connected_users[uid] = {"name": sender_name, "last_ts": time.time()}
        self._user_last_ts[uid] = time.time()
        self._user_keepalive_sent.pop(uid, None)
        self.last_active = time.time()
        self.msg_count += 1

        # ══════════════════════════════════════════════════════════
        # 核心逻辑：收到附件 → 只缓冲，不触发AI；收到文字 → 处理
        # ══════════════════════════════════════════════════════════

        # ── 有附件 且 没有真实用户文字 → 下载缓冲，回复确认，等文字 ──
        if has_attachments and not has_real_text and not has_quoted:
            attach_desc = []
            fail_count  = 0

            # 图片
            if has_images:
                for img in getattr(msg, "images", []):
                    local = await _sdk_download_media(
                        self._bot, img, ext="jpg", bot_id=self.bot_id
                    )
                    if local:
                        self._pending_images.setdefault(uid, []).append(local)
                        size_kb = Path(local).stat().st_size // 1024
                        attach_desc.append(f"图片 {size_kb}KB")
                    else:
                        fail_count += 1

            # 文件
            if has_files:
                for f_ in getattr(msg, "files", []):
                    local = await _sdk_download_media(
                        self._bot, f_, ext="bin", bot_id=self.bot_id,
                        filename=getattr(f_, "name", "")
                    )
                    if local:
                        self._pending_images.setdefault(uid, []).append(local)
                        fname = Path(local).name
                        attach_desc.append(f"文件 {fname}")
                    else:
                        fail_count += 1

            # 视频
            if has_videos:
                for vid in getattr(msg, "videos", []):
                    local = await _sdk_download_media(
                        self._bot, vid, ext="mp4", bot_id=self.bot_id
                    )
                    if local:
                        self._pending_images.setdefault(uid, []).append(local)
                        attach_desc.append("视频")
                    else:
                        fail_count += 1

            # 未转写语音
            if has_voice_no_text:
                for v_ in getattr(msg, "voices", []):
                    if (getattr(v_, "text", "") or "").strip():
                        continue   # 已转写语音走正常流程
                    local = await _sdk_download_media(
                        self._bot, v_, ext="silk", bot_id=self.bot_id
                    )
                    if local:
                        self._pending_images.setdefault(uid, []).append(local)
                        attach_desc.append("语音")
                    else:
                        fail_count += 1

            # 回复收到确认
            buffered = len(self._pending_images.get(uid, []))
            if attach_desc:
                desc_str = "、".join(attach_desc)
                confirm = f"✅ 已收到：{desc_str}（共 {buffered} 个）\n请发文字说明要做什么"
                if fail_count:
                    confirm += f"\n⚠️ {fail_count} 个下载失败，可重新发送"
            else:
                confirm = f"⚠️ 附件下载失败，请重新发送"
            try:
                await self._bot.reply(msg, confirm)
            except Exception:
                pass
            log.info(f"[wx:{self.bot_id}] 附件已缓冲 {buffered} 个，等待用户文字")
            return

        # ── 提取消息内容 ──
        user_content = await _extract_message_full(msg, bot_id=self.bot_id, bot=self._bot)
        if user_content is None:
            log.debug(f"[wx:{self.bot_id}] 空消息，保持沉默")
            return

        # ── 合并缓冲附件 ── 统一预处理 + [v1.4] 图片路径收集 ──
        image_paths_for_vision: list = []
        if uid in self._pending_images and self._pending_images[uid]:
            pending = self._pending_images.pop(uid)
            main_vision = _main_model_supports_vision()
            cfg = _load_cfg()
            total_budget = cfg.get("wechat", {}).get("attachment_total_budget", 6000)
            per_attach = max(500, total_budget // max(1, len(pending)))
            attach_parts = []
            preprocess_timeout = cfg.get("wechat", {}).get("preprocess_timeout", 90)
            for p in pending:
                # [v1.4] 主模型多模态时, 图片走多模态直传; 文字摘要仍保留 (做占位)
                if main_vision and Path(p).suffix.lower() in (
                    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"
                ):
                    image_paths_for_vision.append(p)
                try:
                    summary = await asyncio.wait_for(
                        _preprocess_attachment(p, max_chars=per_attach),
                        timeout=preprocess_timeout
                    )
                    attach_parts.append(summary)
                except asyncio.TimeoutError:
                    fname = Path(p).name
                    log.warning(f"[wx:{self.bot_id}] 附件预处理超时({preprocess_timeout}s): {fname}")
                    attach_parts.append(f"路径: {p}\n[预处理超时]: 文件较大，请用工具查看")
                except Exception as e:
                    log.warning(f"[wx:{self.bot_id}] 附件预处理异常 {p}: {e}")
                    attach_parts.append(f"路径: {p}\n[预处理失败]: {e}")
            attach_block = (
                f"[之前缓冲的附件 共{len(pending)}个]\n"
                + "\n---\n".join(attach_parts)
            )
            if len(attach_block) > total_budget + 500:
                attach_block = attach_block[:total_budget] + "\n... (内容已截断)"
            user_content = attach_block + "\n" + user_content
            log.info(f"[wx:{self.bot_id}] 合并 {len(pending)} 个附件 "
                     f"(文字 {len(attach_block)}字符, 直传图片 {len(image_paths_for_vision)} 张)")

        log.info(f"[wx:{self.bot_id}] → AI 输入: {user_content[:120].replace(chr(10), ' ')}")

        # 构造 web session ID — 用微信账号ID代替随机bot_id，清理特殊字符
        acct = self.wx_display or self.bot_id
        # uid 末尾可能含 @im.wechat，清理后只保留字母数字
        uid_safe = re.sub(r'[^a-zA-Z0-9]', '', uid)[-12:]
        session_id = f"wx-{acct}-{uid_safe}"

        # 写入用户消息到 web session
        ws = _load_web_session(session_id)
        is_first = len(ws["messages"]) == 0
        ws["messages"].append({
            "role": "user", "content": user_content,
            "ts": time.time(), "source": "wechat",
            "wx_user": sender_name,
        })
        ws["last_used"] = time.time()
        if is_first:
            # 格式：微信-账号 | 用户名: 消息预览
            ws["name"] = f"微信-{acct} | {sender_name}: {user_content[:25]}"
        _save_web_session(ws)

        # 显示"正在输入"
        try:
            await self._bot.send_typing(uid)
        except Exception:
            pass

        # 调用 LiteCode Agent（流式消费，感知 tool call）
        agent_result = await self._call_agent_stream(
            user_content, session_id, uid,
            image_paths=image_paths_for_vision,
        )

        reply_text = agent_result.get("text", "")
        if not reply_text:
            reply_text = "抱歉，AI 服务暂时不可用，请稍后再试。"

        wx_reply = _format_wx_reply(reply_text, agent_result)

        log.info(f"[wx:{self.bot_id}] <- AI: {reply_text[:100].replace(chr(10), ' ')}")

        # 写入 AI 回复到 web session
        ws = _load_web_session(session_id)
        ws["messages"].append({
            "role": "assistant", "content": reply_text,
            "ts": time.time(), "source": "wechat",
            "tools": agent_result.get("tools", []),
            "diffs": agent_result.get("diffs", []),
            "reasoning": agent_result.get("reasoning", ""),   # [v1.4] 推理持久化
            "usage": agent_result.get("usage"),
        })
        ws["last_used"] = time.time()
        _save_web_session(ws)

        # ── 分段发送回复（含多种图片协议拦截）─────────────────
        import base64 as _b64

        # ── 协议1: [发送图片: /path/to/file.png] ──────────────
        # Agent 把截图/生成图保存到本地文件后，用此标记主动发送到微信
        _SEND_IMG_RE = re.compile(r'\[发送图片[:：]\s*([^\]\n]+\.(png|jpg|jpeg|gif|webp|bmp))\]', re.I)
        _SEND_FILE_RE = re.compile(r'\[发送文件[:：]\s*([^\]\n]+)\]')

        send_img_paths = _SEND_IMG_RE.findall(wx_reply)   # [(path, ext), ...]
        send_file_paths = _SEND_FILE_RE.findall(wx_reply)  # [path, ...]

        # ── 协议2: ![alt](data:image/png;base64,...) ──────────
        # Agent 直接在回复里嵌入 base64（截图时自动触发）
        b64_images = _IMG_RE.findall(wx_reply)  # [(alt, ext, b64), ...]

        # 清理回复文本中的所有图片/文件标记
        clean_reply = wx_reply
        clean_reply = _SEND_IMG_RE.sub(lambda m: f"[图片: {Path(m.group(1)).name}]", clean_reply)
        clean_reply = _SEND_FILE_RE.sub(lambda m: f"[文件: {Path(m.group(1)).name}]", clean_reply)
        clean_reply = _IMG_RE.sub(lambda m: f"[截图: {m.group(1) or '图片'}]", clean_reply)
        clean_reply = clean_reply.strip()

        # ── [send-order fix 2026-08-28] 媒体先发, 文字后发 ────────────────
        # 原顺序是"文字 → 图片 → 文件"。实测问题:
        #   17:10:42 文字发出「✓ PDF 直接发你了」
        #   17:11:12 文件才真正发送完成 (876KB 上传耗时 30 秒)
        #   17:11:13 用户「文件我还是没收到啊 是不是报错了」
        # 用户在这 30 秒空窗里只看到"已发你"却没有文件, 自然以为报错。
        # 改成媒体先发: 文件/图片到达后才出现说明文字, 说明文字永远不会先于实物。
        # ── 发送本地路径图片（协议1）─────────────────────────
        for img_path, _ in send_img_paths:
            img_path = img_path.strip().strip('`"\'')
            try:
                fp = Path(img_path)
                if not fp.exists():
                    # 尝试在工作区下查找
                    cfg = _load_cfg()
                    ws = Path(cfg.get("paths", {}).get("workspace_base", "/tmp/litecode_workspace"))
                    alt = ws / fp.name
                    if alt.exists():
                        fp = alt
                if fp.exists():
                    img_bytes = fp.read_bytes()
                    await self._bot.reply_media(msg, {"image": img_bytes})
                    log.info(f"[wx:{self.bot_id}] ✅ 路径图片发送成功: {fp} ({len(img_bytes)//1024}KB)")
                else:
                    log.warning(f"[wx:{self.bot_id}] 路径图片不存在: {img_path}")
                    await self._bot.reply(msg, f"[图片文件不存在: {img_path}]")
            except Exception as e:
                log.warning(f"[wx:{self.bot_id}] 路径图片发送失败: {e}")

        # ── 发送本地路径文件（协议1扩展）────────────────────
        for file_path in send_file_paths:
            file_path = file_path.strip().strip('`"\'')
            try:
                fp = Path(file_path)
                if fp.exists():
                    file_bytes = fp.read_bytes()
                    await self._bot.reply_media(msg, {
                        "file": file_bytes,
                        "file_name": fp.name
                    })
                    log.info(f"[wx:{self.bot_id}] ✅ 文件发送成功: {fp.name} ({len(file_bytes)//1024}KB)")
                else:
                    log.warning(f"[wx:{self.bot_id}] 文件不存在: {file_path}")
            except Exception as e:
                log.warning(f"[wx:{self.bot_id}] 文件发送失败: {e}")

        # ── 发送 base64 内嵌图片（协议2）─────────────────────
        for alt, ext, b64_data in b64_images:
            try:
                img_bytes = _b64.b64decode(
                    b64_data.replace("\n", "").replace(" ", "").strip()
                )
                await self._bot.reply_media(msg, {"image": img_bytes})
                log.info(f"[wx:{self.bot_id}] ✅ base64图片发送成功: {alt} ({len(img_bytes)//1024}KB)")
            except Exception as img_e:
                log.warning(f"[wx:{self.bot_id}] base64图片发送失败: {img_e}")
                try:
                    await self._bot.reply(msg, "[图片发送失败，请在 Web UI 查看截图]")
                except Exception:
                    pass

        # ── 发送文字 ──────────────────────────────────────────
        chunks = _split_message(clean_reply)
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
            try:
                await self._bot.reply(msg, chunk)
            except Exception as e:
                log.warning(f"[wx:{self.bot_id}] reply 失败, 尝试 send: {e}")
                try:
                    await self._bot.send(uid, chunk)
                except Exception as e2:
                    log.error(f"[wx:{self.bot_id}] send 也失败: {e2}")
                    break
            if i < len(chunks) - 1:
                await asyncio.sleep(self.manager.send_interval)

        total_imgs = len(send_img_paths) + len(b64_images)
        log.info(f"[wx:{self.bot_id}] ✅ 回复完毕 ({len(chunks)}条文字, {total_imgs}张图, {len(send_file_paths)}个文件)")

    # ── 调用 Agent（流式消费 + typing 保持）───────────────────
    async def _call_agent_stream(self, user_msg: str, session_id: str,
                                  wx_user_id: str,
                                  image_paths: Optional[List[str]] = None) -> dict:
        """
        用 stream=True 调用 litecode_server：
        1. 实时消费 tool_call 事件
        2. 在 agent 执行期间持续刷新 typing 状态
        3. 最终拼出完整回复文本 + 工具调用详情

        [v1.4] image_paths: 主模型多模态时, 图片作为 data URL 直接塞进
        content 数组, 让模型看像素而不依赖二级视觉模型的文字描述 / OCR.
        """
        cfg = _load_cfg()
        srv = cfg.get("server", {})
        server_url = f"http://127.0.0.1:{srv.get('port', 18789)}"
        token = srv.get("token", "")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        # ── 微信格式提示（始终注入，简短）──
        _WX_FORMAT = (
            "\n[微信模式] 回复规则："
            "不用markdown表格(|列|)，用简洁文字列表。"
            "不用##标题。不用**加粗**。回复文字简短直接。"
            # [stop-halfway fix 2026-08-28] 原文只有「简短直接」, 模型把这个**排版要求**
            # 误读成了**任务范围要求** —— 干到一半就收工, 用户被迫反复发「继续」。
            # 实测证据: 微信会话 wx-b3607 多轮 TURN-END 停在 iter=1/2/4/7 (上限 500),
            # 且全程无中断/熔断/空回信号 —— 不是被机制掐断, 是模型自己认为干完了。
            # 该会话里用户连续发出「发我」「发我pdf」「文件我还是没收到」「继续补充」「继续」。
            # 故显式把「排版简短」和「任务做完」分开说。
            "\n注意：「简短」只约束排版，不约束做事。"
            "多步任务要一次做完再回复，不要做一半就停下等用户说「继续」。"
            "中间步骤不用逐步汇报，闷头做完，最后给一段简短结果即可。"
            "确实做不完时（缺信息/需要你决策），要明确说卡在哪、需要什么，"
            "不要停在半路不说原因。"
        )

        # ── 注入微信协议提示（兜底，确保Agent无论如何都能看到发图协议）──
        # 此提示很短，仅在消息含截图/图片/发送等关键词时注入，减少token浪费
        _WX_IMG_KEYWORDS = ("截图", "截屏", "screenshot", "图片", "发图", "拍照",
                            "发送图", "看看", "给我看", "show me", "take a screenshot",
                            "图文", "可视化", "图表", "chart", "graph", "图像")
        _needs_img_hint = any(kw in user_msg.lower() for kw in _WX_IMG_KEYWORDS)

        _WX_HINT = (
            "\n\n[系统提示-微信模式] 若需发图/截图给用户：\n"
            "① 保存到 /tmp/litecode_workspace/uploads/文件名.png\n"
            "② 回复中写 [发送图片: /tmp/litecode_workspace/uploads/文件名.png]\n"
            "③ 禁止只写「图片路径:」，那只是文字，图片不会发出去！"
        )
        enhanced_msg = user_msg + _WX_FORMAT + (_WX_HINT if _needs_img_hint else "")

        # [v1.4] 多模态: 图片 + 文字 组装成 OpenAI content array 给主模型直接看像素.
        # 纯文字时保持 string 格式 (vLLM/Qwen 对 str content 更稳).
        main_supports_vision = bool(cfg.get("model", {}).get("supports_vision", False))
        if image_paths and main_supports_vision:
            parts: list = []
            img_ok = 0
            for p in image_paths[:6]:   # 硬上限 6 张防 payload 爆
                data_url = _image_to_data_url(p)
                if data_url:
                    parts.append({"type": "image_url",
                                   "image_url": {"url": data_url}})
                    img_ok += 1
            parts.append({"type": "text", "text": enhanced_msg})
            user_content_payload: Any = parts
            log.info(f"[wx:{self.bot_id}] 多模态直传: {img_ok} 张图 + {len(enhanced_msg)} 字")
        else:
            user_content_payload = enhanced_msg

        # [2026-09-03] 权谋/多方博弈推演模式 —— 微信没有 chip, 用关键词触发。
        # 命中就给这一轮开推演 (STRATEGIST.md 注入): 拆多方利益/博弈/变量/正反面/情景,
        # 分析行情、AI泡沫、聊天截图感情博弈等。
        _WX_STRATEGIST_KW = ("推演", "权谋", "博弈", "谁得利", "谁在获利", "利益分析",
                             "帮我分析利益", "沙盘", "多方", "局势", "看穿", "背后逻辑")
        _wx_strategist = any(kw in user_msg for kw in _WX_STRATEGIST_KW)
        payload = {
            "model": cfg.get("model", {}).get("id", "openclaw"),
            "stream": True,
            "messages": [{"role": "user", "content": user_content_payload}],
            "user": session_id,
        }
        if _wx_strategist:
            payload["strategist_mode"] = True
            log.info(f"[wx:{self.bot_id}] 权谋推演模式已触发 (关键词命中)")

        result = {"text": "", "reasoning": "", "tools": [], "diffs": [], "usage": None}
        last_typing_ts = time.time()

        # [v1.1] 从 config 读取总超时, 不再硬编码 600s
        agent_cfg = cfg.get("agent", {})
        wx_cfg_local = cfg.get("wechat", {})
        # 优先用 wechat 专属超时 (给用户回复用, 可以比 agent 更激进),
        # 否则 fall back 到 agent.task_timeout_seconds
        total_timeout = int(
            wx_cfg_local.get("agent_call_timeout")
            or agent_cfg.get("task_timeout_seconds", 1800)
        )
        # [FIX] 短问 (如天气/时间/QA) 不该硬等 1800s — 长时间 sock_read=300 失联会导致
        # aiohttp TimeoutError 前, 用户早就不耐烦了。对 <60 字且无代码关键词的短问,
        # 走 short_agent_call_timeout (默认 180s), 让失败反馈更及时。
        _short_kw_heavy = ("写代码", "开发", "写个", "写一个", "生成代码", "实现",
                           "build", "implement", "写作", "小说", "报告", "批量")
        # [continue-timeout fix 2026-08-28] "短消息" ≠ "短任务"。
        # 实测: 用户发「继续」(2 字) → 命中短问模式 → 只给 180s, 而该轮 agent 正在跑
        # web_search / browser_search 深度调研, 被硬掐断 —— 7 轮工具调用全部
        # content=0c, 最后一轮还在调工具时回合就结束, 用户看到的是"发了继续也没做完"。
        # 这类"延续/推进"指令字数天然很少, 但延续的往往正是最重的那个任务。
        _CONTINUATION_KW = ("继续", "接着", "往下", "go on", "continue", "接下来",
                            "还有", "再来", "下一步", "补充", "完善", "重试", "再试")
        _is_continuation = any(kw in user_msg for kw in _CONTINUATION_KW)
        _is_short_query = (
            len(user_msg) < 60
            and not any(kw in user_msg for kw in _short_kw_heavy)
            and not _is_continuation          # 延续指令按长任务给足时间
            and not image_paths
        )
        if _is_short_query:
            total_timeout = int(
                wx_cfg_local.get("short_agent_call_timeout", 180)
            )
            log.debug(
                f"[wx:{self.bot_id}] 短问模式 timeout={total_timeout}s "
                f"(len={len(user_msg)})"
            )
        elif _is_continuation and len(user_msg) < 60:
            log.info(
                f"[wx:{self.bot_id}] 延续指令 ({user_msg[:12]!r}), "
                f"不走短问模式, timeout={total_timeout}s"
            )
        # 连接超时独立配置, 默认 15s
        connect_timeout = int(wx_cfg_local.get("agent_connect_timeout", 15))
        # [continue-timeout fix 2026-08-28] 见下方 ClientTimeout 注释。
        # _HARD_TOTAL_CAP: 兜底总上限, 防止极端情况无限挂 (agent 侧另有 max_iter 保护)。
        # _sock_read_timeout: "多久没收到任何 SSE 数据"才算卡死 —— 这才是真正该管的东西。
        # agent 每轮工具调用都会推送事件, 只要在干活就不会触发。
        _HARD_TOTAL_CAP = int(wx_cfg_local.get("agent_hard_total_cap", 3600))
        _sock_read_timeout = int(wx_cfg_local.get("agent_sock_read_timeout", 300))

        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    f"{server_url}/v1/chat/completions",
                    headers=headers, json=payload,
                    # [continue-timeout fix 2026-08-28] 原来用 total=total_timeout ——
                    # **硬性总时长上限**, 不管 agent 在不在干活, 到点就断。
                    # 配合"按消息字数猜任务轻重"的短问模式 (<60 字 → 180s), 造成实测故障:
                    #   「继续」/「你搜索一下X能不能上市」/「文件我还是没收到」这类短消息
                    #   触发的都是长任务 (web_search / browser / 排查), 180s 到点被掐,
                    #   表现为 5-9 轮工具调用后 content=0c、一个字都不回。
                    # 改成**按活动判定**: total 放宽到硬上限, 真正的保护交给 sock_read
                    # (SSE 流每次有数据就重置)。agent 在干活就一直等, 真卡死了
                    # sock_read 会兜住 —— 这是机械信号, 不用猜任务轻重。
                    timeout=aiohttp.ClientTimeout(
                        total=max(total_timeout, _HARD_TOTAL_CAP),
                        connect=connect_timeout,
                        sock_read=_sock_read_timeout,
                    )
                ) as resp:
                    if resp.status != 200:
                        err = await resp.text()
                        log.error(f"[wx:{self.bot_id}] agent HTTP {resp.status}: {err[:200]}")
                        result["text"] = f"(Agent 错误: HTTP {resp.status})"
                        return result

                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if "error" in chunk:
                            result["text"] = f"(Agent 错误: {chunk['error']})"
                            return result

                        delta = chunk.get("choices", [{}])[0].get("delta", {})

                        # 收集文本
                        ct = delta.get("content", "")
                        if ct:
                            result["text"] += ct
                        # [v1.4] 收集推理流, 持久化到 web session 后前端能展示折叠块
                        rz = delta.get("reasoning", "")
                        if rz:
                            result["reasoning"] += rz

                        # 收集 tool call 信息
                        for key in ("task_exec", "data_collect", "task_analysis"):
                            if key in delta and delta[key].get("status") == "executing":
                                det = delta[key].get("detail", "")
                                if det:
                                    result["tools"].append(det)

                        # 收集 diff
                        if "diff_view" in delta:
                            dv = delta["diff_view"]
                            if dv.get("diff"):
                                result["diffs"].append({
                                    "filepath": dv.get("filepath", ""),
                                    "diff": dv["diff"]
                                })

                        # 收集 usage
                        if "usage" in delta:
                            result["usage"] = delta["usage"]

                        # 定期刷新 typing（agent 可能跑很久）
                        now = time.time()
                        if now - last_typing_ts > _TYPING_REFRESH_INTERVAL:
                            try:
                                await self._bot.send_typing(wx_user_id)
                            except Exception:
                                pass
                            last_typing_ts = now

        except asyncio.TimeoutError:
            log.error(f"[wx:{self.bot_id}] agent 超时")
            result["text"] = result["text"] or "(Agent 响应超时)"
        except aiohttp.ClientConnectorError:
            log.error(f"[wx:{self.bot_id}] 无法连接 Agent Server")
            result["text"] = "(Agent 服务不可用，请检查 Server 是否运行)"
        except Exception as e:
            log.error(f"[wx:{self.bot_id}] agent error: {e}")
            result["text"] = result["text"] or f"(Agent 错误: {e})"

        if result["tools"]:
            log.info(f"[wx:{self.bot_id}] agent 执行了 {len(result['tools'])} 次工具调用")

        # [P0-1] 先剔除内部 [系统:...]/[SYSTEM...] 控制消息, 再 strip,
        # 保证会话持久化和微信发送用的都是同一份已过滤文本.
        result["text"] = _filter_system_leak(result["text"]).strip()
        return result

    # ── 保活循环 ──────────────────────────────────────────────
    async def _keepalive_loop(self):
        """定期检查用户活跃度，对即将 token 过期的用户发送保活消息"""
        log.info(f"[wx:{self.bot_id}] 保活循环已启动")
        while self.status == "online":
            try:
                await asyncio.sleep(600)   # 每 10 分钟检查一次

                cfg = _wx_cfg()
                keepalive_hours = cfg.get("keepalive_hours", _DEFAULT_KEEPALIVE_HOURS)
                keepalive_msg = cfg.get("keepalive_message", _DEFAULT_KEEPALIVE_MSG)
                keepalive_secs = keepalive_hours * 3600
                now = time.time()

                for uid, last_ts in list(self._user_last_ts.items()):
                    elapsed = now - last_ts

                    if keepalive_secs < elapsed < 86400:
                        if self._user_keepalive_sent.get(uid):
                            continue
                        user_info = self.connected_users.get(uid, {})
                        user_name = user_info.get("name", uid[:8])
                        log.info(f"[wx:{self.bot_id}] 发送保活消息给 {user_name}")
                        try:
                            await self._bot.send(uid, keepalive_msg)
                            self._user_keepalive_sent[uid] = True
                        except Exception as e:
                            err_s = str(e)
                            log.warning(f"[wx:{self.bot_id}] 保活发送失败: {e}")
                            if "-14" in err_s or "expired" in err_s.lower():
                                self.status = "expired"
                                self._last_hint = "会话已过期，请重新扫码"
                                log.error(f"[wx:{self.bot_id}] 会话过期，保活循环终止")
                                return  # 退出保活循环

                    elif elapsed >= 86400:
                        user_info = self.connected_users.get(uid, {})
                        user_name = user_info.get("name", uid[:8])
                        if not self._user_keepalive_sent.get(uid + "_expired_logged"):
                            log.warning(f"[wx:{self.bot_id}] ⚠️ 用户 {user_name} token 已过期(>{elapsed/3600:.1f}h)，需用户重新发消息续期")
                            self._user_keepalive_sent[uid + "_expired_logged"] = True

                    elif elapsed < keepalive_secs:
                        self._user_keepalive_sent.pop(uid, None)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[wx:{self.bot_id}] keepalive error: {e}")
                await asyncio.sleep(60)

    # ── 停止 ─────────────────────────────────────────────────
    async def _stop_tasks(self):
        """取消所有后台任务"""
        # [v1.1] 先取消每用户在跑的 agent 任务, 否则 _run_task 停了这些还在挂
        for uid, task in list(self._user_tasks.items()):
            if task and not task.done():
                task.cancel()
        for uid, task in list(self._user_tasks.items()):
            if task and not task.done():
                try:
                    await asyncio.wait_for(task, timeout=2)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
        self._user_tasks.clear()

        for task in (self._run_task, self._keepalive_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._run_task = None
        self._keepalive_task = None

    async def shutdown(self):
        """停止所有循环，下线"""
        self.status = "offline"
        await self._stop_tasks()
        log.info(f"[wx:{self.bot_id}] 已下线")

    # ── 状态摘要（供前端 API 使用）────────────────────────────
    def info(self) -> dict:
        active_users = []
        now = time.time()
        for uid, u in self.connected_users.items():
            last_ts = self._user_last_ts.get(uid, 0)
            elapsed = now - last_ts
            active_users.append({
                "user_id": uid[:8] + "...",
                "name": u.get("name", "?"),
                "last_active": u.get("last_ts", 0),
                "token_expired": elapsed > 86400,
                "hours_remaining": max(0, round((86400 - elapsed) / 3600, 1)),
            })

        return {
            "bot_id": self.bot_id,
            "wx_account": self.wx_account,
            "wx_display": self.wx_display or self.bot_id,
            "status": self.status,
            "msg_count": self.msg_count,
            "last_active": self.last_active,
            "users": active_users,
            "qr_url": self.qr_url if self.status == "wait_scan" else None,
            "last_error": self._last_error,
            "hint": self._last_hint,
        }


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
