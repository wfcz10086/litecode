"""core/tools/web.py — HTTP fetch helper (was tool_dispatch._do_web_fetch)."""
from __future__ import annotations

import re

import httpx


async def _do_web_fetch(url: str, max_chars: int = 6000) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
            headers=headers
        ) as client:
            resp = await client.get(url)
            # [v1.2] 多编码兜底: 中国网站 (weather.com.cn / 163.com 等) 常常
            # 声明 charset 不准确. 优先按声明解, 失败再尝试 utf-8 / gbk / gb2312.
            raw_bytes = resp.content
            html = ""
            if raw_bytes:
                # 1) 从 HTML meta 标签抓编码 (更可靠)
                meta_enc = None
                try:
                    head = raw_bytes[:2048].decode("ascii", errors="ignore")
                    m = re.search(r'charset=["\']?([A-Za-z0-9_\-]+)', head, re.I)
                    if m:
                        meta_enc = m.group(1).lower()
                except Exception:
                    pass
                # 2) 按优先级尝试解码
                for enc in filter(None, [meta_enc,
                                         resp.encoding,
                                         "utf-8", "gbk", "gb18030", "gb2312"]):
                    try:
                        html = raw_bytes.decode(enc, errors="replace")
                        if html.strip():
                            break
                    except (LookupError, UnicodeDecodeError):
                        continue
            # [v1.2] 空响应显式报错, 避免上层把 "" 当结果塞给模型
            if not html or not html.strip():
                return (f"ERROR: {url} returned empty body "
                        f"(status={resp.status_code}, bytes={len(raw_bytes)}). "
                        f"Site may block non-CN IP or require JS. "
                        f"Try browser_read(url) instead.")
            if resp.status_code >= 400:
                return f"ERROR: {url} HTTP {resp.status_code}"
    except Exception as e:
        return f"ERROR fetching {url}: {e}"
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>",   " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if not text:
        return (f"ERROR: {url} fetched ok but content was all markup/whitespace. "
                f"Try browser_read(url) for JS-rendered pages.")
    return text[:max_chars]
