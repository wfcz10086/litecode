#!/usr/bin/env python3
"""
timer_manager.py — LiteCode 定时器系统
========================================
功能:
  • 一次性定时 (once) — 指定时间点执行一次
  • 周期定时 (cron)  — cron 表达式循环执行
  • 动作类型:
    - wechat_msg:   给微信用户发静态文本
    - wechat_shell: 跑 shell 拿 stdout, 再发给微信 (动态内容, 币价/笑话用)
    - wecom_msg:    给企微 (WeCom智能机器人) 用户发静态 markdown
    - wecom_shell:  跑 shell 拿 stdout, 再作 markdown 推给企微
    - shell:        执行 shell 命令 (结果只回 last_result)
    - agent:        调用 Agent 执行任务
    - web_notify:   在 web 前端推送通知
    - dag:          触发指定 DAG
  • 持久化存储到 ~/.litecode/timers.json
  • 后台 asyncio 循环每 30 秒检查一次

API (由 web_ui.py 注册):
  GET    /api/timers           列表
  POST   /api/timers           创建
  PATCH  /api/timers/{id}      修改
  DELETE /api/timers/{id}      删除
  POST   /api/timers/{id}/run  立即执行
"""

import asyncio
import json
import logging
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Callable

log = logging.getLogger("timer_manager")

_BASE = Path(__file__).parent
_TIMERS_FILE = Path.home() / ".litecode" / "timers.json"
_TIMERS_FILE.parent.mkdir(parents=True, exist_ok=True)
TIMER_HISTORY_MAX = 20


# ── Cron 表达式解析（简化版: 分 时 日 月 周）───────────
def _cron_match(expr: str, dt: datetime) -> bool:
    """检查 datetime 是否匹配 cron 表达式 (分 时 日 月 周)"""
    parts = expr.strip().split()
    if len(parts) != 5:
        return False

    fields = [
        (dt.minute, 0, 59),
        (dt.hour, 0, 23),
        (dt.day, 1, 31),
        (dt.month, 1, 12),
        (dt.weekday(), 0, 6),  # 0=Monday
    ]

    for part, (current, low, high) in zip(parts, fields):
        if not _cron_field_match(part, current, low, high):
            return False
    return True


def _cron_field_match(field: str, current: int, low: int, high: int) -> bool:
    """匹配单个 cron 字段"""
    if field == "*":
        return True

    for item in field.split(","):
        if "/" in item:
            base, step = item.split("/", 1)
            step = int(step)
            start = low if base == "*" else int(base)
            if (current - start) % step == 0 and current >= start:
                return True
        elif "-" in item:
            a, b = item.split("-", 1)
            if int(a) <= current <= int(b):
                return True
        else:
            if int(item) == current:
                return True
    return False


# ── 定时器数据模型 ─────────────────────────────────────
def _new_timer(
    name: str,
    timer_type: str = "once",     # once | cron
    schedule: str = "",           # cron: "0 9 * * *" | once: "2025-04-15T10:00:00"
    action_type: str = "shell",   # wechat_msg | wechat_shell | wecom_msg | wecom_shell | shell | agent | web_notify | dag
    action_target: str = "",      # shell: command | agent: session_id | wechat: bot_id:user_id
    action_content: str = "",     # shell: command | agent: prompt | wechat/notify: message
    enabled: bool = True,
    description: str = "",
    notify_on_error: bool = False,
    notify_on_success: bool = False,
    notify_channel: str = "web",  # web | wechat
    notify_target: str = "",       # wechat: bot_id:user_id
) -> dict:
    return {
        "id": f"tmr_{uuid.uuid4().hex[:8]}",
        "name": name,
        "type": timer_type,
        "schedule": schedule,
        "action": {
            "type": action_type,
            "target": action_target,
            "content": action_content,
        },
        "enabled": enabled,
        "description": description,
        "notify_on_error": notify_on_error,
        "notify_on_success": notify_on_success,
        "notify_channel": notify_channel,
        "notify_target": notify_target,
        "created": time.time(),
        "last_run": None,
        "next_run": None,
        "run_count": 0,
        "last_status": "",   # ok | error | ""
        "last_duration_ms": 0,
        "last_result": "",
        "last_error": "",
        "history": [],
    }


