"""agent_stream 回合收尾持久化段 (拆分大文件, 从 finally 原样提取)。"""
from lib import config as _cfg   # 模型热切换后读当前值 (reload_model 更新其模块变量), 不在导入时拷快照
import threading
from lib.config import BASE
from lib.config import HAS_MEMORY
from lib.config import SESSIONS_DISK
from lib.config import TEMPLATE_DIR
from lib.config import WORKSPACE
from lib.config import auto_update_tools as _auto_update_tools
from lib.config import auto_update_user as _auto_update_user
from lib.config import log
from lib.config import mem_get as _mem_get
from lib.config import smart_auto_memory as _smart_auto_memory
from lib.session import save_history as _save_history
from lib.turn_context import strip as _turn_strip


def _turn_persist(_ctx, _test_state, _pm_state, _batch_state):
    """回合结束: 保存历史、后台记忆三线程 (compress/fast/smart)、task 边界自动快照。
    原 agent_stream finally 段的 try 体; 外层 try/except/finally (清中断标记+释放会话锁) 留在原处。"""
    if _ctx.session_id and _ctx.new_msgs:
        _save_history(_ctx.session_id, _ctx.base_history, _ctx.new_msgs)
        all_msgs = _turn_strip(_ctx.base_history) + _ctx.new_msgs   # 记忆提取只看用户原话
        if HAS_MEMORY:
            # [v1.0] 所有 memory 压缩/更新都放后台线程
            # 之前: check_and_compact 同步调 httpx.post(timeout=180) 压缩
            #       → 阻塞 finally 块 → SSE 流无法关闭
            # [v1.5 FIX] compress 和 rule/auto-learn/smart 分离到两个线程:
            #   T1 (compress): LLM 压缩, 最慢, 可能 600s 超时, 不阻塞其他
            #   T2 (fast): rule-memory on_task_end + auto_update_user/tools, 秒级
            #   T3 (smart): LLM 智能补充, 分钟级, 和 compress 并行
            # 理由: 以前串行导致 compress 超时 600s 把 rule-memory 也拖 10 分钟。
            def _bg_compress_worker(_all_msgs):
                try:
                    mgr = _mem_get(
                        workspace=WORKSPACE, session_id=_ctx.session_id,
                        vllm_url=_cfg.BACKEND_URL, model_id=_cfg.MODEL_ID, api_key=_cfg.API_KEY,
                        context_window=_cfg.CONTEXT_WINDOW,
                    )
                    mgr.check_and_compact(_all_msgs, _force_async=True)
                except Exception as _bg_e:
                    log.warning(f"  [memory-compress:{_ctx.sid_tag}] fail: {_bg_e}")

            def _bg_fast_worker(_all_msgs):
                try:
                    _auto_update_user(
                        workspace=WORKSPACE, session_id=_ctx.session_id,
                        sessions_dir=SESSIONS_DISK, template_dir=TEMPLATE_DIR,
                        base_dir=BASE, messages=_all_msgs,
                    )
                except Exception:
                    pass
                try:
                    _auto_update_tools(
                        workspace=WORKSPACE, session_id=_ctx.session_id,
                        sessions_dir=SESSIONS_DISK, template_dir=TEMPLATE_DIR,
                        base_dir=BASE, messages=_all_msgs,
                    )
                except Exception:
                    pass
                try:
                    mgr_fast = _mem_get(
                        workspace=WORKSPACE, session_id=_ctx.session_id,
                        vllm_url=_cfg.BACKEND_URL, model_id=_cfg.MODEL_ID,
                        api_key=_cfg.API_KEY, context_window=_cfg.CONTEXT_WINDOW,
                    )
                    mgr_fast.on_task_end(_all_msgs)
                except Exception as _fe:
                    log.warning(f"  [memory-fast:{_ctx.sid_tag}] fail: {_fe}")

            def _bg_smart_worker(_all_msgs):
                try:
                    mgr2 = _mem_get(
                        workspace=WORKSPACE, session_id=_ctx.session_id,
                        vllm_url=_cfg.BACKEND_URL, model_id=_cfg.MODEL_ID,
                        api_key=_cfg.API_KEY, context_window=_cfg.CONTEXT_WINDOW,
                    )
                    _smart_auto_memory(_cfg.BACKEND_URL, _cfg.MODEL_ID, _cfg.API_KEY, _all_msgs, mgr2)
                except Exception as _sm_e:
                    log.warning(f"  [memory-smart:{_ctx.sid_tag}] fail: {_sm_e}")

            _snapshot = list(all_msgs)  # 快照避免竞争
            for _worker in (_bg_compress_worker, _bg_fast_worker, _bg_smart_worker):
                threading.Thread(
                    target=_worker, args=(_snapshot,), daemon=True,
                ).start()
        log.info(f"  \033[32m[persist:{_ctx.sid_tag}] saved {len(_ctx.new_msgs)} msgs + memory (bg)\033[0m")

        # [SNAPSHOT 2026-09-03] task 边界自动快照 —— 给 agent 改的项目留可回滚锚点。
        # 复用本回合写入的文件路径推断项目根 (不依赖 _pm_state, 后者对绝对路径项目为空)。
        # 后台线程跑, 失败绝不影响主流程。
        try:
            _written = list(getattr(_test_state, "src_files_written", [])) + \
                       list(getattr(_test_state, "test_files_written", []))
            if _written:
                from lib.snapshot import infer_project_root as _infer_root, snapshot as _do_snap
                _proj = _infer_root(_written, fallback=(_pm_state.project_root or None))
                if _proj:
                    _snap_label = f"turn-end iters={_batch_state.plan_gate_tool_call_iters} writes={_batch_state.exec_gate_write_calls}"
                    threading.Thread(
                        target=lambda: _do_snap(WORKSPACE, _proj, _snap_label),
                        daemon=True,
                    ).start()
        except Exception as _snap_exc:
            log.warning(f"  [snapshot:{_ctx.sid_tag}] 挂载失败 (已忽略): {_snap_exc}")
