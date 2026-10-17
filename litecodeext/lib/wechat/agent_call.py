"""lib/wechat/agent_call.py — WeChatBotInstance 调用 Agent Mixin: 流式消费 litecode_server SSE + typing 保持 + 超时策略（从 wechat_bridge.py 抽取, 纯搬运）。"""

import asyncio
import json
import time
from typing import Optional, List, Any

import aiohttp

from lib.wechat_attach import _image_to_data_url
from lib.wechat.common import (
    log, _TYPING_REFRESH_INTERVAL, _load_cfg, _filter_system_leak,
)


class WeChatAgentCallMixin:
    """WeChatBotInstance 的 Agent 调用部分"""

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
            # 实测证据: 微信会话 wx-xxxx 多轮 TURN-END 停在 iter=1/2/4/7 (上限 500),
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
