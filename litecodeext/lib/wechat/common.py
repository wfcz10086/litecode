"""lib/wechat/common.py — 微信桥公共部分: 常量、问候语、SDK 探测、系统消息泄漏过滤、配置与 web session 读写（从 wechat_bridge.py 抽取, 纯搬运）。"""

import json
import logging
import random
import re
import time
from pathlib import Path

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
# 原在 litecodeext/wechat_bridge.py 用 Path(__file__).parent; 搬到 lib/wechat/ 后上溯三级保持同一目录
_BASE = Path(__file__).parent.parent.parent
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
