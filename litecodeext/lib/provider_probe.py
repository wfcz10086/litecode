"""lib/provider_probe.py — 「能力接入」卡片的连通测试 + 通知发送 (lib/providers.py 的协议适配)。

test(p) 按 protocol 发一个最小请求, 返回 {ok, ms, detail | error, status?}:
  openai_images / openai_tts / openai_asr  GET {base}/models (不扣费; 平台没有 /models 时只验证可达)
  openai_embeddings  POST /embeddings 嵌一个词      openai_rerank  POST /rerank 两条文档
  comfyui            GET /system_stats             searxng  GET /search?q=LiteCode&format=json
  *_webhook / smtp   真发一条 "LiteCode 测试消息"    s3  ListBuckets (SigV4 签名, 只用标准库)
  builtin            检查本机脚本在不在             http  用法里第一条 curl 解析成 httpx 请求跑一次 (不走 shell)
"""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import hashlib
import hmac
import json
import time
import urllib.parse
from pathlib import Path

import httpx

from . import providers as P

TEST_TEXT = "LiteCode 测试消息"
_UA = {"User-Agent": "LiteCode-Providers/1.0"}


def _timeout(p: dict, default: float = 20.0) -> float:
    try:
        return float((p.get("extra") or {}).get("timeout") or default)
    except (TypeError, ValueError):
        return default


def _base(p: dict) -> str:
    b = (p.get("base_url") or "").strip().rstrip("/")
    if b and "://" not in b:
        b = "http://" + b
    return b


def _auth_headers(p: dict) -> dict:
    h = dict(_UA)
    if p.get("api_key"):
        h["Authorization"] = f"Bearer {p['api_key']}"
    return h


def _short(text: str, n: int = 300) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text[:n] + ("…" if len(text) > n else "")


def _err_of(r: httpx.Response) -> str:
    return f"HTTP {r.status_code}: {_short(r.text, 240)}"


def _client(p: dict, **kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_timeout(p), follow_redirects=True, **kw)


# ── 各协议 ────────────────────────────────────────────────────

async def _openai_models(p: dict) -> dict:
    base = _base(p)
    if not base:
        return {"ok": False, "error": "没填地址 base_url"}
    async with _client(p) as c:
        r = await c.get(base + "/models", headers=_auth_headers(p))
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code, "error": "鉴权失败 (key 不对或没权限): " + _short(r.text, 160)}
    if r.status_code == 404:
        return {"ok": True, "status": 404, "detail": "服务可达 (该平台没有 /models 列表接口, 未实际调用, key 未验证)"}
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": _err_of(r)}
    try:
        ids = [m.get("id") for m in (r.json().get("data") or []) if isinstance(m, dict)]
    except Exception:
        ids = []
    want = p.get("model")
    note = f"可用模型 {len(ids)} 个" if ids else "接口可达"
    if want and ids:
        note += ", 含 " + want if want in ids else f", 列表里没看到 {want} (有的平台不全列, 以实际调用为准)"
    return {"ok": True, "status": r.status_code, "detail": note}


async def _embeddings(p: dict) -> dict:
    async with _client(p) as c:
        r = await c.post(_base(p) + "/embeddings", headers=_auth_headers(p),
                         json={"model": p.get("model") or "", "input": ["LiteCode"]})
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": _err_of(r)}
    try:
        vec = r.json()["data"][0]["embedding"]
        return {"ok": True, "status": r.status_code, "detail": f"向量维度 {len(vec)}"}
    except Exception:
        return {"ok": False, "status": r.status_code, "error": "返回里没有 data[0].embedding: " + _short(r.text)}


async def _rerank(p: dict) -> dict:
    body = {"model": p.get("model") or "", "query": "LiteCode 是什么",
            "documents": ["LiteCode 是一个 AI 编程助手", "今天天气晴"]}
    async with _client(p) as c:
        r = await c.post(_base(p) + "/rerank", headers=_auth_headers(p), json=body)
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": _err_of(r)}
    try:
        res = r.json().get("results") or []
        top = max(res, key=lambda x: x.get("relevance_score", 0)) if res else None
        return {"ok": bool(res), "status": r.status_code,
                "detail": f"返回 {len(res)} 条, 最相关的是第 {top.get('index', 0) + 1} 条" if top else "无结果"}
    except Exception:
        return {"ok": False, "status": r.status_code, "error": "返回格式不认识: " + _short(r.text)}


