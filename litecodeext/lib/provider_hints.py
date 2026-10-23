"""lib/provider_hints.py — 「能力接入」测试失败时, 把 HTTP 状态 / 平台错误码 / 异常翻译成一句人话 + 下一步建议。

explain(p, r) 的 r 是 provider_probe.test 的结果 {ok, status?, error, code?, errmsg?, net?, timeout?, auth?, has_secret?}。
错误码出处: 钉钉 310000 (安全设置校验失败: sign not match / keywords not in content / ip not in whitelist /
invalid timestamp)、飞书 19021 签名 / 19022 IP / 19024 关键词 (open.feishu.cn add-custom-bot 文档)、企微 93000 webhook 无效。
"""
from __future__ import annotations

from .provider_infer import has_placeholder

_PLATFORM = {"dingtalk_webhook": "钉钉", "feishu_webhook": "飞书", "wecom_webhook": "企业微信"}


def _notify(p: dict, r: dict) -> str | None:
    code = str(r.get("code") if r.get("code") is not None else "")
    msg = str(r.get("errmsg") or r.get("error") or "").lower()
    who = _PLATFORM.get(p.get("protocol"), "群机器人")
    secret = r.get("has_secret", bool((p.get("extra") or {}).get("secret")))
    if "sign" in msg or code == "19021":
        if not secret:
            return (f"{who}机器人开了「加签 / 签名校验」, 但你没写密钥: 去机器人的安全设置里复制密钥"
                    f"{' (SEC 开头)' if who == '钉钉' else ''}, 写进这段话里再测试。")
        return (f"加签不对: 密钥和{who}机器人安全设置里的不一致 (重新复制一遍, 别多空格), "
                f"或者这台电脑的时间不准 (签名要求和标准时间相差不超过 1 小时)。")
    if "timestamp" in msg:
        return "时间戳无效: 这台电脑的时间不准, 校准系统时间后再试。"
    if "keyword" in msg or "key words" in msg or code == "19024":
        return (f"{who}机器人设置了「自定义关键词」, 测试消息里没有这个关键词: 把关键词加一个「LiteCode」, "
                f"或者安全设置改用「加签」。")
    if "ip" in msg.split() or "whitelist" in msg or "ip not" in msg or code == "19022":
        return f"{who}机器人开了「IP 白名单」, 这台电脑的公网 IP 不在里面: 把它加进白名单, 或改用加签。"
    if code == "93000" or "invalid webhook" in msg or "token" in msg and ("not exist" in msg or "invalid" in msg):
        return f"Webhook 地址无效: 机器人可能被删了或地址复制不全, 去{who}群里重新复制完整的 Webhook 地址。"
    if code in ("45009", "130101", "9499") or "limit" in msg or "频率" in msg:
        return "发得太频繁被限流了: 等一分钟再试 (群机器人每分钟大约只能发 20 条)。"
    if code:
        return f"{who}拒绝了这条消息 (错误码 {code}): 对照上面的说明检查 Webhook 地址和安全设置。"
    return None


def explain(p: dict, r: dict) -> str:
    err = str(r.get("error") or "")
    low = err.lower()
    st = r.get("status")
    text = (p.get("usage") or "") + " " + (p.get("base_url") or "")
    if has_placeholder(text) and (st in (None, 400, 401, 403, 404) or "webhook" in low or "没填" in err):
        return "示例里的「粘贴…」还没换成你自己的内容: 把 Key / 地址 / 密钥替换掉再测试。"
    if p.get("protocol") == "smtp" or "smtp" in low:
        if "authentication" in low or "535" in err or "auth" in low:
            return "邮箱登录失败: 密码处要填「授权码」, 不是邮箱登录密码; 并确认邮箱设置里已经开启 SMTP 服务。"
        if "要填" in err or "没填" in err:
            return "邮箱信息没写全: 需要 SMTP 服务器、发件邮箱、授权码和收件人。"
        if any(x in low for x in ("refused", "timed out", "timeout", "gaierror", "name or service", "unreachable")):
            return "连不上邮箱服务器: 检查服务器地址和端口 (QQ / 163 常用 465), 以及这台电脑能否上网。"
    if r.get("code") is not None or p.get("kind") == "notify" and p.get("protocol", "").endswith("webhook"):
        h = _notify(p, r)
        if h:
            return h
    if r.get("timeout"):
        return "等了很久对方都没回应: 服务可能很忙或网络不通, 稍后再试; 内网地址要确认这台电脑能访问到。"
    if r.get("net") or "连不上" in err:
        return ("地址打不开: 检查地址有没有写错、服务是不是开着、端口对不对; "
                "内网地址 (10.x / 192.168.x) 要确认 LiteCode 所在的电脑和它在同一个网络。")
    if "没填" in err or "要填" in err:
        return "信息没写全: " + err
    if "shell 变量" in err or "curl" in low and ("解析" in err or "不支持" in err):
        return "curl 示例写法 LiteCode 测不了: " + err
    if r.get("auth") or st in (401, 403) or "鉴权" in err:
        return "Key 不对或没有权限: 去平台重新复制 Key (注意别多复制空格), 并确认账户已开通这个服务。"
    if st == 402 or any(x in low for x in ("insufficient", "balance", "余额", "quota", "arrear")):
        return "账户余额或额度不足: 去平台充值或查看额度后再试。"
    if st == 404:
        return "地址路径不对 (404): 对照官方文档检查地址, 常见是少了 /v1 之类的部分, 或模型名写错。"
    if st == 429:
        return "调用太频繁或额度用完了 (429): 稍等一会儿再试, 或去平台查看额度。"
    if st and st >= 500:
        return f"对方服务出错了 (HTTP {st}): 一般是平台临时故障, 稍后再试。"
    if st == 400:
        return "请求参数不对 (400): 对照官方文档检查 curl 里的模型名和字段。"
    if "comfyui" in low or "system_stats" in low:
        return "这个地址不像 ComfyUI: 确认填的是 ComfyUI 的地址和端口 (默认 8188), 启动时加了 --listen。"
    return "测试没通过, 原始返回见下方; 可以把它发给 LiteCode, 让它帮你看看哪里不对。"
