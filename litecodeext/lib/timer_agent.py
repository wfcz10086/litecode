"""定时 agent 使用现有 Web 后台轮次，不再另起争抢会话的执行路径。"""


async def call_timer_agent(prompt, session_id):
    from fastapi import HTTPException
    from routers.chat_router import REGISTRY, start_chat_turn
    if not session_id or not prompt.strip():
        return 'ERROR: 定时 agent 缺少会话或提示词'
    if REGISTRY.running(session_id):
        return '跳过：目标会话正在执行，本次不抢占、不重复启动'
    try:
        start_chat_turn(session_id, prompt, {"_usage_source": "timer"})
    except HTTPException as exc:
        if exc.status_code == 409:
            return '跳过：目标会话正在执行，本次不抢占、不重复启动'
        raise
    return f'已启动后台轮次：{session_id}；执行过程与最终结果见原 Web 会话'
