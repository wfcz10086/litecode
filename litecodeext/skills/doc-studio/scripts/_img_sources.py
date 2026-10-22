"""配图来源: 开放授权图库搜索 + 授权归一 + 下载 + ComfyUI 生图接口。

实测 (2026-10-22, 容器 litecode 出口):
  - Wikimedia Commons API: 可用, 搜索 ~1s, 缩略图 (thumburl, 标准宽度) 下载 ~0.5-1s; 全部为自由授权
  - Openverse API (api.openverse.org): 本机出口被 Cloudflare 403 拦截; 代码保留, 换网络可能可用
  - SearXNG 图片分类: 可用但授权未知, 默认不用 (--allow-unlicensed 才启用)
候选统一成 dict: source/title/url/page_url/width/height/mime/author/license/license_url。
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "LiteCode-DocStudio/1.0 (doc-studio skill; open-license image search)"
TIMEOUT = 20


def http_get(url: str, timeout: int = TIMEOUT) -> bytes:
    """优先用 curl (HTTP/2): 2026-10 实测 Wikimedia API 对本机出口的 HTTP/1.1 请求一律 403 "Too Many Reqs",
    Python 自带 urllib/httpx/requests 都只会 HTTP/1.1; 没有 curl 时退回 urllib。"""
    curl = shutil.which("curl")
    if curl:
        err = ""
        for wait in (0, 2, 5, 10):                 # 429 限流: 退避重试
            time.sleep(wait)
            r = subprocess.run([curl, "-sS", "-L", "--fail", "--compressed", "-m", str(timeout), "-A", UA, url],
                               capture_output=True, timeout=timeout + 5)
            if r.returncode == 0:
                return r.stdout
            err = r.stderr.decode("utf-8", "replace").strip()
            if "429" not in err:
                break
        raise IOError(f"curl rc={r.returncode}: {err[:160]}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(url: str, timeout: int = TIMEOUT) -> dict:
    return json.loads(http_get(url, timeout).decode("utf-8", "replace"))


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return " ".join(_html.unescape(s).split())


# ── 授权 ─────────────────────────────────────────
def norm_license(name: str) -> str:
    """把各家写法归一: pd / cc0 / by / by-sa / by-nc / by-nd / by-nc-sa / by-nc-nd / unknown。"""
    s = (name or "").strip().lower().replace("_", "-")
    if not s:
        return "unknown"
    if "cc0" in s or s in ("cc-zero", "zero"):
        return "cc0"
    if "public domain" in s or s in ("pd", "pdm") or s.startswith("pd-"):
        return "pd"
    m = re.search(r"\bby(?:-(?:nc|nd|sa))*\b", s.replace("cc ", "cc-").replace(" ", "-"))
    if m and ("cc" in s or s.startswith("by")):
        return m.group(0)
    if "gfdl" in s or "fal" == s or "free art" in s:
        return "by-sa"                              # GFDL / 自由艺术许可: 署名+相同方式共享, 按 by-sa 对待
    return "unknown"


def license_ok(code: str, allow_nc: bool = False, allow_nd: bool = False) -> bool:
    if code == "unknown":
        return False
    if "nc" in code.split("-") and not allow_nc:
        return False
    if "nd" in code.split("-") and not allow_nd:
        return False
    return True


def license_label(code: str, raw: str = "") -> str:
    if code in ("pd",):
        return "公有领域"
    if code == "cc0":
        return "CC0"
    if raw and raw.lower().startswith("cc"):
        return raw.upper().replace("CC-", "CC ")
    return ("CC " + code.upper()) if code != "unknown" else "授权未知"


# ── 搜索源 ───────────────────────────────────────
def search_openverse(query: str, n: int = 12) -> list[dict]:
    q = urllib.parse.urlencode({"q": query, "page_size": n, "mature": "false",
                                "license_type": "commercial,modification"})
    data = _get_json(f"https://api.openverse.org/v1/images/?{q}")
    out = []
    for r in data.get("results", []):
        lic = f"{r.get('license', '')}"
        ver = r.get("license_version") or ""
        raw = ("CC0" if lic == "cc0" else "Public Domain Mark" if lic == "pdm"
               else f"CC {lic.upper()} {ver}".strip())
        out.append({
            "source": "openverse", "title": r.get("title") or "", "url": r.get("url"),
            "page_url": r.get("foreign_landing_url") or r.get("url"),
            "width": r.get("width") or 0, "height": r.get("height") or 0,
            "mime": "image/" + (r.get("filetype") or "jpeg").replace("jpg", "jpeg"),
            "author": r.get("creator") or "", "license": norm_license(raw if lic not in ("cc0", "pdm") else lic),
            "license_raw": raw, "license_url": r.get("license_url") or "",
        })
    return out


WM_THUMB_STEPS = (1920, 1280, 960, 500)          # Wikimedia 只给标准宽度出缩略图, 其它宽度 400


def wm_thumb(orig_url: str, orig_w: int, want: int = 1920) -> str:
    """原图 URL → thumb.wikimedia.org 标准宽度缩略图。
    2026-10 实测: upload.wikimedia.org 对本机出口 429 限流 (retry-after 600s), thumb.wikimedia.org 正常。"""
    m = re.match(r"https://upload\.wikimedia\.org/wikipedia/commons/(\w/\w\w)/([^/?]+)", orig_url or "")
    if not m:
        return orig_url
    w = next((s for s in WM_THUMB_STEPS if s <= min(want, orig_w or want)), 0)
    if not w:
        return orig_url
    host = os.environ.get("DOC_STUDIO_WM_THUMB_HOST", "https://thumb.wikimedia.org")
    return f"{host}/wikipedia/commons/thumb/{m.group(1)}/{m.group(2)}/{w}px-{m.group(2)}"


def search_wikimedia(query: str, n: int = 12, thumb_width: int = 1920) -> list[dict]:
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": n,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
    })
    data = _get_json(f"https://commons.wikimedia.org/w/api.php?{q}")
    pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        val = lambda k: strip_html((md.get(k) or {}).get("value", ""))  # noqa: E731
        raw = val("LicenseShortName")
        ow, oh = ii.get("width") or 0, ii.get("height") or 0
        out.append({
            "source": "wikimedia", "title": re.sub(r"^File:|\.\w+$", "", p.get("title", "")),
            "url": wm_thumb(ii.get("url", ""), ow, thumb_width), "page_url": ii.get("descriptionurl") or "",
            "width": ow, "height": oh, "orig_width": ow, "orig_height": oh,
            "mime": ii.get("mime") or "", "author": val("Artist")[:80],
            "license": norm_license(raw), "license_raw": raw, "license_url": val("LicenseUrl"),
            "description": val("ImageDescription")[:200],
        })
    return out


def search_searxng(query: str, base: str, n: int = 12) -> list[dict]:
    q = urllib.parse.urlencode({"q": query, "categories": "images", "format": "json"})
    data = _get_json(f"{base.rstrip('/')}/search?{q}")
    out = []
    for r in (data.get("results") or [])[:n]:
        if not r.get("img_src"):
            continue
        out.append({"source": "searxng", "title": r.get("title") or "", "url": r["img_src"],
                    "page_url": r.get("url") or "", "width": 0, "height": 0, "mime": "",
                    "author": r.get("author") or "", "license": "unknown", "license_raw": "",
                    "license_url": ""})
    return out


SEARCHERS = {"openverse": search_openverse, "wikimedia": search_wikimedia}

# 照片里不想要的: 地图 / 标志 / 图表 / 扫描文献 等
_BAD_WORDS = re.compile(r"\b(map|maps|logo|flag|coat of arms|diagram|chart|plan|seal|stamp|svg|icon|"
                        r"emblem|locator|schematic|scan|page|document|poster|banner|screenshot|"
                        r"retrospective|slide|slides)\b", re.I)


def plausible_photo(c: dict, min_w: int = 800) -> bool:
    mime = (c.get("mime") or "").lower()
    if mime and mime not in ("image/jpeg", "image/jpg", "image/webp", "image/png"):
        return False
    if _BAD_WORDS.search(c.get("title") or ""):
        return False
    w = c.get("orig_width") or c.get("width") or 0
    return not w or w >= min_w


def rank(cands: list[dict], ratio: float | None) -> list[dict]:
    """原序 (相关度) 为主, 比例接近、分辨率高加分。"""
    def score(ic):
        i, c = ic
        s = -i * 1.0
        w, h = c.get("width") or 0, c.get("height") or 0
        if ratio and w and h:
            s -= abs((w / h) - ratio) * 4                 # 比例差 1.0 ≈ 掉 4 名
        if (c.get("orig_width") or w) >= 1600:
            s += 1
        return s
    return [c for _, c in sorted(enumerate(cands), key=score, reverse=True)]


def download(url: str, dest: Path, timeout: int = 40) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(http_get(url, timeout))
    if dest.stat().st_size < 2048:
        raise IOError(f"下载内容过小 ({dest.stat().st_size}B), 多半不是图片")
    return dest


# ── ComfyUI 生图: 只调 comfyui-image 技能的脚本, 不自己实现 ───────────────
def comfy_script() -> Path | None:
    env = os.environ.get("DOC_STUDIO_COMFY_SCRIPT")
    if env:
        return Path(env)
    root = Path(__file__).resolve().parents[3]          # litecodeext/
    p = root / "skills" / "comfyui-image" / "scripts" / "comfy.py"
    return p if p.exists() else None


def comfy_size(ratio: float, area: int = 1024 * 1024) -> tuple[int, int]:
    w = int(round((area * ratio) ** 0.5 / 16) * 16)
    h = int(round((area / ratio) ** 0.5 / 16) * 16)
    return w, h


def run_comfy(addr: str, prompt: str, ratio: float, dest: Path, timeout: int = 900) -> Path:
    """addr='last' 表示用 comfy.py 记住的上次地址。返回下载到 dest 的图片。"""
    script = comfy_script()
    if not script or not script.exists():
        raise RuntimeError("没找到 comfyui-image 技能的 comfy.py (可设 DOC_STUDIO_COMFY_SCRIPT)")
    w, h = comfy_size(ratio or 1.5)
    cmd = [sys.executable, str(script), "run"] + ([] if addr in ("", "last") else [addr]) + \
          ["-w", "qwen21_t2i", "--prompt", prompt, "--width", str(w), "--height", str(h)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    m = re.search(r"^RESULT (\{.*\})\s*$", r.stdout, re.M)
    if r.returncode != 0 or not m:
        raise RuntimeError(f"comfy.py 失败 rc={r.returncode}: {(r.stderr or r.stdout)[-300:]}")
    src = None
    for f in json.loads(m.group(1)).get("files") or []:     # comfy.py: files=[{"path","bytes","url"}]
        cand = f if isinstance(f, str) else (f or {}).get("path")
        if cand and Path(cand).exists():
            src = cand
            break
    if not src:
        raise RuntimeError("comfy.py 没有返回图片文件")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest
