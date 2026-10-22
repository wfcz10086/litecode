"""lib/wechat/lifecycle.py — WeChatBotInstance 生命周期 Mixin: 扫码登录、SDK 回调、保活循环、停止下线、状态摘要（从 wechat_bridge.py 抽取, 纯搬运）。"""

import asyncio
import time
from pathlib import Path

from lib.wechat.common import (
    log, _SDK_AVAILABLE, _DEFAULT_KEEPALIVE_HOURS, _DEFAULT_KEEPALIVE_MSG, _wx_cfg,
)
if _SDK_AVAILABLE:
    from lib.wechat.common import WeChatBot


def _bot_agent() -> str:
    try:
        from lib.version import VERSION
        return f"LiteCode/{VERSION.lstrip('v')}"
    except Exception:
        return "LiteCode/2"


class WeChatLifecycleMixin:
    """WeChatBotInstance 的登录/回调/保活/停止/状态部分 (依赖实例在 __init__ 中初始化的属性)"""

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
            kw = dict(cred_path=cred_path, on_qr_url=self._on_qr_url, on_scanned=self._on_scanned,
                      on_expired=self._on_expired, on_error=self._on_error)
            try:   # wechatbot-sdk >= 0.3.0: 扫码后可能要输手机上显示的配对码; 0.2.x 没有这两个参数
                self._bot = WeChatBot(**kw, on_verify_code=self._on_verify_code, bot_agent=_bot_agent())
                self._hook_verify_poll()
            except TypeError:
                self._bot = WeChatBot(**kw)

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

    # ── 配对码 (wechatbot-sdk 0.3.0) ─────────────────────────
    # SDK 的 on_verify_code 是同步回调、直接要返回值, 默认实现是从命令行 input() 读 —— 服务里没有命令行,
    # 而且同步等待会卡住整个事件循环。做法: 回调只把状态置成 need_code 并返回空; 在 SDK 下一次轮询扫码状态时
    # (这里包了一层, 是异步的) 等网页提交的配对码, 带上它再去轮询。
    def _on_verify_code(self, is_retry: bool):
        self.status = "need_code"
        self._need_code = True
        self._last_hint = ("配对码不对，请对照手机微信上的数字重新输入" if getattr(self, "_code_tried", False)
                           else "请输入手机微信上显示的配对码")
        log.info(f"[wx:{self.bot_id}] 需要配对码 (retry={is_retry or getattr(self, '_code_tried', False)})")
        return None

    def _hook_verify_poll(self):
        api = getattr(self._bot, "_api", None)
        orig = getattr(api, "poll_qr_status", None)
        if orig is None:
            return

        async def poll(base_url, qrcode, verify_code=None):
            if getattr(self, "_need_code", False) and not verify_code:
                deadline = time.time() + 300
                while getattr(self, "_need_code", False) and not getattr(self, "_submitted_code", None):
                    if time.time() > deadline:
                        self._need_code = False
                        break
                    await asyncio.sleep(0.5)
                verify_code = getattr(self, "_submitted_code", None)
                self._submitted_code = None
                self._need_code = False
                if verify_code:
                    self._code_tried = True
            return await orig(base_url, qrcode, verify_code)
        api.poll_qr_status = poll

    def submit_verify_code(self, code: str) -> dict:
        code = "".join(ch for ch in str(code or "") if ch.isdigit())
        if not code:
            return {"ok": False, "error": "配对码是手机微信上显示的数字"}
        if self.status != "need_code":
            return {"ok": False, "error": f"当前状态是 {self.status}，不需要配对码"}
        self._submitted_code = code
        self.status = "scanned"
        self._last_hint = "已提交配对码，等待确认…"
        return {"ok": True}

    # ── SDK 回调 ─────────────────────────────────────────────
    def _on_qr_url(self, url: str):
        """SDK 回调：二维码 URL 已就绪"""
        self.qr_url = url
        self.status = "wait_scan"
        self._need_code = False      # 配对码错太多次被拦后 SDK 会换新二维码
        self._last_error = None
        log.info(f"[wx:{self.bot_id}] 📱 二维码已生成，等待扫码")

    def _on_scanned(self):
        """SDK 回调：用户已扫码，等待手机确认"""
        self.status = "scanned"
        self._code_tried = False
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
            "qr_url": self.qr_url if self.status in ("wait_scan", "need_code") else None,
            "last_error": self._last_error,
            "hint": self._last_hint,
        }
