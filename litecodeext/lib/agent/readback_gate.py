"""[READBACK] 收尾前回读闸: Windows 界面改过文件后, 汇报前必须回读文件真值。

真机案例 (2026-10-17, gpu-vm): 模型用 WPS 表格做销售表, 文件里 D5=SUM(D2:D4) 实际值 1964,
它却从截图/OCR 读错数, 汇报"合计 4163 ✓ 已验证"。本闸在模型给出最终文本回复 (无 tool_calls) 时检查:
本回合有 windows_desktop 改动类动作, 且最后一次改动之后没有任何回读类动作 → 不结束回合,
注入一次 [SYSTEM-READBACK] 提示; 每回合最多一次, 第二次直接放行 (不死循环)。
开关 agent.readback_gate (默认 true), 关闭时 observe/check 都是空操作。

状态挂在 TurnContext.readback 上 (每回合新建), 主循环零改动:
  observe() 由 after_tools._after_tool_batch 调用, check() 由 text_reply._handle_text_reply 调用。
"""
from dataclasses import dataclass, field
import json
import re

from lib.config import CFG
from lib.agent.untrusted import unwrap as _unwrap   # 外部内容信封 (windows_* 结果) 先剥掉再解析 JSON

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
    if action in ("click_text", "click_element"):   # click_element 按控件名点 "保存" 等同 click_text
        return bool(_FILE_CLICK_TEXT.search(str(args.get("text", ""))))
    return False


# windows_system 里能读到文件真值的动作
READBACK_ACTIONS = frozenset({"inspect_file", "list_dir", "run_powershell", "download"})

INJECT_TEXT = (
    PREFIX + " 你在界面上修改/保存过文件, 但之后没有回读核对。请用 windows_system 的 inspect_file "
    "(或 list_dir/run_powershell) 读取实际文件内容, 用真实数据核对你将要汇报的结论 (尤其是数字), "
    "再给出最终总结; 若确实没有产出文件, 说明原因后直接总结。"
)


# [P0-2 2026-10-21] 待验状态绑定 (电脑, 目标文件, 那次修改), 不再只按电脑别名记:
# 原先同机任何成功的 inspect_file/list_dir/run_powershell (哪怕读的是另一个文件、Get-Date) 都会清掉待验。
#   - 目标文件: 桌面动作返回里带动作后的 windows 列表, 取前台窗口标题里的文件名 (如 "销售表.xlsx - WPS Office");
#     取不到文件名时目标记为 None (未知), 只能放宽到"同机读到了某个文件内容"。
#   - 改动类别: content (保存/粘贴/剪切) 只能被读到文件内容的动作清除; fs (删除/重命名/拖拽) 允许 list_dir 确认。
#   - 那次修改: 按调用顺序处理, 回读只清除它之前登记的改动; 之后同一文件再改会重新登记, 旧回读不算。
_FS_HOTKEYS = frozenset({"delete", "f2"})
_FS_CLICK_TEXT = re.compile(r"删除|重命名|(?i:delete|rename)")
_FILE_NAME = re.compile(r"([^\\/:*?\"<>|\r\n]+?\.(?:xlsx|xlsm|xls|csv|et|docx|doc|wps|pptx|ppt|dps|txt|md|json|"
                        r"xml|ini|log|pdf|html?|py|js|ps1|bat|cfg|yaml|yml))(?=$|[\s\]\)\-—|*])", re.I)
# run_powershell 只有真正读文件的脚本才算回读 (Get-Date / Get-Process 之类不算)
_PS_READ = re.compile(r"Get-Content|\bgc\b|\bcat\b|\btype\b|Import-Csv|Import-Excel|Get-FileHash|Select-String|"
                      r"\[(?:System\.)?IO\.File\]|ReadAll", re.I)
_PS_LIST = re.compile(r"Get-Item|Get-ChildItem|\bdir\b|\bls\b|\bgci\b|Test-Path", re.I)   # 只够确认删除/重命名


def change_kind(action, args) -> str:
    if action in FILE_DESKTOP_ACTIONS:
        return "fs"
    if action == "hotkey":
        return "fs" if str(args.get("keys", "")).lower() in _FS_HOTKEYS else "content"
    if action in ("click_text", "click_element") and _FS_CLICK_TEXT.search(str(args.get("text", ""))):
        return "fs"
    return "content"


def norm_name(path) -> str:
    """规范化为小写文件名 (去引号/空白, 统一分隔符); 远端环境变量展开不了, 比较以文件名为准。"""
    text = str(path or "").strip().strip("'\"").replace("/", "\\").rstrip("\\")
    return text.rsplit("\\", 1)[-1].strip().lower()


@dataclass
class ReadbackState:
    used_desktop: bool = False
    pending_change: bool = False   # 有改动类动作且其后尚无匹配的成功回读
    pending: dict = field(default_factory=dict)   # (alias, 目标文件名或 None) → 改动类别 content/fs
    focus_file: dict = field(default_factory=dict)  # alias → 最近一次桌面返回里前台窗口的文件名
    injected: bool = False

    @property
    def pending_aliases(self) -> set:   # 兼容旧字段: 各电脑分别确认
        return {alias for alias, _ in self.pending}


def enabled() -> bool:
    return bool((CFG.get("agent") or {}).get("readback_gate", True))


def _state(ctx) -> ReadbackState:
    if getattr(ctx, "readback", None) is None:
        ctx.readback = ReadbackState()
    return ctx.readback