async def _comfyui(p: dict) -> dict:
    base = _base(p)
    async with _client(p) as c:
        r = await c.get(base + "/system_stats", headers=_UA)
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": _err_of(r) + " (不是 ComfyUI 地址?)"}
    try:
        d = r.json()
        ver = (d.get("system") or {}).get("comfyui_version") or "?"
        gpus = ", ".join(str(x.get("name", "")).split(":")[-1].strip() for x in d.get("devices") or [])
        return {"ok": True, "status": r.status_code, "detail": f"ComfyUI {ver}" + (f" · {gpus}" if gpus else "")}
    except Exception:
        return {"ok": False, "status": r.status_code, "error": "返回不是 ComfyUI 的 system_stats: " + _short(r.text)}


async def _searxng(p: dict) -> dict:
    base = _base(p)
    async with _client(p) as c:
        r = await c.get(base + "/search", params={"q": "LiteCode", "format": "json"}, headers=_UA)
    if r.status_code == 403:
        return {"ok": False, "status": 403, "error": "403: SearXNG 没开 JSON 输出 (settings.yml search.formats 加 json)"}
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": _err_of(r)}
    try:
        n = len(r.json().get("results") or [])
    except Exception:
        return {"ok": False, "status": r.status_code, "error": "返回不是 JSON: " + _short(r.text, 120)}
    return {"ok": True, "status": r.status_code, "detail": f"搜索 LiteCode 返回 {n} 条结果"}


def _builtin(p: dict) -> dict:
    script = (p.get("extra") or {}).get("script") or ""
    if not script:
        return {"ok": True, "detail": "内置能力"}
    path = Path(script)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / script
    if path.exists():
        return {"ok": True, "detail": f"本机脚本就绪: {path.name}"}
    return {"ok": False, "error": f"本机没找到脚本 {path}"}


async def run_curl(p: dict, cmd: str, **vars) -> httpx.Response:
    """渲染占位后把 curl 解析成 httpx 请求执行 (不经 shell)。"""
    req = P.parse_curl(P.render(cmd, p, **vars))
    kw = {k: req[k] for k in ("content", "files") if req.get(k) is not None}
    async with httpx.AsyncClient(timeout=req.get("timeout") or _timeout(p, 30), follow_redirects=True) as c:
        return await c.request(req["method"], req["url"], headers=req["headers"], auth=req.get("auth"), **kw)


async def _http(p: dict) -> dict:
    cmd = P.first_curl(p.get("usage") or "")
    if not cmd:
        base = _base(p)
        if not base:
            return {"ok": False, "error": "用法里没有 curl 示例, 也没填地址, 没法测试"}
        async with _client(p) as c:
            r = await c.get(base, headers=_UA)
        ok = r.status_code < 500
        return {"ok": ok, "status": r.status_code,
                "detail" if ok else "error": f"用法里没有 curl, 只 GET 了地址: HTTP {r.status_code}"}
    try:
        r = await run_curl(p, cmd, prompt="一只橘猫, 测试图", query="LiteCode", text=TEST_TEXT, width=256,
                           height=256, input=TEST_TEXT)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": _err_of(r)}
    ctype = r.headers.get("content-type", "")
    preview = _short(r.text, 160) if ("json" in ctype or "text" in ctype) else f"{ctype} {len(r.content)} 字节"
    return {"ok": True, "status": r.status_code, "detail": f"跑了用法里第一条 curl: HTTP {r.status_code} · {preview}"}


# ── 通知 ──────────────────────────────────────────────────────

def _sign_dingtalk(secret: str, ts_ms: int) -> str:
    s = f"{ts_ms}\n{secret}".encode()
    return urllib.parse.quote_plus(base64.b64encode(hmac.new(secret.encode(), s, hashlib.sha256).digest()).decode())


