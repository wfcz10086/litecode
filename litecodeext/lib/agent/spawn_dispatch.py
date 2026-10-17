"""spawn_agent 工具调用的分派: 子代理运行、超时与中断处理。

从 litecode_server.py 原样搬出 (拆分大文件), 函数体未改动; litecode_server 仍 re-export 这些名字。
"""
import asyncio
from lib.agent.subagent import _run_subagent
from lib.agent.subagent import _run_subagent_safe
from lib.config import TASK_TIMEOUT
from lib.config import log
from lib.session import _interrupt_flags
from lib.session import _interrupt_lock
from tool_dispatch import execute_tool


# ── [AGENT_STREAM_REFACTOR M7c 2026-09-18] spawn_agent 分派段, 迁自主循环
#    (原 ~120 行: subagent 注入/超时拉伸/多代理路由/单agent/SSE drain 透传/
#    中断取消/心跳/结果回收)。含 yield → 异步子生成器 (M6a _turn_finalize 同款),
#    结果经 out 持有者回传: out["result"], out["effective_timeout"]。
#    queue 与 emit 回调为调用方每轮闭包, 经参数传入; 其余依赖均模块级。──
async def _dispatch_spawn_agent(fn_args: dict, ctx, sse_emit, sse_queue, out):
    """spawn_agent 工具分派: 透传子代理 SSE + 心跳 + 父会话中断级联取消。"""
    # [v1.0] 确保 execute_tool 已注入到 subagent 模块
    try:
        from lib.agent.subagent import set_execute_tool, execute_tool as _sa_et
        if _sa_et is None:
            set_execute_tool(execute_tool)
    except Exception:
        pass
    # [v1.0] 动态延长超时: spawn_agent(writer) 需要大量时间
    out["effective_timeout"] = max(out["effective_timeout"], TASK_TIMEOUT * 6)

    # [v1.0] 检测多代理编排模式
    # [batch-items-2026-05] 加 batch_items 模式 (批量同类任务, 比如 N 章小说)
    _multi_tasks = (fn_args.get("dag_tasks")
                   or fn_args.get("pipeline_tasks")
                   or fn_args.get("parallel_tasks")
                   or fn_args.get("competitive_tasks")
                   or fn_args.get("batch_items"))
    # [v1.0] 将子代理包装为 task, 同时 drain SSE 队列做实时透传 + 中断检查
    if _multi_tasks and isinstance(_multi_tasks, list) and len(_multi_tasks) > 0:
        # 路由到 multi-agent 编排器
        try:
            from multi_agent import handle_spawn_agent_enhanced
            _sa_coro = handle_spawn_agent_enhanced(
                fn_args,
                run_subagent_fn=_run_subagent,
                sse_emit=sse_emit,
                session_id=ctx.session_id,
                tracer=ctx.tracer,  # [v1.1] 让 DAG 事件进日志
            )
        except ImportError:
            _sa_coro = None
            out["result"] = "ERROR: multi_agent.py not available"
    else:
        # 单 agent 模式
        task_arg   = fn_args.get("task", "")
        agent_type = fn_args.get("agent_type", "coder")
        context_arg = fn_args.get("context", "")
        # [v1.0] max_iter 调大后, timeout 相应放宽
        # coder 50 iter * 平均 10s = 500s, 给 600s
        # researcher 每搜 30-40s, 8 次搜 + 整理 = 400s
        _sa_timeout = 600
        if agent_type == "writer":
            _sa_timeout = 900
        elif agent_type == "researcher":
            _sa_timeout = 400
        elif agent_type == "critic":
            _sa_timeout = 180
        _sa_coro = _run_subagent_safe(
            task_arg, agent_type, context_arg,
            sse_emit=sse_emit,
            timeout=_sa_timeout,
            parent_sid=ctx.session_id,
        )

    if _sa_coro is not None:
        _sa_task = asyncio.ensure_future(_sa_coro)
        try:
            while not _sa_task.done():
                # drain 队列中已有事件（实时透传到前端）
                drained = 0
                while True:
                    try:
                        _chunk = sse_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield _chunk
                    drained += 1
                    if drained >= 50:  # 防止 starvation
                        break
                # 等队列新事件 / 任务完成 / 超时心跳
                _waiters = [
                    asyncio.ensure_future(sse_queue.get()),
                    _sa_task,
                ]
                done, pending = await asyncio.wait(
                    _waiters, timeout=5,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # 清理 queue.get 的 waiter
                for _p in pending:
                    if _p is not _sa_task:
                        _p.cancel()
                # 如果 queue.get 完成了, 把取出的 chunk 发出去
                for _d in done:
                    if _d is not _sa_task:
                        try:
                            yield _d.result()
                        except Exception:
                            pass
                # [v1.0] 中断检查: 父 session 被标记中断 -> 取消子任务
                with _interrupt_lock:
                    if ctx.session_id in _interrupt_flags:
                        log.warning(f"  [INTERRUPT-SPAWN] cancelling subagent task for sid={ctx.session_id}")
                        _sa_task.cancel()
                        try:
                            await _sa_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        break
                # 无事件时发心跳, 避免 SSE 连接被关
                if not done:
                    yield f": heartbeat spawn_agent\n\n"
            # 任务完成 → 获取结果
            if _sa_task.done() and not _sa_task.cancelled():
                try:
                    out["result"] = _sa_task.result()
                except Exception as e:
                    out["result"] = f"ERROR in subagent: {e}"
            elif _sa_task.cancelled():
                out["result"] = "[子代理已取消: 父会话被中断]"
            # drain 队列中剩余事件
            while True:
                try:
                    yield sse_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        except Exception as e:
            out["result"] = f"ERROR in subagent dispatch: {e}"
