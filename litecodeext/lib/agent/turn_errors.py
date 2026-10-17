"""agent_stream 回合异常的用户可见文案 (拆分大文件, 从 except 段提取)。"""


def _turn_error_message(_raw_err: str) -> str:
    """回合异常 → 给用户看的人话 (原文只进日志, 不暴露内部实现)。"""
    _el = _raw_err.lower()
    if "maximum context length" in _el or "context_length_exceeded" in _el or "too many tokens" in _el:
        _err_msg = ("\n对话历史太长了，这轮没能跑完。我已经在压缩上下文，"
                    "你把刚才那句再发一次就行；要是还不行，说一声我给你开个新会话。")
    elif "timeout" in _el or "timed out" in _el:
        _err_msg = "\n上游响应超时，这轮中断了。再发一次试试。"
    elif "connect" in _el or "connection" in _el:
        _err_msg = "\n连不上模型服务，这轮中断了。稍后再试。"
    elif "rate limit" in _el or "429" in _el:
        _err_msg = "\n上游限流了，稍等一下再发。"
    elif "已自动重启" in _el:
        _err_msg = "\n模型服务卡住了，已经自动重启，大约 1 分钟后恢复，到时再发一次。"
    elif "server busy" in _el or "maximum pending" in _el or "returned 503" in _el:
        _err_msg = "\n模型服务排队已满，这轮没能发出去。稍等再发；一直这样就是模型服务卡住了，需要重启。"
    else:
        _err_msg = "\n这轮出错中断了。具体原因已记进日志，再发一次试试。"
    return _err_msg