def _sign_feishu(secret: str, ts: int) -> str:
    key = f"{ts}\n{secret}".encode()
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()


def _webhook_url(p: dict) -> str:
    """webhook 地址: base_url 填完整地址; 只填了 key/token 时 (api_key) 按平台拼。"""
    url = (p.get("base_url") or "").strip()
    key = (p.get("api_key") or "").strip()
    proto = p["protocol"]
    if url:
        return url
    if proto == "wecom_webhook" and key:
        return "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + key
    if proto == "feishu_webhook" and key:
        return "https://open.feishu.cn/open-apis/bot/v2/hook/" + key
    if proto == "dingtalk_webhook" and key:
        return "https://oapi.dingtalk.com/robot/send?access_token=" + key
    return ""


async def send_notify(p: dict, text: str, title: str = "") -> dict:
    """往一张 notify 卡片发一条文本; 返回 {ok, ms, detail|error}。"""
    t0 = time.time()
    try:
        r = await _send(p, text, title)
    except Exception as e:
        r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    r["ms"] = int((time.time() - t0) * 1000)
    return r


async def _send(p: dict, text: str, title: str) -> dict:
    proto = p["protocol"]
    if proto == "smtp":
        return await asyncio.to_thread(_smtp_send, p, text, title or TEST_TEXT)
    if proto == "http":
        cmd = P.first_curl(p.get("usage") or "")
        if not cmd:
            return {"ok": False, "error": "这张卡是通用 HTTP, 用法里没有 curl 示例"}
        r = await run_curl(p, cmd, text=json.dumps(text, ensure_ascii=False)[1:-1], title=title)
        ok = r.status_code < 400
        return {"ok": ok, "status": r.status_code, ("detail" if ok else "error"): _err_of(r)}
    url = _webhook_url(p)
    if not url:
        return {"ok": False, "error": "没填 webhook 地址"}
    secret = str((p.get("extra") or {}).get("secret") or "")
    msg = (title + "\n" + text) if title else text
    if proto == "wecom_webhook":
        body = {"msgtype": "text", "text": {"content": msg}}
    elif proto == "feishu_webhook":
        body = {"msg_type": "text", "content": {"text": msg}}
        if secret:
            ts = int(time.time())
            body.update(timestamp=str(ts), sign=_sign_feishu(secret, ts))
    elif proto == "dingtalk_webhook":
        body = {"msgtype": "text", "text": {"content": msg}}
        if secret:
            ts = int(time.time() * 1000)
            url += ("&" if "?" in url else "?") + f"timestamp={ts}&sign={_sign_dingtalk(secret, ts)}"
    else:
        return {"ok": False, "error": f"协议 {proto} 不能发通知"}
    async with _client(p) as c:
        r = await c.post(url, json=body, headers=_UA)
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": _err_of(r)}
    try:
        d = r.json()
    except Exception:
        d = {}
    code = d.get("errcode", d.get("code", d.get("StatusCode", 0)))
    if code not in (0, "0", None):
        return {"ok": False, "status": r.status_code, "error": f"平台返回错误 {code}: {d.get('errmsg') or d.get('msg')}"}
    return {"ok": True, "status": r.status_code, "detail": "已发送"}


def _smtp_send(p: dict, text: str, subject: str) -> dict:
    import smtplib
    import ssl
    from email.message import EmailMessage
    ex = p.get("extra") or {}
    host = (p.get("base_url") or "").replace("smtp://", "").replace("smtps://", "").strip().rstrip("/")
    if not host:
        return {"ok": False, "error": "没填 SMTP 服务器 (base_url, 如 smtp.qq.com:465)"}
    h, _, port = host.partition(":")
    port = int(port or ex.get("port") or 465)
    user = str(ex.get("username") or ex.get("from") or "")
    sender = str(ex.get("from") or user)
    to = ex.get("to") or sender
    to = [x.strip() for x in to.split(",")] if isinstance(to, str) else list(to)
    if not sender or not to:
        return {"ok": False, "error": "extra 里要填 username / from / to"}
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = subject, sender, ", ".join(to)
    m.set_content(text)
    tmo = _timeout(p)
    ctx = ssl.create_default_context()
    if port == 465 or str(ex.get("ssl", "")).lower() in ("1", "true", "yes"):
        s = smtplib.SMTP_SSL(h, port, timeout=tmo, context=ctx)
    else:
        s = smtplib.SMTP(h, port, timeout=tmo)
        s.ehlo()
        if s.has_extn("starttls"):
            s.starttls(context=ctx)
            s.ehlo()
    try:
        if user and p.get("api_key"):
            s.login(user, p["api_key"])
        s.send_message(m)
    finally:
        try:
            s.quit()
        except Exception:
            pass
    return {"ok": True, "detail": f"邮件已发给 {', '.join(to)}"}


