"""doc-studio 公共小件: 浏览器单例、页面类型判定、视觉模型调用、JSON 输出。

各脚本 (images / render / html2pptx / html2docx / qa) 都能单独命令行跑, 这里只放它们共用的东西,
不做编排 —— 编排交给 DAG 或对话里的 agent。
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
LITECODE_ROOT = SKILL_DIR.parent.parent          # litecodeext/ (容器里 /opt/litecode)

SLIDE_W, SLIDE_H = 1280, 720                       # 16:9 幻灯片画布 (CSS px)
A4_W, A4_H = 794, 1123                             # A4 @96dpi (210×297mm)
PAGE_SELECTOR = "section.slide, section.page"
LOCK_PATH = Path(os.environ.get("DOC_STUDIO_LOCK", "/tmp/doc_studio_browser.lock"))


def eprint(*a):
    print(*a, file=sys.stderr, flush=True)


def dump_json(obj, path: str | os.PathLike | None = None) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(s, encoding="utf-8")
    return s


# ── 浏览器: 单例 (文件锁串行) + 用完即关 ─────────────────────────────
@contextlib.contextmanager
def _browser_lock(timeout: float = 600):
    """同一台机器同一时刻只开一个 doc-studio 浏览器 (feedback_browser_singleton)。"""
    try:
        import fcntl
    except ImportError:                         # Windows: 不加锁
        yield
        return
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    t0 = time.time()
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.time() - t0 > timeout:
                fh.close()
                raise RuntimeError(f"等浏览器锁超时 ({LOCK_PATH}), 另一个 doc-studio 渲染还没结束")
            time.sleep(0.5)
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def ensure_base_css(html_path: str | os.PathLike) -> bool:
    """HTML 以相对路径引用 base.css 而同目录没有时, 从技能 assets/ 复制一份。返回是否复制。"""
    import re
    import shutil
    p = Path(html_path)
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    m = re.search(r"""<link[^>]+href=["']((?:\./)?(?:[\w-]+/)?base\.css)["']""", txt)
    if not m:
        return False
    dest = p.parent / m.group(1)
    if dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SKILL_DIR / "assets" / "base.css", dest)
    return True


@contextlib.contextmanager
def open_page(html_path: str | os.PathLike, width: int = SLIDE_W, height: int = SLIDE_H,
              scale: float = 1.0, media: str = "screen"):
    """打开本地 HTML, 等字体与图片加载完, yield (page, browser)。退出时必关浏览器 (含异常路径)。"""
    from playwright.sync_api import sync_playwright
    ensure_base_css(html_path)
    url = Path(html_path).resolve().as_uri()
    with _browser_lock(), sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-dev-shm-usage", "--no-sandbox"])
        try:
            ctx = browser.new_context(viewport={"width": width, "height": height},
                                      device_scale_factor=scale)
            page = ctx.new_page()
            page.emulate_media(media=media)
            page.goto(url, wait_until="load", timeout=60000)
            wait_ready(page)
            yield page, browser
        finally:
            browser.close()


def wait_ready(page, timeout_ms: int = 20000):
    """等 web 字体与所有 <img> 解码完 (含懒加载)。"""
    page.evaluate("""async (t) => {
      const until = Date.now() + t;
      document.querySelectorAll('img[loading=lazy]').forEach(i => i.loading = 'eager');
      try { await document.fonts.ready; } catch (e) {}
      for (const img of Array.from(document.images)) {
        if (img.complete) continue;
        await new Promise(r => { img.onload = img.onerror = r; setTimeout(r, Math.max(0, until - Date.now())); });
      }
    }""", timeout_ms)


def detect_kind(page) -> str:
    """slides: 有 section.slide; doc: 有 section.page 或其它 (流式文档)。"""
    return page.evaluate("""() => document.querySelector('section.slide') ? 'slides'
                             : (document.querySelector('section.page') ? 'doc' : 'flow')""")


def page_size_for(kind: str) -> tuple[int, int]:
    return (SLIDE_W, SLIDE_H) if kind == "slides" else (A4_W, A4_H)


def sniff_kind(html_path: str | os.PathLike) -> str:
    """不开浏览器的粗判 (决定视口宽度用)。"""
    txt = Path(html_path).read_text(encoding="utf-8", errors="replace")
    if 'class="slide' in txt or "class='slide" in txt or "section.slide" in txt:
        return "slides"
    return "doc"


# ── 视觉模型: 复用 vision_ocr 的调用方式 ─────────────────────────────
def vision_ask(image_paths: list[str], prompt: str, max_tokens: int = 1024) -> str:
    """优先走 LiteCode 自己的 core.tools.vision._do_vision_ocr (读当前激活模型 + vision 兜底路由);
    不在 LiteCode 里跑时用环境变量 DOC_STUDIO_VISION_URL / _MODEL / _KEY 直连 OpenAI 兼容接口。
    返回文本; 失败返回以 "ERROR" 开头的字符串 (调用方据此降级, 不抛异常)。"""
    paths = [str(Path(p).resolve()) for p in image_paths]
    if not os.environ.get("DOC_STUDIO_VISION_URL"):
        try:
            if str(LITECODE_ROOT) not in sys.path:
                sys.path.insert(0, str(LITECODE_ROOT))
            import asyncio
            from core.tools.vision import _do_vision_ocr   # type: ignore
            return asyncio.run(_do_vision_ocr(paths[0], prompt, max_tokens=max_tokens, paths=paths))
        except Exception as e:                             # 不在 LiteCode 环境 / 配置缺失
            return f"ERROR: vision 不可用 ({type(e).__name__}: {e}); 可设 DOC_STUDIO_VISION_URL"
    import urllib.request
    parts = []
    for p in paths:
        mime = "image/png" if p.lower().endswith(".png") else "image/jpeg"
        b64 = base64.b64encode(Path(p).read_bytes()).decode()
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    body = {"model": os.environ.get("DOC_STUDIO_VISION_MODEL", "default"), "stream": False,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": parts + [{"type": "text", "text": prompt}]}]}
    req = urllib.request.Request(os.environ["DOC_STUDIO_VISION_URL"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    key = os.environ.get("DOC_STUDIO_VISION_KEY")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            msg = json.loads(r.read())["choices"][0]["message"]
        return (msg.get("content") or msg.get("reasoning_content") or "").strip() or "ERROR: 空回复"
    except Exception as e:
        return f"ERROR: vision 请求失败: {e}"


def parse_json_loose(text: str):
    """从模型回复里抠出第一个 JSON 对象/数组; 失败返回 None。"""
    import re
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    cand = m.group(1) if m else text
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = cand.find(opener), cand.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(cand[i:j + 1])
            except ValueError:
                continue
    return None


def safe_screenshot(page, tries: int = 4, **kw):
    """page.screenshot 偶发 "Unable to capture screenshot" (headless Chromium 首帧还没合成好, 机器忙时更常见), 稍等重试。"""
    for i in range(tries):
        try:
            return page.screenshot(**kw)
        except Exception as e:
            if "Unable to capture screenshot" not in str(e) or i == tries - 1:
                raise
            page.wait_for_timeout(300 * (i + 1))
