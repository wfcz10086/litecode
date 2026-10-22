"""lib/friendly_errors.py — 把异常 / 上游报错 / 工具 ERROR 文本归类成给人看的一句话 + 建议。

用法:
    from lib.friendly_errors import classify
    fe = classify("vLLM returned 404: model `foo` does not exist")
    # {"kind": "model_unavailable", "title": "...", "hint": "...",
    #  "actions": ["switch_model", "open_settings", "details"], "details": "<脱敏原文>"}

整轮级错误 (SSE delta.error / 顶层 error) 和工具级错误 (delta.tool_done.error_kind / error_hint) 共用。
原文只放 details, 先脱敏 (去掉 key / token / 密码样式的串), 再截到 DETAILS_MAX。
规则按顺序匹配, 先具体后笼统 (例如 SSH / ComfyUI / 模型不存在 要排在 "网络不通" / "文件不存在" 前面)。
"""
from __future__ import annotations

import re
from typing import Optional

DETAILS_MAX = 2000

# 前端按钮: retry=重试本轮 switch_model=换个模型 open_settings=打开设置 details=查看详情
ACTIONS = ("retry", "switch_model", "open_settings", "details")

# ── 脱敏 ────────────────────────────────────────────────────────
_SECRET_PATTERNS = [
    # sk-xxx / sk-ant-xxx (OpenAI / Anthropic / 各家兼容 key)
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{12,}"), "sk-***"),
    # Authorization: Bearer xxx / Basic xxx
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{12,}"), r"\1 ***"),
    # URL 查询参数里的 key / token
    (re.compile(r"(?i)([?&](?:api[_-]?key|key|token|access_token|auth|signature|sig)=)[^&\s\"']+"), r"\1***"),
    # key=value / "key": "value" 样式 (api_key / token / secret / password ...)
    (re.compile(r"(?i)([\"']?\b[\w\-]*(?:api[_-]?key|apikey|token|secret|password|passwd|pwd|authorization)"
                r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;}&]{3,}"), r"\1***"),
    # sshpass -p xxx
    (re.compile(r"(sshpass\s+-p\s+)\S+"), r"\1***"),
    # user:pass@host 形式的 URL 凭据
    (re.compile(r"(\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:)[^@\s/]+@"), r"\1***@"),
    # 长串十六进制 / base64 风格令牌 (≥40, 纯字母数字), 很可能是密钥
    (re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{40,}\b"), "***"),
]


def scrub(text) -> str:
    """去掉 key / token / 密码样式的串。非字符串先 str()。"""
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    for pat, sub in _SECRET_PATTERNS:
        s = pat.sub(sub, s)
    return s


# ── 分类规则 ────────────────────────────────────────────────────
# (kind, 匹配函数(low_text, tool) -> bool, 标题, 建议, 按钮)
def _has(low: str, *words) -> bool:
    return any(w in low for w in words)


def _re(pattern: str):
    rx = re.compile(pattern, re.I)
    return lambda low, tool: bool(rx.search(low))


_RULES = [
    ("user_interrupt",
     lambda low, tool: _has(low, "用户已点停止", "用户立即插话", "任务已被用户中断", "user interrupt",
                         "interrupted by user", "用户插话了, 本工具暂缓"),
     "已按你的要求停下",
     "需要的话可以点「从失败处继续」, 或直接说接下来怎么做。",
     ["retry"]),
    ("context_overflow",
     lambda low, tool: _has(low, "maximum context length", "context_length_exceeded", "context length",
                         "too many tokens", "prompt is too long", "上下文过长", "上下文超长", "input is too long"),
     "对话太长, 超出了模型能处理的长度",
     "已在自动压缩上下文, 直接重试一次即可; 仍不行就新开一个会话, 或换个上下文更长的模型。",
     ["retry", "switch_model", "details"]),
    ("comfyui_model_missing",
     lambda low, tool: ("comfy" in low or (tool or "").startswith("comfy"))
     and _has(low, "value not in list", "not in list", "ckpt_name", "lora_name", "unet_name",
              "missing model", "model not found", "缺少模型", "模型文件不存在", "no such file"),
     "ComfyUI 缺少所需的模型文件",
     "到 ComfyUI 的 models 目录补齐对应模型 (检查点 / LoRA / VAE), 或换一个已安装模型的工作流。",
     ["retry", "details"]),
    ("comfyui_busy",
     lambda low, tool: ("comfy" in low or (tool or "").startswith("comfy"))
     and _has(low, "queue", "busy", "排队", "队列", "pending"),
     "ComfyUI 正忙, 任务在排队",
     "等前面的任务跑完再试; 一直卡住可以在 ComfyUI 页面清空队列。",
     ["retry", "details"]),
    ("ssh_unreachable",
     lambda low, tool: (tool in ("ssh_exec", "ssh_upload", "ssh_download", "sftp_transfer") or "ssh" in low)
     and _has(low, "could not resolve hostname", "unknown host", "未知主机", "未配置", "not configured",
              "no such host", "connection refused", "connection timed out", "timed out", "unreachable",
              "permission denied (publickey", "authentication failed", "host key verification failed",
              "连不上", "无法连接", "not found in ssh", "unknown alias", "未找到主机", "找不到主机",
              "name or service not known", "gaierror", "nodename nor servname", "no route to host",
              "temporary failure in name resolution", "noValidConnectionsError".lower(), "unable to connect",
              "connection reset", "banner", "sshexception",
              "需要 password", "key_file", "未保存", "no saved host", "没有保存"),
     "SSH 连不上目标主机",
     "检查主机别名 / IP 和端口是否写对 (别名要先用 ssh_save_host 保存凭据), 目标机是否开机、SSH 服务是否在跑, 以及密钥或密码是否有效。",
     ["retry", "details"]),
    ("model_unavailable",
     lambda low, tool: ("model" in low or "模型" in low)
     and _has(low, "does not exist", "not exist", "not found", "model_not_found", "no such model",
              "unknown model", "invalid model", "不存在", "不可用", "not available", "is not supported",
              "returned 404", "http 404", "404 not found", "未找到"),
     "所选模型不存在或暂不可用",
     "换一个可用的模型再试; 如果是自建模型服务, 确认它已启动且模型名与配置一致。",
     ["switch_model", "open_settings", "details"]),
    ("auth",
     lambda low, tool: bool(re.search(r"\b(401|403)\b", low)) or _has(
         low, "unauthorized", "invalid api key", "incorrect api key", "invalid_api_key", "api key",
         "authentication", "forbidden", "密钥无效", "密钥失效", "鉴权失败", "invalid token", "token expired"),
     "密钥无效或没有权限 (401/403)",
     "到设置里检查该模型 / 服务的 API Key 是否填对、是否过期或欠费。",
     ["open_settings", "switch_model", "details"]),
    ("rate_limit",
     lambda low, tool: bool(re.search(r"\b429\b", low)) or _has(
         low, "rate limit", "rate_limit", "too many requests", "quota", "限流", "请求过于频繁", "额度"),
     "请求太频繁, 被上游限流了 (429)",
     "稍等半分钟再重试; 经常出现可以换个模型或调低并发。",
     ["retry", "switch_model", "details"]),
    ("disk_full",
     lambda low, tool: _has(low, "no space left on device", "disk quota exceeded", "磁盘已满", "空间不足", "enospc"),
     "磁盘空间不足",
     "清理工作区里的大文件或临时文件后再试。",
     ["retry", "details"]),
    ("upstream_5xx",
     lambda low, tool: bool(re.search(r"(returned|http|status|code)[\s:=]*5\d\d\b", low)) or _has(
         low, "bad gateway", "service unavailable", "internal server error", "gateway timeout",
         "server busy", "maximum pending", "已自动重启", "排队已满", "upstream error"),
     "上游服务出故障了 (5xx)",
     "通常是模型服务临时不稳, 稍后重试; 持续出现就换个模型。",
     ["retry", "switch_model", "details"]),
    ("timeout",
     lambda low, tool: _has(low, "timeout", "timed out", "超时", "time out", "deadline exceeded"),
     "等待响应超时",
     "可能是网络慢或任务太重, 重试一次; 反复超时可以把任务拆小些。",
     ["retry", "details"]),
    ("network",
     lambda low, tool: _has(low, "connection refused", "connecterror", "connect error", "failed to connect",
                         "connection reset", "connection aborted", "network is unreachable", "unreachable",
                         "name or service not known", "temporary failure in name resolution",
                         "nodename nor servname", "getaddrinfo", "dns", "无法连接", "连不上", "连接中断",
                         "连接失败", "remote end closed", "server disconnected", "network error",
                         "failed to fetch", "all connection attempts failed"),
     "网络不通, 连不上服务",
     "检查网络和服务地址 (base_url) 是否正确、服务是否在运行, 然后重试。",
     ["retry", "open_settings", "details"]),
    ("permission",
     lambda low, tool: _has(low, "permission denied", "permissionerror", "eacces", "operation not permitted",
                         "权限不足", "没有权限", "read-only file system", "access is denied", "拒绝访问"),
     "权限不足, 操作被拒绝",
     "换一个有写权限的路径, 或确认当前用户有权限访问该文件 / 目录。",
     ["retry", "details"]),
    ("file_not_found",
     lambda low, tool: _has(low, "no such file or directory", "filenotfounderror", "file not found",
                         "文件不存在", "路径不存在", "does not exist", "not found:", "cannot find the path",
                         "找不到文件", "no such file"),
     "文件或路径不存在",
     "确认路径拼写和大小写是否正确, 或先列一下目录看看文件实际在哪里。",
     ["retry", "details"]),
    ("bad_args",
     lambda low, tool: _has(low, "参数非法", "invalid json", "missing required", "缺少参数", "参数错误",
                         "unexpected keyword argument", "invalid arguments"),
     "工具参数有误",
     "模型通常会根据报错自动修正后重试; 一直出错可以把要求说得更具体些。",
     ["retry", "details"]),
]

_UNKNOWN = ("unknown", "出错了", "可以先重试一次; 反复出现请展开「查看详情」看原始报错。", ["retry", "details"])


def classify(err, *, tool: Optional[str] = None, status: Optional[int] = None) -> dict:
    """异常 / 报错文本 → {kind, title, hint, actions, details}。永不抛异常。"""
    try:
        if isinstance(err, BaseException):
            raw = f"{type(err).__name__}: {err}"
        else:
            raw = "" if err is None else str(err)
        if status:
            raw = f"HTTP {status} {raw}".strip()
        low = raw.lower()
        for kind, match, title, hint, actions in _RULES:
            try:
                hit = match(low, tool or "")
            except Exception:
                hit = False
            if hit:
                break
        else:
            kind, title, hint, actions = _UNKNOWN
        details = scrub(raw)
        if len(details) > DETAILS_MAX:
            details = details[:DETAILS_MAX] + f"…(共 {len(raw)} 字, 已截断)"
        return {"kind": kind, "title": title, "hint": hint, "actions": list(actions), "details": details}
    except Exception:  # 分类本身绝不能影响主流程
        return {"kind": "unknown", "title": _UNKNOWN[1], "hint": _UNKNOWN[2],
                "actions": list(_UNKNOWN[3]), "details": ""}


def empty_reply(details: str = "") -> dict:
    """模型整轮什么都没返回 (P46 空响应) 的专用说法。"""
    return {"kind": "empty_reply", "title": "模型这一轮没有返回任何内容",
            "hint": "多半是上游临时超时或限流, 重试一次; 反复出现就换个模型。",
            "actions": ["retry", "switch_model", "details"], "details": scrub(details)[:DETAILS_MAX]}


def kinds() -> list:
    """所有分类 (含 unknown / empty_reply), 文档 / 测试用。"""
    return [r[0] for r in _RULES] + ["empty_reply", "unknown"]
