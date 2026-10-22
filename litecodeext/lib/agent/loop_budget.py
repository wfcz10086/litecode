"""主循环的上下文窗口治理: 窗口估算、锚点日志、中途压缩与预算检查。

从 litecode_server.py 原样搬出 (拆分大文件), 函数体未改动; litecode_server 仍 re-export 这些名字。
"""
from lib import config as _cfg   # 模型热切换后读当前值 (reload_model 更新其模块变量), 不在导入时拷快照
from copy import deepcopy
import json
from history_utils import _sanitize_history
from lib import context_pressure as _cp
from lib import overhead_calib as _oc
from lib.config import CFG
from lib.config import HAS_MEMORY
from lib.config import MEM_HARD as _MEM_HARD
from lib.config import WORKSPACE
from lib.config import est_tokens
from lib.config import log
from lib.config import mem_get as _mem_get
from lib.tools.defs import TOOL_DEFS
from lib import tool_groups as _tg   # 实际发送的工具列表 (按会话按需暴露)
_AGT = CFG.get("agent", {})


def sanitize_model_copy(messages: list) -> list:
    """[2026-10-19] 主循环每轮对 ctx.messages 原地 _sanitize_history, 而这些 dict 与 new_msgs/历史共享:
    落盘证据被改写 (content None→"", arguments 被规范化/坏 JSON 变 "{}", 未答复的 tool_calls 被删)。
    只给模型列表换副本: 会被改写的消息 (带 tool_calls, 或 assistant content=None) 拷一份再清理,
    其余消息仍复用原对象 (不改它们, 省掉整段 deepcopy)。返回新列表。"""
    out = []
    for m in messages:
        if isinstance(m, dict) and (m.get("tool_calls") or (m.get("role") == "assistant" and m.get("content") is None)):
            m = {**m}
            if m.get("tool_calls"):
                m["tool_calls"] = deepcopy(m["tool_calls"])
        out.append(m)
    return _sanitize_history(out)


