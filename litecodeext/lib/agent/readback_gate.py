"""[READBACK] 收尾前回读闸: Windows 界面改过文件后, 汇报前必须回读文件真值。

真机案例 (2026-10-17, gpu-vm): 模型用 WPS 表格做销售表, 文件里 D5=SUM(D2:D4) 实际值 1964,
它却从截图/OCR 读错数, 汇报"合计 4163 ✓ 已验证"。本闸在模型给出最终文本回复 (无 tool_calls) 时检查:
本回合有 windows_desktop 改动类动作, 且最后一次改动之后没有任何回读类动作 → 不结束回合,
注入一次 [SYSTEM-READBACK] 提示; 每回合最多一次, 第二次直接放行 (不死循环)。
开关 agent.readback_gate (默认 true), 关闭时 observe/check 都是空操作。

状态挂在 TurnContext.readback 上 (每回合新建), 主循环零改动:
  observe() 由 after_tools._after_tool_batch 调用, check() 由 text_reply._handle_text_reply 调用。
"""
from dataclasses import dataclass
import json
import re

from lib.config import CFG

PREFIX = "[SYSTEM-READBACK]"

# 只有"和文件有关"的界面操作才要求回读: 保存/另存为、剪切粘贴、删除、重命名、拖拽。
# 纯打字 + 回车 (如在微信里发消息) 不产出文件, 不拦 —— 否则每次发消息都要多绕一轮 (2026-10-17 收窄)。
# 鼠标点工具栏"保存"图标这类按坐标点击识别不了, 由工具描述里的"改过文件必须 inspect_file"兜底。
FILE_HOTKEYS = frozenset({"ctrl+s", "ctrl+shift+s", "f12", "ctrl+v", "ctrl+x", "delete", "f2"})
FILE_DESKTOP_ACTIONS = frozenset({"drag"})
_FILE_CLICK_TEXT = re.compile(r"保存|另存|粘贴|剪切|删除|重命名|(?i:save|paste|cut|delete|rename)")


def is_file_change(action, args) -> bool:
    if action in FILE_DESKTOP_ACTIONS:
        return True
    if action == "hotkey":
        return str(args.get("keys", "")).lower() in FILE_HOTKEYS
    if action == "click_text":
        return bool(_FILE_CLICK_TEXT.search(str(args.get("text", ""))))
    return False


# windows_system 里能读到文件真值的动作
READBACK_ACTIONS = frozenset({"inspect_file", "list_dir", "run_powershell", "download"})

INJECT_TEXT = (
    PREFIX + " 你在界面上修改/保存过文件, 但之后没有回读核对。请用 windows_system 的 inspect_file "
    "(或 list_dir/run_powershell) 读取实际文件内容, 用真实数据核对你将要汇报的结论 (尤其是数字), "
    "再给出最终总结; 若确实没有产出文件, 说明原因后直接总结。"
)


@dataclass
class ReadbackState:
    used_desktop: bool = False
    pending_change: bool = False   # 有改动类动作且其后尚无成功的回读
    injected: bool = False


def enabled() -> bool:
    return bool((CFG.get("agent") or {}).get("readback_gate", True))


def _state(ctx) -> ReadbackState:
    if getattr(ctx, "readback", None) is None:
        ctx.readback = ReadbackState()
    return ctx.readback


def _ok(content) -> bool:
    """工具确实执行了且未报错: 被守卫拦下 ([SYSTEM-...]) 或 ERROR 开头都不算。"""
    text = (content if isinstance(content, str) else str(content or "")).lstrip()
    return not (text.startswith("ERROR") or text.startswith("[SYSTEM"))


def observe(ctx, tool_call_list, tool_results) -> None:
    """一批工具执行完后按调用顺序更新状态 (同批内"先改后读"也算已回读)。"""
    if not enabled():
        return
    results = {r.get("tool_call_id"): r.get("content") for r in tool_results or [] if isinstance(r, dict)}
    st = _state(ctx)
    for tc in tool_call_list or []:
        fn = (tc.get("function") or {})
        name = fn.get("name")
        if name not in ("windows_desktop", "windows_system"):
            continue
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (TypeError, ValueError):
            args = {}
        action = (args if isinstance(args, dict) else {}).get("action")
        content = results.get(tc.get("id"))
        if name == "windows_desktop":
            st.used_desktop = True
            if is_file_change(action, args if isinstance(args, dict) else {}) and _ok(content):
                st.pending_change = True
        elif action in READBACK_ACTIONS and _ok(content):
            st.pending_change = False


def check(ctx):
    """模型即将以纯文本收尾时调用: 需要拦一次则返回注入文本, 否则 None。"""
    if not enabled():
        return None
    st = getattr(ctx, "readback", None)
    if st is None or not st.used_desktop or not st.pending_change or st.injected:
        return None
    st.injected = True
    return INJECT_TEXT
