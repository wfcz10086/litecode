"""[SYSTEM-CONTINUE] 远程操控回合"只说不做"续跑闸。

真机 E2E (2026-10-21, GLM-5.3-Flash 操作 win-hugebq 记事本): 模型连续两次在中途以一段
"……让我用 list_dir 确认文件是否存在：" / "让我用坐标来点击这个按钮。" 的纯文本收尾, 没有调用工具,
回合直接结束, 用户拿到的是半截任务。远程界面操作步骤多, 这种"宣布下一步却没动手"最常见。

本闸只在**本回合用过远程操控工具** (windows_desktop / windows_system / ssh_exec 等) 且最终文本
的最后一句是"接下来要做 X"的意图句时触发, 注入一次续跑提示; 每回合最多 MAX_INJECT 次, 不会死循环。
"""
import re

PREFIX = "[SYSTEM-CONTINUE]"
MAX_INJECT = 2
REMOTE_TOOLS = frozenset({"windows_desktop", "windows_system", "ssh_exec", "ssh_read_file", "ssh_write_file",
                          "desktop_click", "desktop_type", "desktop_key", "desktop_screenshot", "desktop_exec"})
# 最后一句以这些说法开头/包含, 且没有完成/汇报类收束词 → 视为宣布了下一步动作
_INTENT = re.compile(r"(让我|我先|我来|我需要|我要|我将|我再|下一步|接下来|现在(我)?(来|去|要|需要|就|开始|先)|"
                     r"先(用|点|按|关|打开|截图|确认|检查)|然后(用|点|按)|let me|next,? i|i'?ll now|i will now)", re.I)
_DONE = re.compile(r"(已完成|全部完成|任务完成|总结|结论|核对(一致|通过)|完全一致|如下表|以上|失败原因|无法完成|需要你|请你|请用户|是否需要)")

INJECT_TEXT = (PREFIX + " 你刚才说了下一步要做的操作, 但没有调用任何工具, 回合会就此结束、任务停在半截。"
               "现在直接调用工具执行这一步 (一次一个动作, 做完看返回结果核对); "
               "如果任务其实已经完成或确实无法继续, 就给出最终总结: 做了什么、证据 (回读内容/截图/返回值)、未完成项和原因。")


def _last_sentence(text: str) -> str:
    lines = [line.strip(" \t*#>-") for line in (text or "").strip().splitlines() if line.strip(" \t*#>-")]
    if not lines:
        return ""
    tail = lines[-1]
    # 最后一行是列表项/很短的残句时, 带上前一行一起看
    if len(tail) < 8 and len(lines) > 1:
        tail = lines[-2] + " " + tail
    return tail[-160:]


def dangling_intent(text: str) -> bool:
    """最终文本的最后一句是不是"我接下来要做 X"。"""
    tail = _last_sentence(text)
    if not tail or _DONE.search(tail):
        return False
    return bool(_INTENT.search(tail)) or tail.endswith(("：", ":"))


def used_remote_tools(messages, lookback: int = 60) -> bool:
    """本回合 (最近一条真实用户消息之后) 是否调用过远程操控工具。"""
    for message in reversed((messages or [])[-lookback:]):
        role = message.get("role")
        # 系统注入的 user 消息都以 "[" 开头 ([SYSTEM-…]/[DESKTOP_OBSERVATION]…, 截图观察是 list), 不算新回合起点
        if role == "user" and isinstance(message.get("content"), str) and not message["content"].lstrip().startswith("["):
            return False
        for call in message.get("tool_calls") or []:
            if (call.get("function") or {}).get("name") in REMOTE_TOOLS:
                return True
    return False


# ---- [SYSTEM-CHECKLIST] GUI 任务收尾对照原始要求 ----
# 跑分 (2026-10-21): 重试时打开/输入/保存/读回都做了, 漏了"关闭记事本"就汇报完成。
# 只检查用户原话里明确提到、且本回合工具调用里完全找不到执行痕迹的步骤; 每回合最多提醒一次。
CHECK_PREFIX = "[SYSTEM-CHECKLIST]"
_NEGATION = re.compile(r"(别|不要|勿|不用|无需|不必|不能|禁止|don'?t|do not|never)\s*$", re.I)
_STEPS = (
    ("关闭窗口", re.compile(r"关闭|关掉|关上|\bclose\b", re.I)),
    ("保存文件", re.compile(r"保存|另存|\bsave\b", re.I)),
    ("读回核对文件", re.compile(r"读回|回读|核对|\bverify\b|read (it )?back", re.I)),
)


def _wanted(pattern, text):
    """原话里有这个要求, 且至少一处不是否定说法 ("别关它" 不算要求关闭)。"""
    for match in pattern.finditer(text or ""):
        if not _NEGATION.search(text[max(0, match.start() - 6):match.start()]):
            return True
    return False


def _calls(turn):
    import json
    for message in turn or []:
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            yield fn.get("name"), args if isinstance(args, dict) else {}


def _done(step, calls):
    for name, args in calls:
        action = str(args.get("action", ""))
        text = str(args.get("text", "")).lower()
        keys = str(args.get("keys") or args.get("key") or "").lower().replace(" ", "")
        if step == "关闭窗口" and (action == "close_window" or keys in ("alt+f4", "ctrl+w")
                                  or (action in ("click_element", "click_text") and ("关闭" in text or "close" in text))
                                  or (name == "windows_system" and action == "kill_process")):
            return True
        if step == "保存文件" and (keys in ("ctrl+s", "ctrl+shift+s", "f12")
                                  or (action in ("click_element", "click_text") and ("保存" in text or "save" in text))):
            return True
        if step == "读回核对文件" and name == "windows_system" and action in ("inspect_file", "list_dir", "download", "run_powershell"):
            return True
    return False


def missing_steps(user_text, turn):
    calls = list(_calls(turn))
    if not any(name == "windows_desktop" for name, _ in calls):
        return []
    return [step for step, pattern in _STEPS if _wanted(pattern, user_text) and not _done(step, calls)]


def _user_text(ctx):
    for message in list(getattr(ctx, "new_msgs", None) or []) + list(reversed(getattr(ctx, "messages", None) or [])):
        if message.get("role") == "user" and isinstance(message.get("content"), str) \
                and not message["content"].lstrip().startswith(("[SYSTEM", "[DESKTOP", "[READBACK")):
            return message["content"]
    return ""


def check(ctx, full_text: str):
    """模型即将以纯文本收尾时调用: 需要续跑返回注入文本, 否则 None。"""
    # new_msgs 恰好是本回合产生的消息 (不含系统提示/历史/注入), 优先用它判断; 取不到再回看 messages
    turn = getattr(ctx, "new_msgs", None)
    used = (any(name in REMOTE_TOOLS for name, _ in _calls(turn)) if turn
            else used_remote_tools(getattr(ctx, "messages", None)))
    if not used:
        return None
    count = getattr(ctx, "remote_continue_count", 0) or 0
    if count < MAX_INJECT and dangling_intent(full_text):
        ctx.remote_continue_count = count + 1
        return INJECT_TEXT
    if not getattr(ctx, "remote_checklist_done", False) and turn:
        missing = missing_steps(_user_text(ctx), turn)
        if missing:
            ctx.remote_checklist_done = True
            return (CHECK_PREFIX + " 收尾前对照用户原始要求逐项核对: 本回合工具调用里没看到这些步骤的执行痕迹: "
                    + "、".join(missing) + "。确实要做就现在补做并核对结果 (关闭窗口前确认是你打开的那个, 别动用户的窗口); "
                    "已用别的方式完成或用户其实不需要, 就在总结里说明。")
    return None