def _ok(content) -> bool:
    """工具确实执行了且未报错: 被守卫拦下 ([SYSTEM-...]) 或 ERROR 开头都不算。"""
    text = _unwrap(content if isinstance(content, str) else str(content or "")).lstrip()
    if not text or text.startswith(("ERROR", "[SYSTEM", "TIMEOUT", "EXEC_ERROR")):
        return False
    if text.startswith('{'):
        try:
            result, _ = json.JSONDecoder().raw_decode(text)
        except ValueError:
            return False
        return result.get('ok') is True and result.get('exit_code', 0) == 0
    return True


def _alias(ctx, args, content) -> str:
    """[2026-10-19] 原先直接比模型原始 alias: 操作员子代理不传 alias 时 check_windows_call 在服务端补成
    分配的电脑, 而这里读到空串 → 改动记在 "" 名下, 回读记在 "gpu-vm" 名下, 永远对不上, 误注入回读提示;
    "GPU-VM " 与 "gpu-vm" 也被当成两台。两边统一: 优先用工具结果里服务端回填的 alias, 其次模型参数,
    缺省按工具同样的规则取本会话分配的电脑; 最后 strip + lower。"""
    alias = ''
    text = _unwrap(content) if isinstance(content, str) else ''
    body = text.lstrip()
    if body.startswith('ERROR: '):
        body = body[7:].split(' ', 1)[-1].lstrip()
    if body.startswith('{'):
        try:
            result, _ = json.JSONDecoder().raw_decode(body)
            if isinstance(result, dict) and isinstance(result.get('alias'), str):
                alias = result['alias']
        except ValueError:
            pass
    if not alias.strip() and isinstance(args, dict):
        alias = str(args.get('alias') or '')
    if not alias.strip():
        try:
            from lib.agent.subagent_operator import assigned_alias
            alias = assigned_alias(getattr(ctx, 'session_id', None) or '') or ''
        except Exception:
            alias = ''
    return alias.strip().lower()


def _result_obj(content):
    text = _unwrap(content) if isinstance(content, str) else ''
    body = text.lstrip()
    if body.startswith('ERROR: '):
        body = body[7:].split(' ', 1)[-1].lstrip()
    if body.startswith('{'):
        try:
            result, _ = json.JSONDecoder().raw_decode(body)
            return result if isinstance(result, dict) else {}
        except ValueError:
            pass
    return {}


def _foreground_file(content):
    """桌面动作返回 → 前台窗口标题里的文件名 (小写); 没有 windows 信息返回 False, 有但无文件名返回 None。"""
    windows = _result_obj(content).get('windows')
    if not isinstance(windows, list):
        return False
    for w in windows:
        if isinstance(w, dict) and w.get('foreground'):
            m = _FILE_NAME.search(str(w.get('title') or ''))
            return norm_name(m.group(1)) if m else None
    return None


def _clears(kind, target, action, args, content) -> bool:
    """一次成功的 windows_system 读取能否确认 (target, kind) 这项待验改动。"""
    if action in ("inspect_file", "download"):
        if target is None:
            return True
        result = _result_obj(content)
        names = {norm_name(args.get("path")), norm_name(result.get("remote_path")), norm_name(result.get("name"))}
        return target in names
    if action == "run_powershell":
        script = str(args.get("script") or "")
        reads = _PS_READ.search(script) or (kind == "fs" and _PS_LIST.search(script))
        return bool(reads) and (target is None or target in script.lower().replace("/", "\\"))
    if action == "list_dir":   # 列目录看不到内容, 只能确认删除/重命名/移动
        return kind == "fs" and (target is None or target in _unwrap(str(content or "")).lower())
    return False


def pending_targets(ctx) -> list:
    st = getattr(ctx, "readback", None)
    if st is None:
        return []
    return sorted(f"{alias or '?'}:{target or '(未识别文件名)'}" for alias, target in st.pending)


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
        if not isinstance(args, dict):
            args = {}
        action = args.get("action")
        content = results.get(tc.get("id"))
        alias = _alias(ctx, args, content)
        if name == "windows_desktop":
            st.used_desktop = True
            fg = _foreground_file(content)
            if fg is not False:
                st.focus_file[alias] = fg
            if is_file_change(action, args) and _ok(content):
                target = st.focus_file.get(alias)
                kind = change_kind(action, args)
                key = (alias, target)
                # 同一文件既有 content 又有 fs 改动时按更严的 content 记
                st.pending[key] = "content" if "content" in (kind, st.pending.get(key)) else "fs"
        elif action in READBACK_ACTIONS and _ok(content):
            for key in [k for k, kind in st.pending.items()
                        if k[0] == alias and _clears(kind, k[1], action, args, content)]:
                del st.pending[key]
        st.pending_change = bool(st.pending)


def check(ctx):
    """模型即将以纯文本收尾时调用: 需要拦一次则返回注入文本, 否则 None。"""
    if not enabled():
        return None
    st = getattr(ctx, "readback", None)
    if st is None or not st.used_desktop or not st.pending_change or st.injected:
        return None
    st.injected = True
    named = [t for t in pending_targets(ctx) if not t.endswith("(未识别文件名)")]
    if named:
        return INJECT_TEXT + " 待回读的文件 (电脑:文件名): " + ", ".join(named) + "。"
    return INJECT_TEXT