# ── 存储 ─────────────────────────────────────────────
def _load_timers() -> List[dict]:
    if _TIMERS_FILE.exists():
        try:
            return json.loads(_TIMERS_FILE.read_text())
        except Exception:
            pass
    return []


def _save_timers(timers: List[dict]):
    # [bind-mount-fix-2026-05] _TIMERS_FILE 是单文件 bind-mount (docker-compose
    # ./litecode_state/timers.json:/root/.litecode/timers.json), 不能跨 mount
    # 用 os.replace rename → "Device or resource busy". 改用 truncate-then-write
    # 保留 inode (跟 config.json 同思路).
    payload = json.dumps(timers, ensure_ascii=False, indent=2)
    _TIMERS_FILE.write_text(payload)


# ── TimerManager ─────────────────────────────────────
class TimerManager:
    """定时器管理器，后台循环检查并执行到期任务"""

    def __init__(self):
        self.timers: List[dict] = _load_timers()
        try:
            self._loaded_mtime = _TIMERS_FILE.stat().st_mtime if _TIMERS_FILE.exists() else 0.0
        except Exception:
            self._loaded_mtime = 0.0
        self._task: Optional[asyncio.Task] = None
        self._wx_send_fn: Optional[Callable] = None  # 微信发送回调
        self._wc_send_fn: Optional[Callable] = None  # 企微(WeCom) 发送回调
        self._agent_fn: Optional[Callable] = None     # Agent 调用回调
        self._dag_run_fn: Optional[Callable] = None   # [v1.3] DAG 直接触发回调
        self._notify_queue: List[dict] = []            # web 通知队列

    def _reload_if_stale(self) -> bool:
        """[cross-process] 若磁盘 mtime 比内存新, 重载 self.timers.
        避免另一个进程 (server ↔ web_ui) 写盘后本进程的旧内存覆盖回去。"""
        try:
            if not _TIMERS_FILE.exists():
                return False
            disk_mtime = _TIMERS_FILE.stat().st_mtime
            if disk_mtime > getattr(self, "_loaded_mtime", 0.0):
                self.timers = _load_timers()
                self._loaded_mtime = disk_mtime
                return True
        except Exception as e:
            log.warning(f"[timer] reload check failed: {e}")
        return False

    def set_wechat_sender(self, fn: Callable):
        """注入微信发送函数: async fn(bot_id, user_id, text)"""
        self._wx_send_fn = fn

    def set_wecom_sender(self, fn: Callable):
        """注入企微(WeCom)发送: async fn(bot_id, userid, text, mode='markdown')"""
        self._wc_send_fn = fn

    def set_agent_caller(self, fn: Callable):
        """注入 Agent 调用函数: async fn(prompt, session_id) -> str"""
        self._agent_fn = fn

    def set_dag_runner(self, fn: Callable):
        """[v1.3] 注入 DAG 触发函数: async fn(dag_name) -> dict({ok, job_id, ...}).
        让 timer action_type='dag' 不需要绕 HTTP/auth, 直接拉一个 DAG job."""
        self._dag_run_fn = fn

    # ── CRUD ─────────────────────────────────────────
    def list_timers(self) -> List[dict]:
        # [v1.14 fix] 跨进程一致性：若磁盘文件比内存版本新（其他进程写过），重新加载。
        self._reload_if_stale()
        self._update_next_run()
        for t in self.timers:
            t.setdefault("history", [])
        return self.timers

    def add_timer(self, data: dict) -> dict:
        # [v1.11 F2/P76/P77] 入参校验: 空名 / 非法 cron / once 过去时间
        name = (data.get("name") or "").strip()
        if not name:
            raise ValueError("name 必填且非空")
        timer_type = data.get("type", "once")
        schedule = (data.get("schedule") or "").strip()
        if timer_type == "cron":
            parts = schedule.split()
            if len(parts) != 5:
                raise ValueError(f"cron 表达式必须 5 段 (分 时 日 月 周): '{schedule}'")
            # 每段必须能被 _cron_field_match 解析: * 或数字, 或 a-b, 或 a/b, 或 a,b 列表
            ranges = [(0,59),(0,23),(1,31),(1,12),(0,6)]
            for part, (lo, hi) in zip(parts, ranges):
                try:
                    # 用一个范围内合法的当前值尝试一次, 若内部 int() 抛 ValueError 即非法
                    _cron_field_match(part, lo, lo, hi)
                except Exception as e:
                    raise ValueError(f"cron 字段 '{part}' 无效: {e}")
        elif timer_type == "once":
            if not schedule:
                raise ValueError("once 类型需指定 schedule (ISO 时间)")
            try:
                target = datetime.fromisoformat(schedule)
                if target <= datetime.now():
                    raise ValueError(f"once 目标时间必须未来: '{schedule}' (现在 {datetime.now().isoformat(timespec='seconds')})")
            except ValueError as e:
                # ValueError 来自上面的 raise 或 fromisoformat 失败
                if "未来" in str(e) or "ISO" in str(e):
                    raise
                raise ValueError(f"once schedule 必须 ISO 格式 (如 2027-01-01T10:00:00): '{schedule}'")
        timer = _new_timer(
            name=name,
            timer_type=timer_type,
            schedule=schedule,
            action_type=data.get("action_type", "shell"),
            action_target=data.get("action_target", ""),
            action_content=data.get("action_content", ""),
            enabled=data.get("enabled", True),
            description=data.get("description", ""),
            notify_on_error=bool(data.get("notify_on_error", False)),
            notify_on_success=bool(data.get("notify_on_success", False)),
            notify_channel=data.get("notify_channel", "web"),
            notify_target=data.get("notify_target", ""),
        )
        self._reload_if_stale()
        self.timers.append(timer)
        self._save()
        log.info(f"[timer] 创建: {timer['name']} ({timer['type']}: {timer['schedule']})")
        return timer

    def update_timer(self, timer_id: str, data: dict) -> Optional[dict]:
        self._reload_if_stale()
        for t in self.timers:
            if t["id"] == timer_id:
                for k in ("name", "type", "schedule", "enabled",
                         "description", "notify_on_error", "notify_on_success",
                         "notify_channel", "notify_target"):
                    if k in data:
                        t[k] = data[k]
                if "action_type" in data or "action_target" in data or "action_content" in data:
                    t.setdefault("action", {})
                    for k in ("type", "target", "content"):
                        ak = f"action_{k}"
                        if ak in data:
                            t["action"][k] = data[ak]
                self._save()
                return t
        return None

    def delete_timer(self, timer_id: str) -> bool:
        self._reload_if_stale()
        before = len(self.timers)
        self.timers = [t for t in self.timers if t["id"] != timer_id]
        if len(self.timers) < before:
            self._save()
            return True
        return False

    async def run_now(self, timer_id: str) -> dict:
        """立即执行指定定时器"""
        for t in self.timers:
            if t["id"] == timer_id:
                result = await self._execute(t)
                return {"ok": True, "result": result}
        return {"ok": False, "error": "not found"}

    def get_notifications(self) -> List[dict]:
        """获取并清空 web 通知队列"""
        nots = list(self._notify_queue)
        self._notify_queue.clear()
        return nots

    # ── 后台循环 ─────────────────────────────────────
    async def start(self):
        """启动后台检查循环"""
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        log.info(f"[timer] 后台循环已启动，{len(self.timers)} 个定时器")

    async def _loop(self):
        while True:
            try:
                await asyncio.sleep(30)
                # [cross-process-fix] 每 tick 先重载磁盘, 避免 web_ui/server 两进程各自
                # 持有 stale 副本, 一方 disable/delete 后被另一方 _loop 的 self._save() 覆盖回去
                reloaded = self._reload_if_stale()
                now = datetime.now()
                dirty = False
                for t in self.timers:
                    if not t.get("enabled"):
                        continue
                    if self._should_run(t, now):
                        try:
                            await self._execute(t)
                            dirty = True  # _execute 会改 last_run/history
                        except Exception as e:
                            t["last_error"] = str(e)[:200]
                            log.error(f"[timer] {t['name']} 执行失败: {e}")
                            dirty = True
                # 只在有实际改动时落盘, 避免无意义覆盖别进程的写入
                if dirty:
                    self._save()
                elif reloaded:
                    # 别进程写过, 我这轮没改, 什么也不写
                    pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[timer] loop error: {e}")
                await asyncio.sleep(60)

    def _should_run(self, timer: dict, now: datetime) -> bool:
        """判断是否应该执行"""
        if timer["type"] == "once":
            try:
                target = datetime.fromisoformat(timer["schedule"])
                if now >= target and not timer.get("last_run"):
                    return True
            except Exception:
                pass

        elif timer["type"] == "cron":
            if _cron_match(timer["schedule"], now):
                last = timer.get("last_run")
                if not last or (time.time() - last) > 55:
                    return True

        return False

    # ── 执行动作 ─────────────────────────────────────
    async def _execute(self, timer: dict) -> str:
        action = timer.get("action", {})
        action_type = action.get("type", "shell")
        target = action.get("target", "")
        content = action.get("content", "")
        result = ""
        run_error = ""    # 只装本次运行的错误; last_error 之前的跨运行 sticky 是 bug
        _t0 = time.time()

        log.info(f"[timer] 执行: {timer['name']} → {action_type}")

        try:
            if action_type == "shell":
                result = await self._exec_shell(content)
            elif action_type == "agent":
                result = await self._exec_agent(content, target)
            elif action_type == "wechat_msg":
                result = await self._exec_wechat(content, target)
            elif action_type == "wechat_shell":
                result = await self._exec_wechat_shell(content, target)
            elif action_type == "wecom_msg":
                result = await self._exec_wecom(content, target)
            elif action_type == "wecom_shell":
                result = await self._exec_wecom_shell(content, target)
            elif action_type == "web_notify":
                result = self._exec_notify(content, timer["name"])
            elif action_type == "dag":
                # [v1.3] target=DAG 名 (优先), 缺则用 content; 让一句话 timer+dag 链路打通
                dag_name = (target or content or "").strip()
                result = await self._exec_dag(dag_name)
            else:
                result = f"未知动作类型: {action_type}"

        except Exception as e:
            result = f"ERROR: {e}"
            run_error = str(e)[:200]

        # 结果字符串里带 "ERROR:" 前缀也算失败 (_exec_shell 的分支)
        if not run_error and result.startswith("ERROR:"):
            run_error = result[:200]

        duration_ms = int((time.time() - _t0) * 1000)
        status = "error" if run_error else "ok"

        timer["last_run"] = time.time()
        timer["run_count"] = timer.get("run_count", 0) + 1
        timer["last_result"] = result[:500]
        timer["last_error"] = run_error       # 清掉上次的 sticky
        timer["last_status"] = status
        timer["last_duration_ms"] = duration_ms

        # 一次性定时器执行后自动禁用
        if timer["type"] == "once":
            timer["enabled"] = False

        _entry = {
            "ts": time.time(),
            "status": status,
            "result": result[:500],
            "error": run_error,
            "duration_ms": duration_ms,
            "action_type": action_type,
        }
        timer.setdefault("history", []).append(_entry)
        if len(timer["history"]) > TIMER_HISTORY_MAX:
            timer["history"] = timer["history"][-TIMER_HISTORY_MAX:]

        # ── 触发通知钩子 (2026-07-24: 用户要求 "状态 + 执行情况 + 通知") ──
        try:
            await self._fire_notify(timer, status, result, run_error, duration_ms)
        except Exception as ne:
            log.warning(f"[timer] notify hook failed: {ne}")

        self._save()
        return result

    async def _fire_notify(self, timer: dict, status: str, result: str,
                            error: str, duration_ms: int):
        """按 timer.notify_on_error / notify_on_success 触发通知.
        channel=web  → 塞 _notify_queue, 前端轮询;
        channel=wechat → 走 wx_send_fn, target=bot_id:user_id.
        """
        if status == "error" and not timer.get("notify_on_error"):
            return
        if status == "ok" and not timer.get("notify_on_success"):
            return
        channel = timer.get("notify_channel", "web")
        target = timer.get("notify_target", "")
        icon = "❌" if status == "error" else "✅"
        summary = error if status == "error" else (result[:100] or "执行完成")
        msg = f"{icon} 定时器 [{timer['name']}] {status}  {duration_ms}ms\n{summary}"
        if channel == "wechat" and self._wx_send_fn and target:
            try:
                parts = target.split(":", 1)
                bot_id = parts[0] if parts else ""
                user_id = parts[1] if len(parts) > 1 else ""
                await self._wx_send_fn(bot_id, user_id, msg)
            except Exception as e:
                log.warning(f"[timer] wechat notify failed: {e}")
        else:
            self._notify_queue.append({
                "id": uuid.uuid4().hex[:8],
                "name": timer["name"],
                "message": msg,
                "status": status,
                "time": time.time(),
            })

    async def _exec_shell(self, command: str) -> str:
        """执行 shell 命令"""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            out = (stdout or b"").decode(errors="replace")
            err = (stderr or b"").decode(errors="replace")
            return f"exit={proc.returncode}\n{out}{err}".strip()[:1000]
        except asyncio.TimeoutError:
            return "TIMEOUT after 60s"
        except Exception as e:
            return f"ERROR: {e}"

    async def _exec_agent(self, prompt: str, session_id: str = "") -> str:
        """调用 Agent"""
        if self._agent_fn:
            try:
                return await asyncio.wait_for(
                    self._agent_fn(prompt, session_id or f"timer-{uuid.uuid4().hex[:6]}"),
                    timeout=300
                )
            except asyncio.TimeoutError:
                return "Agent 超时 (300s)"
        return "Agent 回调未注册"

    async def _exec_dag(self, dag_name: str) -> str:
        """[v1.3] 触发指定 DAG. 直接调注入的 dag_run_fn, 不绕 HTTP."""
        if not dag_name:
            return "ERROR: dag 动作需指定 DAG 名 (action_target 或 action_content)"
        if not self._dag_run_fn:
            return "ERROR: DAG 触发回调未注册 (web_ui 启动时应调 set_dag_runner)"
        try:
            ret = await asyncio.wait_for(self._dag_run_fn(dag_name), timeout=15)
            if isinstance(ret, dict):
                if ret.get("ok"):
                    return (f"✅ DAG '{dag_name}' 已启动 "
                            f"job_id={ret.get('job_id','?')} "
                            f"steps={ret.get('step_count','?')}")
                return f"ERROR: 启动失败 {ret.get('error', ret)}"
            return f"DAG 触发返回: {str(ret)[:300]}"
        except asyncio.TimeoutError:
            return "DAG 触发超时 (15s)"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"

    async def _exec_wechat(self, message: str, target: str) -> str:
        """发送微信消息"""
        if self._wx_send_fn:
            try:
                parts = target.split(":", 1)
                bot_id = parts[0] if parts else ""
                user_id = parts[1] if len(parts) > 1 else ""
                await self._wx_send_fn(bot_id, user_id, message)
                return f"已发送到 {target}"
            except Exception as e:
                return f"微信发送失败: {e}"
        return "微信发送回调未注册"

    async def _exec_wechat_shell(self, command: str, target: str) -> str:
        # 先跑 shell 拿 stdout, 再作为 wx 消息推给 target.
        # 币价/笑话等动态内容用: shell 输出即微信正文.
        if not self._wx_send_fn:
            return "ERROR: 微信发送回调未注册"
        if not target:
            return "ERROR: wechat_shell 需 target=bot_id:user_id"
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        except asyncio.TimeoutError:
            return "ERROR: shell TIMEOUT after 45s"
        except Exception as e:
            return f"ERROR: shell 启动失败 {e}"
        if proc.returncode != 0:
            err = (stderr or b"").decode(errors="replace").strip()[:300]
            return f"ERROR: shell exit={proc.returncode} {err}"
        text = (stdout or b"").decode(errors="replace").strip()
        if not text:
            return "ERROR: shell 输出为空, 不发送"
        text = text[:1800]  # 单条上限保守 (wechat_bridge 2000, 留余量)
        parts = target.split(":", 1)
        bot_id = parts[0] if parts else ""
        user_id = parts[1] if len(parts) > 1 else ""
        try:
            await self._wx_send_fn(bot_id, user_id, text)
        except Exception as e:
            return f"ERROR: 发送失败 {type(e).__name__}: {e}"
        return f"已发送 {len(text)} 字 → {target[:40]} | 首行: {text.splitlines()[0][:60]}"

    async def _exec_wecom(self, message: str, target: str) -> str:
        """企微静态消息. target 格式 <bot_id>:<userid>"""
        if not self._wc_send_fn:
            return "ERROR: 企微发送回调未注册"
        if not target:
            return "ERROR: wecom_msg 需 target=bot_id:userid"
        parts = target.split(":", 1)
        bot_id = parts[0]
        userid = parts[1] if len(parts) > 1 else ""
        if not userid:
            return "ERROR: target 缺 userid"
        try:
            r = await self._wc_send_fn(bot_id, userid, message)
            if isinstance(r, dict) and not r.get("ok"):
                return f"ERROR: {r.get('errmsg') or r.get('error') or r}"
            return f"已发送 → {target[:40]}"
        except Exception as e:
            return f"ERROR: {type(e).__name__}: {e}"

    async def _exec_wecom_shell(self, command: str, target: str) -> str:
        """跑 shell 拿 stdout 作企微 markdown 推送. 动态内容 (币价/笑话) 用."""
        if not self._wc_send_fn:
            return "ERROR: 企微发送回调未注册"
        if not target:
            return "ERROR: wecom_shell 需 target=bot_id:userid"
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
        except asyncio.TimeoutError:
            return "ERROR: shell TIMEOUT after 45s"
        except Exception as e:
            return f"ERROR: shell 启动失败 {e}"
        if proc.returncode != 0:
            err = (stderr or b"").decode(errors="replace").strip()[:300]
            return f"ERROR: shell exit={proc.returncode} {err}"
        text = (stdout or b"").decode(errors="replace").strip()
        if not text:
            return "ERROR: shell 输出为空, 不发送"
        text = text[:3800]  # 企微 markdown 4000 上限
        parts = target.split(":", 1)
        bot_id = parts[0]
        userid = parts[1] if len(parts) > 1 else ""
        try:
            r = await self._wc_send_fn(bot_id, userid, text)
            if isinstance(r, dict) and not r.get("ok"):
                return f"ERROR: {r.get('errmsg') or r.get('error') or r}"
        except Exception as e:
            return f"ERROR: 发送失败 {type(e).__name__}: {e}"
        return f"已发送 {len(text)} 字 → {target[:40]} | 首行: {text.splitlines()[0][:60]}"

    def _exec_notify(self, message: str, name: str) -> str:
        """推送 web 通知"""
        self._notify_queue.append({
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "message": message,
            "time": time.time(),
        })
        return f"已推送通知: {message[:50]}"

    # ── 工具 ─────────────────────────────────────────
    def _save(self):
        _save_timers(self.timers)
        # 记录自己刚写入的 mtime, 防止后续 _reload_if_stale 误判为"外部更新"再重载
        try:
            if _TIMERS_FILE.exists():
                self._loaded_mtime = _TIMERS_FILE.stat().st_mtime
        except Exception:
            pass

    def _update_next_run(self):
        """计算每个定时器的下次执行时间"""
        now = datetime.now()
        for t in self.timers:
            if not t.get("enabled"):
                t["next_run"] = None
                continue
            if t["type"] == "once":
                try:
                    target = datetime.fromisoformat(t["schedule"])
                    t["next_run"] = target.isoformat() if target > now else None
                except Exception:
                    t["next_run"] = None
            elif t["type"] == "cron":
                # 简单预测：检查接下来 1440 分钟
                for delta_min in range(1, 1441):
                    future = now + timedelta(minutes=delta_min)
                    if _cron_match(t["schedule"], future):
                        t["next_run"] = future.strftime("%Y-%m-%d %H:%M")
                        break
                else:
                    t["next_run"] = "?"

    async def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()


# ── 全局单例 ─────────────────────────────────────────
_manager: Optional[TimerManager] = None


def get_timer_manager() -> TimerManager:
    global _manager
    if _manager is None:
        _manager = TimerManager()
    return _manager