# ── S3 (SigV4, 只做 ListBuckets) ──────────────────────────────

def s3_list_request(endpoint: str, access_key: str, secret_key: str, region: str = "us-east-1",
                    now: _dt.datetime | None = None) -> tuple[str, dict]:
    """GET / (ListBuckets) 的 SigV4 签名; 返回 (url, headers)。纯函数, 便于单测。"""
    u = urllib.parse.urlparse(endpoint if "://" in endpoint else "https://" + endpoint)
    host = u.netloc
    now = now or _dt.datetime.now(_dt.timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()
    canonical = "\n".join(["GET", "/", "", f"host:{host}", f"x-amz-content-sha256:{payload_hash}",
                           f"x-amz-date:{amz_date}", "", "host;x-amz-content-sha256;x-amz-date", payload_hash])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])

    def _h(k, m):
        return hmac.new(k, m.encode(), hashlib.sha256).digest()
    k = _h(_h(_h(_h(("AWS4" + secret_key).encode(), day), region), "s3"), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {"x-amz-date": amz_date, "x-amz-content-sha256": payload_hash,
               "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                                f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={sig}"}
    return f"{u.scheme}://{host}/", headers


async def _s3(p: dict) -> dict:
    ex = p.get("extra") or {}
    ak, sk = str(ex.get("access_key_id") or ""), str(p.get("api_key") or "")
    if not (_base(p) and ak and sk):
        return {"ok": False, "error": "要填 endpoint (base_url)、extra.access_key_id 和 Secret (api_key)"}
    url, headers = s3_list_request(p["base_url"], ak, sk, str(ex.get("region") or "us-east-1"))
    async with _client(p) as c:
        r = await c.get(url, headers=headers)
    if r.status_code >= 400:
        return {"ok": False, "status": r.status_code, "error": _err_of(r)}
    import re
    names = re.findall(r"<Name>([^<]+)</Name>", r.text)
    return {"ok": True, "status": r.status_code,
            "detail": f"列出 {len(names)} 个桶" + (": " + ", ".join(names[:8]) if names else "")}


# ── 入口 ──────────────────────────────────────────────────────

async def test(p: dict) -> dict:
    t0 = time.time()
    proto = p.get("protocol")
    try:
        if proto in ("openai_images", "openai_tts", "openai_asr"):
            r = await _openai_models(p)
        elif proto == "openai_embeddings":
            r = await _embeddings(p)
        elif proto == "openai_rerank":
            r = await _rerank(p)
        elif proto == "comfyui":
            r = await _comfyui(p)
        elif proto == "searxng":
            r = await _searxng(p)
        elif proto in ("wecom_webhook", "feishu_webhook", "dingtalk_webhook", "smtp"):
            r = await _send(p, TEST_TEXT, "")
        elif proto == "s3":
            r = await _s3(p)
        elif proto == "builtin":
            r = _builtin(p)
        else:
            r = await (_send(p, TEST_TEXT, "") if p.get("kind") == "notify" else _http(p))
    except httpx.TimeoutException:
        r = {"ok": False, "error": f"超时 ({_timeout(p):.0f} 秒)"}
    except httpx.HTTPError as e:
        r = {"ok": False, "error": f"连不上: {type(e).__name__}: {e}"}
    except Exception as e:
        r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    r["ms"] = int((time.time() - t0) * 1000)
    return r
