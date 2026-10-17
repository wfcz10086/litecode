"""lib/agent/subagent_operator.py — 远程操作员子代理 (operator): 让多台 Windows 节点并行干活。

一台 Windows 只有一套鼠标键盘, 所以本模块保证"一台电脑同一时刻只归一个操作员":

  派发期 (spawn_agent 两条路径都调 prepare_spawn_args):
    - 每个 operator 任务必须有 alias (任务项的 alias 字段, 或 spawn_agent 顶层 alias 作默认)。
    - 会同时运行的两个 operator 指定了同一 alias → 整个派发拒绝 (返回 ERROR, 不跑任何子代理),
      并提示改用 pipeline_tasks / dag depends_on 串行或合并成一个任务。
      选"拒绝"而不是"悄悄改串行": 并行编排器给每个任务的超时是独立计的, 排队等锁会白白吃掉超时,
      且模型以为在并行、实际在串行, 容易误判进度; 明确报错后模型改用串行编排一次就对。
      pipeline_tasks / batch_items 本身串行, DAG 中有依赖先后关系的同 alias 步骤也允许。
    - alias 以一行标记 `[操作员节点 alias=<alias>]` 追加进任务文本 —— 编排器各条路径 (并行/管道/DAG/批量/
      重试) 都原样透传 task, 子代理据此取到自己的 alias, 不会串号; 模型也能直接看到分配给自己的电脑。

  运行期 (_run_subagent 对 operator 走 run_as_operator):
    - 按 alias 的 asyncio.Lock 独占: 操作员之间排队等待 (跨会话/跨派发), 最多等 agent.operator_lock_wait 秒
      (默认 120) 仍拿不到 → 以 [子代理异常] 结束, 不死锁 (每个操作员只持有一把锁)。
    - 非操作员调用方 (主代理 / 其他会话) 对被占用 alias 的 windows_desktop / windows_system 调用 → 立即返回
      ERROR 说明正被哪个操作员占用, 不等待 —— 主代理在等自己的子代理时不可能再自己排队拿锁, 立即报错永不死锁。
    - 操作员调用 windows 工具时 alias 与分配的不一致 → 直接拒绝; 未填 alias → 自动补成分配的 alias。
    - 子代理 sid 唯一 (subagent_operator_<hex>), handlers/windows_desktop 的 _LAST_SHOT 以 (sid, alias) 为 key,
      各子代理的最近截图互不串用; 拿锁时清掉其他会话对该 alias 的旧截图 (画面即将被改), 结束时清掉本 sid 的截图。

  产出物 (截图 / 下载):
    - handler 以子代理 sid 调 tool_dispatch._add_artifact (子代理 sid 没有会话, 本身登记不上), 经 note_artifact
      先记在本模块; 子代理结束时 (含超时/取消) flush_artifacts 登记到**父会话**: 最后 MAX_SHOTS 张截图 + 全部下载,
      按文件路径去重 (同一路径只留最后一条), 记录带 source=operator / alias; 结果末尾附一行登记清单。
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple

OPERATOR_ROLES = frozenset({"operator"})
WINDOWS_TOOLS = ("windows_desktop", "windows_system")
DEFAULT_TIMEOUT = 1200           # 单个操作员默认超时 (秒): GUI 任务步数多
DEFAULT_LOCK_WAIT = 120
MARKER_FMT = "[操作员节点 alias={alias}]"
_MARKER_RE = re.compile(r"\[操作员节点 alias=([A-Za-z0-9_.\-]{1,64})\]")

OPERATOR_CONFIG = {
    "system": (
        "你是远程操作员 (Operator Agent), 负责在分配给你的那一台 Windows 电脑上完成任务。\n\n"
        "## 只操作分配给你的电脑\n"
        "任务末尾的 [操作员节点 alias=...] 就是你的电脑。windows_desktop / windows_system 的 alias 必须填它, "
        "填别的电脑会被框架直接拒绝。其它电脑可能正由别的操作员同时操作, 不要碰。\n\n"
        "## 工作方式\n"
        "1. 先观察: 开工先 windows_desktop screenshot (或 ocr) 看清当前桌面、前台窗口和弹窗, 不要凭想象操作。\n"
        "2. 任务指定了做法就照做: 任务里写明用什么方式 (例如「用记事本」「用鼠标拖拽」「在 WPS 里改」「打开某软件」), "
        "必须用那个方式完成, 不能为了省事换成命令行 —— 这类任务往往就是在验收界面操作本身。"
        "只有任务没指定做法时才按下一条: 命令行优先。\n"
        "   命令行优先: 查文件/目录、看进程、读系统信息、执行脚本等能用 windows_system 完成的, 优先用它, "
        "比截图点击更快更稳; 只有必须操作图形界面 (WPS/Office 编辑、GUI 弹窗、微信) 才用 windows_desktop。"
        "回读核对随时可以用命令行, 不受这条限制。\n"
        "3. 每步确认: 每个界面动作 (点击/输入/快捷键/拖拽) 之后, 用返回的截图或再 screenshot/ocr 确认画面"
        "真的变成预期状态, 再做下一步; 没变化就重新判断焦点/遮挡, 不要盲目重复同一动作。\n"
        "4. 改过文件必须回读: 用界面保存/另存/粘贴/剪切/删除/重命名/拖拽过文件, 或 upload 过文件后, "
        "必须用 windows_system inspect_file (或 list_dir / run_powershell) 回读文件真实内容, "
        "汇报的数字和文字以回读结果为准, 不以截图/OCR 为准; 两者不一致时以文件为准并如实说明。\n"
        "5. 收尾确认: 结束前最后一步用 screenshot/ocr 或回读确认最终状态。\n"
        "6. 同一做法连续失败 2 次就换方法; 遇到需要用户决定的事 (多个同名对象、要覆盖用户草稿、要输密码) "
        "停下来在总结里说明, 不要自己猜。\n\n"
        "## 输出\n"
        "做了哪些操作 + 最终状态 (引用回读/截图的真实结果) + 没完成或没验证的部分。"
    ),
    # 只给 Windows 工具与服务端只读辅助; 没有 write_file/patch_file/execute_shell, 不能改服务器。
    "allowed": {"windows_desktop", "windows_system", "read_file", "vision_ocr"},
    "max_iter": 60,
}

# 观察类动作: 重复调用是常态 (每步都要截图), 不计入子代理的"同参数重复调用"死循环检测
_DESKTOP_OBSERVE = frozenset({"screenshot", "ocr", "windows", "clipboard", "wechat_status", "scroll"})
_SYSTEM_OBSERVE = frozenset({"system_info", "processes", "services", "list_dir", "inspect_file"})


def is_operator(agent_type: str) -> bool:
    return agent_type in OPERATOR_ROLES


def marker(alias: str) -> str:
    return MARKER_FMT.format(alias=alias)


def alias_from_task(task: str) -> Optional[str]:
    m = _MARKER_RE.findall(str(task or ""))
    return m[-1] if m else None


def loop_exempt(fn_name: str, fn_args: dict) -> bool:
    act = (fn_args or {}).get("action")
    return ((fn_name == "windows_desktop" and act in _DESKTOP_OBSERVE)
            or (fn_name == "windows_system" and act in _SYSTEM_OBSERVE))


# ═══════════════════════════ 派发期校验 ═══════════════════════════

_LIST_KEYS = ("parallel_tasks", "competitive_tasks", "pipeline_tasks", "dag_tasks")
_CONCURRENT_KEYS = ("parallel_tasks", "competitive_tasks")


def _valid_alias(a) -> bool:
    return isinstance(a, str) and re.fullmatch(r"[A-Za-z0-9_.\-]{1,64}", a.strip() or "") is not None


def _with_marker(task: str, alias: str) -> str:
    task = str(task or "")
    if alias_from_task(task) == alias:
        return task
    return f"{task.rstrip()}\n\n{marker(alias)}"


def _dag_conflicts(items: List[dict]) -> List[Tuple[str, str, str]]:
    """DAG 中同 alias 且互无祖先关系 (会并发) 的 operator 步骤对。"""
    ids, deps, ops = [], {}, []
    for i, t in enumerate(items):
        sid = t.get("step_id") or f"step_{i+1}"
        ids.append(sid)
        deps[sid] = list(t.get("depends_on") or [])
        if is_operator(t.get("agent_type", "coder")):
            ops.append((sid, t["alias"]))

    def anc(s, seen=None):
        seen = set() if seen is None else seen
        for d in deps.get(s, []):
            if d not in seen:
                seen.add(d)
                anc(d, seen)
        return seen
    out = []
    for i, (a, al) in enumerate(ops):
        for b, bl in ops[i + 1:]:
            if al == bl and b not in anc(a) and a not in anc(b):
                out.append((a, b, al))
    return out


def prepare_spawn_args(args: dict) -> Tuple[dict, Optional[str]]:
    """校验并规范 spawn_agent 参数里的 operator 任务。返回 (新参数, 错误文本或 None)。

    不含 operator 的派发原样返回 (同一对象), 零影响。
    """
    if not isinstance(args, dict):
        return args, None
    top_alias = args.get("alias")
    top_type = args.get("agent_type", "coder")
    lists = {k: args.get(k) for k in _LIST_KEYS if isinstance(args.get(k), list)}
    batch = args.get("batch_items") if isinstance(args.get("batch_items"), list) else None
    has_op = is_operator(top_type) or any(
        isinstance(t, dict) and is_operator(t.get("agent_type", "coder"))
        for v in lists.values() for t in v)
    if not has_op:
        return args, None

    new = dict(args)
    missing: List[str] = []
    for key, items in lists.items():
        out = []
        for i, t in enumerate(items):
            if not (isinstance(t, dict) and is_operator(t.get("agent_type", "coder"))):
                out.append(t)
                continue
            al = t.get("alias") or top_alias or alias_from_task(t.get("task", ""))
            if not _valid_alias(al):
                missing.append(f"{key}[{i}]「{str(t.get('task', ''))[:20]}」")
                out.append(t)
                continue
            al = al.strip()
            t2 = dict(t, alias=al, task=_with_marker(t.get("task", ""), al))
            t2.setdefault("timeout", DEFAULT_TIMEOUT)
            out.append(t2)
        new[key] = out
    multi = any(lists.values()) or bool(batch)
    if is_operator(top_type) and batch:
        out = []
        for i, it in enumerate(batch):
            it = it if isinstance(it, dict) else {"task": str(it)}
            al = it.get("alias") or top_alias
            if not _valid_alias(al):
                missing.append(f"batch_items[{i}]")
                out.append(it)
                continue
            out.append(dict(it, alias=al.strip(), task=_with_marker(it.get("task", ""), al.strip())))
        new["batch_items"] = out
    elif is_operator(top_type) and not multi:
        al = top_alias or alias_from_task(args.get("task", ""))
        if not _valid_alias(al):
            missing.append("task")
        else:
            new["alias"] = al.strip()
            new["task"] = _with_marker(args.get("task", ""), al.strip())
    if missing:
        return args, ("ERROR: spawn_agent 派发 operator 必须指定要操作的 Windows 电脑 alias "
                      "(任务项里的 alias 字段, 或顶层 alias 作为默认)。缺 alias 的任务: "
                      + ", ".join(missing[:6]) + "。可先用 windows_nodes list 查看在线节点别名。")

    # 并发冲突: 同一 alias 同时只能有一个操作员
    conflicts: List[str] = []
    for key in _CONCURRENT_KEYS:
        seen: Dict[str, int] = {}
        for i, t in enumerate(new.get(key) or []):
            if isinstance(t, dict) and is_operator(t.get("agent_type", "coder")):
                if t["alias"] in seen:
                    conflicts.append(f"{key}[{seen[t['alias']]}] 与 {key}[{i}] 都要操作 {t['alias']}")
                else:
                    seen[t["alias"]] = i
    if isinstance(new.get("dag_tasks"), list):
        dag_items = [t for t in new["dag_tasks"] if isinstance(t, dict)]
        for a, b, al in _dag_conflicts(dag_items):
            conflicts.append(f"dag 步骤 {a} 与 {b} 都要操作 {al} 且没有依赖先后")
    if conflicts:
        return args, ("ERROR: spawn_agent 拒绝派发 —— 一台 Windows 电脑同一时刻只能由一个操作员操作 "
                      "(只有一套鼠标键盘)。冲突: " + "; ".join(conflicts[:6]) + "。"
                      "请改为: 不同任务分配给不同电脑并行; 同一台电脑上的多件事合并成一个 operator 任务, "
                      "或用 pipeline_tasks / dag_tasks 的 depends_on 让它们串行。")
    return new, None


# ═══════════════════════════ 产出物登记到父会话 ═══════════════════════════

MAX_SHOTS = 6                          # 每个操作员最多登记最后 6 张截图; 下载全部登记
_ARTS: Dict[str, List[dict]] = {}      # operator sid -> 运行期间 handler 登记的产出物 (按时间顺序)


def note_artifact(sid: str, record) -> None:
    """tool_dispatch._add_artifact 的旁路: 只收集正在运行的操作员子代理的产出物。"""
    lst = _ARTS.get(sid or "")
    if lst is not None and isinstance(record, dict) and isinstance(record.get("filepath"), str):
        lst.append(dict(record))


def select_artifacts(records: List[dict], max_shots: Optional[int] = None) -> List[dict]:
    """去重 (同路径留最后一条) + 截图只留最后 max_shots (默认 MAX_SHOTS) 张 + 下载/其它全部; 保持时间顺序。"""
    max_shots = MAX_SHOTS if max_shots is None else max_shots
    last: Dict[str, int] = {}
    for i, r in enumerate(records):
        last[r["filepath"]] = i
    uniq = [records[i] for i in sorted(last.values())]
    shots = [r for r in uniq if r.get("op") == "screenshot"]
    keep_shots = {id(r) for r in shots[-max_shots:]} if max_shots > 0 else set()
    return [r for r in uniq if r.get("op") != "screenshot" or id(r) in keep_shots]


def flush_artifacts(sid: str, parent_sid: Optional[str], alias: Optional[str]) -> List[dict]:
    """把操作员 sid 收集到的产出物登记到父会话; 返回已登记的记录 (无父会话时只丢弃)。幂等: 只生效一次。"""
    recs = _ARTS.pop(sid, None)
    if not recs or not parent_sid:
        return []
    picked = select_artifacts(recs)
    try:
        from core import tool_dispatch
    except Exception:
        return []
    out = []
    for r in picked:
        rec = {"filepath": r["filepath"], "op": r.get("op") or "file", "source": "operator",
               "alias": alias or "", "subagent": sid}
        try:
            tool_dispatch._add_artifact(parent_sid, rec)
            out.append(rec)
        except Exception:
            continue
    return out


def artifacts_note(regs: List[dict]) -> str:
    if not regs:
        return ""
    shots = [r for r in regs if r["op"] == "screenshot"]
    others = [r for r in regs if r["op"] != "screenshot"]
    head = f"[产出物已登记到主会话: 截图 {len(shots)} 张" + (f" (最近 {MAX_SHOTS} 张)" if len(shots) >= MAX_SHOTS else "")
    head += f", 下载/文件 {len(others)} 个]"
    return head + "".join(f"\n- {r['op']}: {r['filepath']}" for r in others + shots)


# ═══════════════════════════ 运行期独占 ═══════════════════════════

_LOCKS: Dict[str, Tuple[asyncio.Lock, object]] = {}
_OWNER: Dict[str, dict] = {}          # alias -> {sid, since, task}
_SID_ALIAS: Dict[str, str] = {}       # operator sid -> 分配的 alias


def _lock_for(alias: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    ent = _LOCKS.get(alias)
    if ent is None or (ent[1] is not loop and not ent[0].locked()):
        ent = (asyncio.Lock(), loop)
        _LOCKS[alias] = ent
    return ent[0]


def _lock_wait() -> float:
    try:
        from ..config import CFG
        return float((CFG.get("agent") or {}).get("operator_lock_wait", DEFAULT_LOCK_WAIT))
    except Exception:
        return float(DEFAULT_LOCK_WAIT)


def owner_of(alias: str) -> Optional[dict]:
    return _OWNER.get(alias)


def assigned_alias(sid: str) -> Optional[str]:
    return _SID_ALIAS.get(sid)


def check_windows_call(sid: str, fn_name: str, args: dict) -> Tuple[dict, Optional[str]]:
    """windows 工具调用前的独占检查 (子代理循环与 handler 都调用, 幂等)。返回 (参数, 错误或 None)。"""
    if fn_name not in WINDOWS_TOOLS or not isinstance(args, dict):
        return args, None
    mine = _SID_ALIAS.get(sid or "")
    if mine is not None:
        al = args.get("alias")
        if not al:
            return dict(args, alias=mine), None
        if al != mine:
            return args, (f"ERROR: {fn_name} 拒绝 —— 你是操作员子代理, 只分配了电脑 {mine}, "
                          f"不能操作 {al}。只用 alias={mine} 完成任务; 需要别的电脑请在总结里说明。")
        return args, None
    al = args.get("alias")
    own = _OWNER.get(al) if al else None
    if own and own.get("sid") != sid:
        held = int(time.time() - own.get("since", time.time()))
        return args, (f"ERROR: {fn_name} 节点 {al} 正被操作员子代理独占 (已 {held}s, 任务: {own.get('task', '')[:40]}), "
                      "一台电脑同一时刻只能一个操作员用鼠标键盘。请等该子代理结束后再操作, 或改用其它节点。")
    return args, None


def _forget_shots(alias: str, keep_sid: Optional[str] = None, only_sid: Optional[str] = None) -> None:
    try:
        from core.tools.handlers.windows_desktop import _LAST_SHOT
    except Exception:
        return
    for k in list(_LAST_SHOT):
        if not (isinstance(k, tuple) and len(k) == 2):
            continue
        s, a = k
        if only_sid is not None:
            if s == only_sid:
                _LAST_SHOT.pop(k, None)
        elif a == alias and s != keep_sid:
            _LAST_SHOT.pop(k, None)


async def run_as_operator(core_fn: Callable, task: str, agent_type: str, context: str = "",
                          sse_emit=None, parent_sid: str = None) -> str:
    """拿 alias 锁 → 绑定 sid → 跑子代理 → 释放。core_fn(task, agent_type, context, sse_emit, parent_sid, sid)。"""
    from ..config import log
    alias = alias_from_task(task)
    if not alias:
        return ("[子代理异常] operator 没有分配 Windows 电脑: 任务里缺 [操作员节点 alias=...] 标记。"
                "请用 spawn_agent 的 alias 字段 (或 parallel_tasks 每项的 alias) 指定要操作的电脑。")
    sid = f"subagent_{agent_type}_{uuid.uuid4().hex[:8]}"
    lock = _lock_for(alias)
    wait = _lock_wait()
    t0 = time.time()
    if lock.locked():
        own = _OWNER.get(alias) or {}
        log.info(f"  [operator] {alias} 正被 {own.get('sid', '?')} 占用, 排队等待 ≤{wait:.0f}s")
    try:
        await asyncio.wait_for(lock.acquire(), timeout=wait)
    except asyncio.TimeoutError:
        own = _OWNER.get(alias) or {}
        return (f"[子代理异常] operator: 电脑 {alias} 正被另一个操作员 ({own.get('sid', '?')}) 占用, "
                f"等待 {wait:.0f}s 仍未释放, 本任务未执行。请稍后重试或改用其它电脑。")
    try:
        _OWNER[alias] = {"sid": sid, "since": time.time(), "task": " ".join(str(task).split())[:80],
                         "parent": parent_sid}
        _SID_ALIAS[sid] = alias
        _forget_shots(alias, keep_sid=sid)       # 别的会话手里那张截图马上就过时了
        if time.time() - t0 > 1:
            log.info(f"  [operator] {alias} 等锁 {time.time() - t0:.1f}s 后获得")
        log.info(f"  [operator] {sid} 独占 {alias}")
        _ARTS[sid] = []
        out = await core_fn(task, agent_type, context, sse_emit, parent_sid, sid)
        regs = flush_artifacts(sid, parent_sid, alias)
        if regs and isinstance(out, str):
            out = f"{out}\n\n{artifacts_note(regs)}"
        return out
    finally:
        flush_artifacts(sid, parent_sid, alias)      # 超时/取消/异常: 已产生的截图与下载照样登记
        _SID_ALIAS.pop(sid, None)
        if (_OWNER.get(alias) or {}).get("sid") == sid:
            _OWNER.pop(alias, None)
        _forget_shots(alias, only_sid=sid)
        lock.release()
        log.info(f"  [operator] {sid} 释放 {alias}")


def append_observation(messages: list, tool_calls: list, raw_results: list) -> bool:
    """子代理版 DESKTOP-VISION: 与主循环同一实现 (lib.desktop_observation), 用未截断的原始工具结果。

    是否真把图交给模型由 transport 按配置决定 (与主循环一致): 当前模型 supports_vision → 直接看图;
    不支持但配了 vision_fallback_id → 本次请求临时路由到视觉模型; 都没有 → 剥图只留文字说明。
    """
    if not raw_results:
        return False
    try:
        from lib.desktop_observation import append_latest_observation
        from ..config import WORKSPACE
        return append_latest_observation(messages, tool_calls, raw_results, WORKSPACE)
    except Exception:
        return False
