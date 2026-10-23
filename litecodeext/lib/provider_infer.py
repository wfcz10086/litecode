"""lib/provider_infer.py — 从「能力接入」卡片的明文 (一两句大白话 + curl 示例) 里尽力识别结构化字段。

用户只写标题 + 一段明文 (如 "钉钉运营群 webhook https://oapi.dingtalk.com/robot/send?access_token=xx 加签密钥 SECxx"),
这里猜出 kind / protocol / base_url / api_key / model / extra.secret 等, 让测试、自动路由、send_notify 能直接用。
识别不出也没关系: 卡片照样保存, agent 读明文自己调用。纯函数, 不联网。

infer(p) → {字段: 值}; 字段名 "extra.xxx" 表示 extra 里的键。只返回识别到的, 由 providers.auto_fill 决定填不填。
"""
from __future__ import annotations

import re
import shlex
import urllib.parse

_URL = re.compile(r"https?://[^\s'\"<>，。；、）)]+")
_HOSTPORT = re.compile(r"(?<![\w/.@-])((?:\d{1,3}(?:\.\d{1,3}){3}|localhost|[a-zA-Z][\w-]*(?:\.[\w-]+)+):\d{2,5})(?![\w.])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PLACEHOLDER = re.compile(r"粘贴|换成|你的|这里|填写|待定|官方文档|xxx|\.\.\.|…|\{")
_KEY_LABEL = re.compile(r"(?im)^\s*(?:api[ _-]?key|key|密钥|令牌|api 密钥)\s*[:：=]\s*(\S+)")
_PWD_LABEL = re.compile(r"(?im)(?:授权码|密码|password)\s*[:：=]\s*(\S+)")
_SECRET_LABEL = re.compile(r"(?i)(?:加签密钥|签名校验密钥|签名密钥|secret)(?:\s*[:：=]\s*|\s+)([A-Za-z0-9+/=_-]{8,})")
_DING_SEC = re.compile(r"\bSEC[0-9a-fA-F]{16,}\b")
_BEARER = re.compile(r"Bearer\s+([^\s'\"{}]+)")
_BARE_KEY = re.compile(r"\b((?:sk|tvly)-[A-Za-z0-9_-]{16,})\b")
_ADDR_LABEL = re.compile(r"(?im)^\s*(?:地址|服务器|smtp 服务器|base_url|url|endpoint|服务地址)\s*[:：=]\s*(\S+)")
_MODEL_JSON = re.compile(r'"model"\s*:\s*"([^"{}]+)"')
_MODEL_LABEL = re.compile(r"(?im)^\s*(?:模型|model)\s*[:：=]\s*([^\s{}]+)")

# 关键词 → kind (按顺序匹配; 视频要在生图前, 因为视频说明里常有"首帧图")
_KIND_WORDS = [
    ("notify", r"webhook|机器人|钉钉|飞书|企微|企业微信|dingtalk|feishu|lark|wecom|smtp|邮件|邮箱|通知"),
    ("video", r"视频|video|seedance|hailuo|海螺|可灵|kling|h3_"),
    ("image", r"生图|画图|绘图|图片生成|文生图|改图|image|seedream|qwen-image|flux|comfyui"),
    ("tts", r"配音|语音合成|朗读|tts|speech|cosyvoice"),
    ("asr", r"语音识别|转文字|听写|asr|transcri|whisper"),
    ("music", r"音乐|配乐|bgm|歌曲|music|suno"),
    ("search", r"搜索|search|searxng|tavily|博查|bocha"),
    ("rerank", r"重排|rerank"),
    ("embedding", r"向量|embedding"),
    ("storage", r"对象存储|\bs3\b|\boss\b|\bcos\b|minio|存储桶"),
]

# OpenAI 兼容路径 → (protocol, kind)
_OPENAI_PATHS = [
    ("/images/generations", "openai_images", "image"), ("/images/edits", "openai_images", "image"),
    ("/audio/speech", "openai_tts", "tts"), ("/audio/transcriptions", "openai_asr", "asr"),
    ("/embeddings", "openai_embeddings", "embedding"), ("/rerank", "openai_rerank", "rerank"),
]


def _clean(v: str) -> str:
    return (v or "").strip().strip("'\"`,，。;；").strip()


def _real(v: str) -> bool:
    """是用户填的真实值, 不是模板里的 "粘贴你的 Key" 之类占位。"""
    v = _clean(v)
    return bool(v) and not _PLACEHOLDER.search(v) and len(v) >= 4


def _open_quote(cmd: str) -> bool:
    try:
        shlex.split(cmd)
        return False
    except ValueError:      # No closing quotation: 引号里的内容跨了行 (多行 JSON / 歌词)
        return True


def curls(text: str) -> list[str]:
    """明文里所有 curl 命令: 支持行尾 \\ 续行, 也支持 -d '{ … }' 这种引号里跨多行的写法 (换行原样保留);
    跳过 # 注释行。"""
    out, lines = [], (text or "").splitlines()
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        m = re.search(r"(?:^|[\s$(=])(curl\s.*)$", s)
        if m and not s.startswith("#"):
            cmd, start = m.group(1), i
            while i + 1 < len(lines) and i - start < 300:
                if cmd.rstrip().endswith("\\"):
                    cmd = cmd.rstrip()[:-1] + " " + lines[i + 1].strip()
                elif _open_quote(cmd):
                    cmd = cmd + "\n" + lines[i + 1]
                else:
                    break
                i += 1
            out.append(cmd)
        i += 1
    return out


def test_curl(text: str) -> str | None:
    """测试用的 curl: 紧跟在含「测试用」注释行后面的那条; 没有就取第一条。"""
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#") and "测试用" in ln:
            rest = curls("\n".join(lines[i + 1:]))
            if rest:
                return rest[0]
    allc = curls(text)
    return allc[0] if allc else None


def is_probe_curl(text: str) -> bool:
    """明文里有「测试用」标记 (只验证 Key, 不提交任务)。"""
    return any(ln.strip().startswith("#") and "测试用" in ln for ln in (text or "").splitlines())


def _curl_parts(cmd: str) -> tuple[str, str]:
    """(url, data) —— 解析失败返回空串, 不抛异常。"""
    try:
        toks = shlex.split(cmd)
    except ValueError:
        return "", ""
    url, data = "", ""
    skip_next = False
    for i, t in enumerate(toks[1:], 1):
        if skip_next:
            skip_next = False
            continue
        if t in ("-d", "--data", "--data-raw", "--json", "--data-binary"):
            data = toks[i + 1] if i + 1 < len(toks) else ""
            skip_next = True
        elif t in ("-H", "--header", "-X", "--request", "-F", "--form", "-o", "-m", "-u", "-A", "-e", "-b"):
            skip_next = True
        elif not t.startswith("-") and not url and re.match(r"https?://|[\w.-]+:\d+|\{base_url\}", t):
            url = t
    return url, data


def _origin(url: str) -> str:
    u = urllib.parse.urlparse(url)
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""


def kind_of(text: str) -> str | None:
    low = (text or "").lower()
    for k, pat in _KIND_WORDS:
        if re.search(pat, low):
            return k
    return None


def infer(p: dict) -> dict:
    name = str(p.get("name") or "")
    text = str(p.get("usage") or "")
    both = name + "\n" + text
    low = both.lower()
    out: dict = {}
    cl = curls(text)
    urls = [_clean(u) for u in _URL.findall(text)]
    first_url, first_data = _curl_parts(cl[0]) if cl else ("", "")

    # ── 地址 / 模型 / key 的显式标注 ──
    m = _ADDR_LABEL.search(text)
    addr = _clean(m.group(1)) if m and _real(m.group(1)) else ""
    mm = _MODEL_JSON.search(first_data or "") or _MODEL_LABEL.search(text)
    if mm and _real(mm.group(1)):
        out["model"] = _clean(mm.group(1))

    # ── 通知: webhook ──
    hook = next((u for u in urls if "/robot/send" in u or "/open-apis/bot/v2/hook/" in u
                 or "/cgi-bin/webhook/send" in u), "")
    if hook and _real(hook.split("?", 1)[-1] if "/robot/send" in hook or "/webhook/send" in hook
                      else hook.rsplit("/", 1)[-1]):
        out["base_url"] = hook
    if hook:
        out["kind"] = "notify"
        if "/robot/send" in hook or ("钉钉" in both or "dingtalk" in low) and "/send" in hook:
            out["protocol"] = "dingtalk_webhook"
            s = _DING_SEC.search(text)
            if s:
                out["extra.secret"] = s.group(0)
        elif "/open-apis/bot/" in hook:
            out["protocol"] = "feishu_webhook"
        else:
            out["protocol"] = "wecom_webhook"
        if out["protocol"] != "dingtalk_webhook":
            s = _SECRET_LABEL.search(text)
            if s and _real(s.group(1)) and not s.group(1).startswith("http"):
                out["extra.secret"] = s.group(1)
        return out

    # ── 通知: SMTP 邮件 ──
    sm = re.search(r"\bsmtp\.[\w.-]+(?::\d+)?", text, re.I)
    if sm:
        out.update(kind="notify", protocol="smtp", base_url=sm.group(0))
        mails = [e for e in _EMAIL.findall(text) if _real(e)]
        frm = re.search(r"(?:发件|账号|用户名|from)[^\n]*?([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", text, re.I)
        sender = frm.group(1) if frm and _real(frm.group(1)) else (mails[0] if mails else "")
        if sender:
            out["extra.username"] = sender
            out["extra.from"] = sender
        to_m = re.search(r"(?:发给|收件人|收件|to)\s*[:：=]?\s*([^\n]+)", text, re.I)
        to = [e for e in _EMAIL.findall(to_m.group(1)) if _real(e)] if to_m else [e for e in mails if e != sender]
        if to:
            out["extra.to"] = ", ".join(to)
        pw = _PWD_LABEL.search(text)
        if pw and _real(pw.group(1)):
            out["api_key"] = _clean(pw.group(1))
        return out

    # ── S3 兼容存储: Access Key / Secret Key 两行 ──
    ak = re.search(r"(?im)^\s*(?:access[ _-]?key(?:[ _-]?id)?|accesskey id|secretid)\s*[:：=]\s*(\S+)", text)
    sk = re.search(r"(?im)^\s*(?:secret[ _-]?(?:access[ _-]?)?key|accesskey secret|secretkey)\s*[:：=]\s*(\S+)", text)
    if ak or sk:
        out.update(kind="storage", protocol="s3")
        if addr:
            out["base_url"] = addr
        if ak and _real(ak.group(1)):
            out["extra.access_key_id"] = _clean(ak.group(1))
        if sk and _real(sk.group(1)):
            out["api_key"] = _clean(sk.group(1))
        rg = re.search(r"(?im)^\s*(?:区域|地域|region)\s*[:：=]\s*([\w-]+)", text)
        if rg and _real(rg.group(1)):
            out["extra.region"] = rg.group(1)
        return out

    # ── key ──
    k = _KEY_LABEL.search(text)
    if k and _real(k.group(1)):
        out["api_key"] = _clean(k.group(1))
    else:
        for c in cl:
            b = _BEARER.search(c)
            if b and _real(b.group(1)):
                out["api_key"] = _clean(b.group(1))
                break
        else:
            bk = _BARE_KEY.search(text)
            if bk:
                out["api_key"] = bk.group(1)

    # ── ComfyUI ──
    if "comfy" in low:
        hp = addr or next((u for u in urls), "") or (_HOSTPORT.search(text).group(1) if _HOSTPORT.search(text) else "")
        if hp and "{" not in hp:
            hp = re.sub(r"(https?://[^/]+).*", r"\1", hp)
            out["base_url"] = hp
        out["protocol"] = "comfyui"
        out["kind"] = "video" if re.search(r"视频|video|h3_", low) else "image"
        out.pop("model", None)
        return out

    # ── SearXNG ──
    if "searxng" in low or re.search(r"/search\?[^\s'\"]*format=json", text):
        u = addr or next((x for x in urls if "/search" in x), "") or first_url
        if u and "{" not in u.split("/search")[0]:
            out["base_url"] = u.split("/search")[0].rstrip("/")
        out.update(protocol="searxng", kind="search")
        return out

    # ── OpenAI 兼容路径 ──
    probe_urls = [_curl_parts(c)[0] for c in cl] or urls
    for u in probe_urls:
        for path, proto, kind in _OPENAI_PATHS:
            if path in u:
                out.update(protocol=proto, kind=kind)
                pre = u.split(path)[0].rstrip("/")
                if addr or not pre.startswith("{"):
                    out["base_url"] = addr or pre
                return out

    # ── 其它: 地址取标注 / 第一条 curl 的站点; 类型按关键词 ──
    if addr:
        out["base_url"] = addr
    elif first_url and first_url.startswith("http"):
        o = _origin(first_url)
        if o:
            out["base_url"] = o
    kd = kind_of(both)
    if kd:
        out["kind"] = kd
    return out


def has_placeholder(text: str) -> bool:
    """模板里的「粘贴…」占位还没换掉。"""
    return bool(re.search(r"粘贴|换成你的", text or ""))