# ── [AGENT_STREAM_REFACTOR M7d 2026-09-18] 循环顶部上下文治理家族之一:
#    SLIDING-WINDOW 窗口缩紧 + token-budget 硬裁剪 (迁自主循环, 纯同步)。
#    返回本轮重算的 fixed_overhead (原局部 _fixed_overhead, 每轮无条件重算,
#    供 CONTEXT-BUDGET 段使用 —— 原实现靠 locals() 隐式借用, 现显式返回)。──
def _loop_context_window(ctx, usage, iteration, sys_prompt) -> int:
    # ── [SLIDING-WINDOW] 窗口缩紧: 每 5 轮触发, msgs>20 就 trim ──
    # [FIX] 老逻辑 iter%10 + msgs>30: msgs=302 的长 session 要 10 轮才 trim 一次, 上下文早就爆
    # 新逻辑: iter%5, 保留最近 14 条完整, 再配合 token-budget 硬裁剪 (下一段)
    _sw_every   = int(_AGT.get("sliding_window_every", 5))
    _sw_min     = int(_AGT.get("sliding_window_min_msgs", 20))
    _sw_keep    = int(_AGT.get("sliding_window_keep_recent", 14))
    # [2026-10-13] 只在上下文真有压力时才缩: 128K 窗口跑 SWE-bench, 第 10 轮就把读过的代码砍成一行,
    # 模型只好重读 → 触发防死循环。压力 = 当前 prompt 估值 / (窗口*token_budget_pct)。
    _sw_pressure = float(_AGT.get("sliding_window_pressure", 0.5))
    if (iteration > 0 and iteration % _sw_every == 0 and len(ctx.messages) > _sw_min
            and _cp.under_pressure(ctx.messages, usage, est_tokens,
                                   int(_cfg.live_context_window() * float(_AGT.get("token_budget_pct", 0.6))), _sw_pressure)):
        _trim_boundary = max(2, len(ctx.messages) - _sw_keep)
        _trimmed_count = 0
        for _mi in range(2, _trim_boundary):  # 跳过 system + 第一条 user
            _msg = ctx.messages[_mi]
            if _msg.get("role") == "tool":
                _old_content = _msg.get("content", "")
                if isinstance(_old_content, str) and len(_old_content) > 120:
                    _first_line = _old_content.split("\n", 1)[0][:120]
                    # messages 与 new_msgs/历史可能共享 dict；只替换模型输入，保留落盘证据。
                    ctx.messages[_mi] = {**_msg, "content": _first_line + " [trimmed]"}
                    _trimmed_count += 1
            elif _msg.get("role") == "assistant" and isinstance(_msg.get("content"), str):
                if len(_msg["content"]) > 200:
                    ctx.messages[_mi] = {**_msg, "content": _msg["content"][:150] + "..."}
                    _trimmed_count += 1
        if _trimmed_count > 0:
            log.info(f"  [SLIDING-WINDOW] trimmed {_trimmed_count} old msgs at iter {iteration} (keep_recent={_sw_keep})")

    # [FIX] token-budget 硬裁剪: 估算 prompt tokens, 超 (_cfg.CONTEXT_WINDOW*0.6) 就持续丢最早 tool/asst
    # 理由: 单靠 sliding-window 删不干净, 几轮对话后老 tool result 堆 200 条也没删
    _tb_pct     = float(_AGT.get("token_budget_pct", 0.6))
    # [FIX 2026-08-31] 预算此前只数 ctx.messages —— 而 messages 里**没有**
    # system 消息, 工具定义也不在其中。实测会话 wx-xxxx: 历史估 99,370 t,
    # vLLM 实报 180,828 t, 差的 8 万就是系统提示词 + 84 个工具的 JSON schema。
    # 预算看不见这块固定开销, 于是永远判定"还够", 最后由 vLLM 返回 400。
    # 现按 (窗口*pct - 固定开销) 作为历史可用额度。
    try:
        _fixed_overhead = est_tokens(sys_prompt) + est_tokens(json.dumps(_tg.tools_for(ctx, TOOL_DEFS), ensure_ascii=False))
    except Exception:
        _fixed_overhead = 0
    # [2026-10-12] 上面的估算对工具 schema 高估约 3 倍, 改用上游 usage 学到的实测值 (lib/overhead_calib.py)
    _fixed_overhead = _oc.adjust(_live_model(), _fixed_overhead, int(_cfg.live_context_window() * _tb_pct))
    # 两个口径分开, 不混:
    #   锚点真值 = 整个输入 (含 system+工具+对话) → 直接和 窗口*pct 比
    #   纯估算   = 只有对话历史 → 和 窗口*pct - 固定开销 比
    _tb_budget_est    = max(4096, int(_cfg.live_context_window() * _tb_pct) - _fixed_overhead)  # 估算口径
    _tb_budget_real   = max(4096, int(_cfg.live_context_window() * _tb_pct))                    # 锚点口径
    if len(ctx.messages) > 20:
        # [USAGE-ANCHOR] 有上游真实 usage 时, 用"锚点真值 + 自锚点以来的增量估算"。
        # 真值自带全部开销, 比 est 整段更准。保守: 真值 >= 当时估算才用锚 (防上游漏报)。
        _est_full = sum(est_tokens(m.get("content",""))+est_tokens(m.get("tool_calls","")) + est_tokens(m.get("reasoning_content",""))
                        for m in ctx.messages)
        _use_anchor = usage.usage_reports > 0 and usage.last_real_prompt_tokens >= usage.last_est_at_anchor
        if _use_anchor:
            _delta_since = max(0, _est_full - usage.last_est_at_anchor)   # 自锚点后历史增量 (估)
            _cur_tokens = usage.last_real_prompt_tokens + _delta_since
            _tb_budget  = _tb_budget_real
        else:
            _cur_tokens = _est_full
            _tb_budget  = _tb_budget_est
        if _cur_tokens > _tb_budget:
            _removed = 0
            # 从第 3 条开始删 (保留 system + 首 user), 保留最后 10 条
            _drop_cutoff = max(2, len(ctx.messages) - 10)
            _mi = 2
            while _mi < _drop_cutoff and _cur_tokens > _tb_budget:
                _role = ctx.messages[_mi].get("role")
                if _role in ("tool", "assistant"):
                    _c = str(ctx.messages[_mi].get("content") or "")
                    _tc = str(ctx.messages[_mi].get("tool_calls") or "")
                    _cur_tokens -= est_tokens(_c) + est_tokens(_tc)
                    ctx.messages.pop(_mi)
                    _drop_cutoff -= 1
                    _removed += 1
                    continue
                _mi += 1
            if _removed:
                # 在被删位置插一条标记, 防止 tool_calls/tool pair 残缺导致 400
                ctx.messages.insert(2, {"role": "user", "content": f"[SYSTEM: trimmed {_removed} old msgs to fit token budget {_tb_budget}t]"})
                # 清理 dangling calls 会原地改嵌套 tool_calls；先隔离持久化消息。
                ctx.messages = _sanitize_history(deepcopy(ctx.messages))
                log.warning(f"  [TOKEN-BUDGET] removed {_removed} msgs, {_cur_tokens}t/{_tb_budget}t")

    return _fixed_overhead


def _live_model() -> str:
    from lib.config import _LIVE as _lv
    return _lv.get("model_id", "")


def _log_anchor(ctx, usage, real: int, est_msgs: int) -> None:
    """[USAGE-ANCHOR] 打一行真实/估算比值。估算口径 = 对话历史 + 固定开销 (系统提示词+工具定义),
    和上游 prompt_tokens 同口径; 以前只比对话历史, 固定开销 ~1.4 万 t 让比值常年显示 1.5 倍误报。"""
    est = est_msgs + int(getattr(usage, "fixed_overhead", 0) or 0)
    _oc.learn(_live_model(), real, est_msgs)
    if est <= 0:
        return
    r = real / est
    off = r < 0.7 or r > 1.3
    if off or usage.usage_reports == 0:
        log.info(f"  \033[2m[USAGE-ANCHOR]\033[0m [{ctx.sid_tag}] 真实={real} 估算={est} "
                 f"(历史 {est_msgs} + 固定 {est - est_msgs}) 比值={r:.2f}"
                 + ("  \033[33m← 估算偏离\033[0m" if off else ""))


