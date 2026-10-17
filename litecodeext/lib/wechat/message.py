"""lib/wechat/message.py — WeChatBotInstance 消息处理 Mixin: 抢占式调度、附件缓冲、写 web session、分段回复与发图发文件（从 wechat_bridge.py 抽取, 纯搬运）。"""

import asyncio
import re
import time
from pathlib import Path

from lib.wechat_media import _sdk_download_media
from lib.wechat_attach import _main_model_supports_vision, _preprocess_attachment
from lib.wechat_format import _split_message, _format_wx_reply
from lib.wechat.common import (
    log, _IMG_RE, _load_cfg, _load_web_session, _save_web_session,
)
from lib.wechat.extract import _extract_message_full


class WeChatMessageMixin:
    """WeChatBotInstance 的收消息 → 调 Agent → 回微信 主流程"""

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