# ── [M7d] 上下文治理家族之二: MID-LOOP-COMPRESS 记忆压缩触发。
#    原 locals().get("_mlc_done_this_turn") 跨轮标志显式化为参数+返回值。──
def _loop_mid_compress(ctx, iteration, done_flag):
    # ── [MID-LOOP-COMPRESS] 按 token 水位 + 每15轮 触发 memory 压缩 ──
    # [FIX 2026-08-31] 原条件是 `iteration % 15 == 0` —— 只在第 15/30/45 轮检查。
    # 而微信/短对话一轮只跑 2-5 次迭代, iteration 永远到不了 15,
    # **压缩对这类会话从未运行过**。实测会话 wx-xxxx 因此涨到 300 条 /
    # 192,513 input tokens, 反复吃 vLLM 400。
    # 现改为: 每 15 轮照旧, 另加"任一轮超过 MEM_HARD 水位即压缩"(每回合最多一次)。
    _mlc_due = (iteration > 0 and iteration % 15 == 0)
    if not _mlc_due and HAS_MEMORY and ctx.session_id and not done_flag:
        _mlc_due = sum(est_tokens(m.get("content","")) for m in ctx.messages) > _MEM_HARD
    if _mlc_due and HAS_MEMORY and ctx.session_id:
        done_flag = True
        _est_tokens = sum(est_tokens(m.get("content","")) for m in ctx.messages)
        if _est_tokens > _MEM_HARD:
            try:
                _mlc_mgr = _mem_get(
                    workspace=WORKSPACE, session_id=ctx.session_id,
                    vllm_url=_cfg.BACKEND_URL, model_id=_cfg.MODEL_ID,
                    api_key=_cfg.API_KEY, context_window=_cfg.CONTEXT_WINDOW,
                )
                _mlc_mgr.check_and_compact(ctx.messages, _force_async=True)
                log.info(f"  [MID-LOOP-COMPRESS] triggered at iter {iteration}, est_tokens={_est_tokens}")
            except Exception:
                pass

    return done_flag


# ── [M7d] 上下文治理家族之三: CONTEXT-BUDGET LLM 调用前预算裁剪。
#    原 locals().get("_fixed_overhead", 0) 显式化为参数。──
def _loop_context_budget(ctx, fixed_overhead):
    # ── [CONTEXT-BUDGET] LLM 调用前检查 token 预算，超限则裁剪老消息 ──
    # [FIX 2026-08-31] 原先只数 content, 不数 tool_calls —— 而 write_file 的
    # 文件正文全在 tool_calls[].arguments 里, 对写代码的 agent 恰是最大一块。
    # 同文件 L986 的记账口径本来就数了 tool_calls, 两处口径不一致导致
    # 裁剪闸门系统性低估, 真实溢出时直接吃 vLLM 400 而非走优雅裁剪。
    _ctx_est = sum(est_tokens(m.get("content", "")) + est_tokens(m.get("tool_calls", "")) + est_tokens(m.get("reasoning_content",""))
                   for m in ctx.messages)
    # [FIX 2026-08-31] 同样扣掉 system + 工具定义的固定开销 (见上方 TOKEN-BUDGET 注释)
    _ctx_limit = max(4096, int(_cfg.live_context_window() * 0.75) - fixed_overhead)
    if _ctx_est > _ctx_limit and len(ctx.messages) > 10:
        _ctx_trimmed = 0
        # 从第3条消息开始（跳过 system + 第1条 user），逐条压缩直到低于预算
        for _ci in range(2, len(ctx.messages) - 8):  # 保留最近8条完整
            _cm = ctx.messages[_ci]
            _cc = str(_cm.get("content", ""))
            if len(_cc) > 100:
                if _cm.get("role") == "tool":
                    ctx.messages[_ci] = {**_cm, "content": _cc.split("\n", 1)[0][:100] + " [budget-trimmed]"}
                elif _cm.get("role") == "user" and "[SYSTEM" in _cc:
                    ctx.messages[_ci] = {**_cm, "content": _cc[:80] + " [trimmed]"}
                elif _cm.get("role") == "assistant" and isinstance(_cc, str):
                    ctx.messages[_ci] = {**_cm, "content": _cc[:120] + "..."}
                _ctx_trimmed += 1
            # 重新估算
            _ctx_est2 = sum(est_tokens(m.get("content", "")) + est_tokens(m.get("tool_calls", "")) + est_tokens(m.get("reasoning_content",""))
                            for m in ctx.messages)
            if _ctx_est2 <= _ctx_limit:
                break
        if _ctx_trimmed > 0:
            log.info(f"  [CONTEXT-BUDGET] trimmed {_ctx_trimmed} msgs, {_ctx_est}→{_ctx_est2} tokens est")
