"""plugin_assets_router.py — 仓外插件自带前端资源 (ABI v1 向后兼容扩展, 契约见 plugins/_contract.md)。

plugin.py 可选导出:
- WEB_ASSETS: 静态资源目录 (str/Path; 相对 plugin.py 所在目录或绝对路径)
- WEB_ENTRY:  要注入主页的文件名列表 (只允许目录下第一层的 .js / .css)

提供:
- GET /plugin-assets/{plugin}/{path}  只读托管 WEB_ASSETS 目录 (与 /assets/ 一致不要求登录)
- inject_html(html)                   主页 </body> 前按插件名排序注入 <link>/<script defer>;
                                      没有任何 WEB_ENTRY 时原样返回 (字节不变)

未设置 LITECODE_PLUGIN_PATH 时什么都不收集; 单个插件配置错误只记日志跳过。
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()
ORDER = 990  # 在 static_router (999) 之前即可

log = logging.getLogger("routers.plugin_assets")

PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ENTRY_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\.(?:js|css)$")

ASSET_MIME = {
    ".js":    "application/javascript; charset=utf-8",
    ".mjs":   "application/javascript; charset=utf-8",
    ".css":   "text/css; charset=utf-8",
    ".map":   "application/json; charset=utf-8",
    ".json":  "application/json; charset=utf-8",
    ".txt":   "text/plain; charset=utf-8",
    ".png":   "image/png",
    ".jpg":   "image/jpeg",
    ".jpeg":  "image/jpeg",
    ".gif":   "image/gif",
    ".webp":  "image/webp",
    ".svg":   "image/svg+xml",
    ".ico":   "image/x-icon",
    ".woff":  "font/woff",
    ".woff2": "font/woff2",
    ".ttf":   "font/ttf",
}


def web_plugin_name(path: str) -> str:
    """URL 用的插件名: <dir>/plugin.py → <dir>; <root>/plugins/<x>.py → <root>.<x>"""
    p = Path(path)
    return p.parent.name if p.name == "plugin.py" else f"{p.parent.parent.name}.{p.stem}"


def _valid_plugin_name(name: str) -> bool:
    return bool(PLUGIN_NAME_RE.match(name)) and ".." not in name


def _valid_entry(name) -> bool:
    return (isinstance(name, str) and bool(ENTRY_NAME_RE.match(name))
            and ".." not in name and "/" not in name and "\\" not in name)


def _resolve_dir(path: str, raw) -> Path | None:
    if not isinstance(raw, (str, Path)) or isinstance(raw, bool) or not str(raw).strip():
        log.warning("plugin %s: WEB_ASSETS 必须是非空 str/Path (%r), 跳过前端资源", path, raw)
        return None
    d = Path(raw)
    if not d.is_absolute():
        d = Path(path).parent / d
    d = d.resolve()
    if not d.is_dir():
        log.warning("plugin %s: WEB_ASSETS 目录不存在 (%s), 跳过前端资源", path, d)
        return None
    return d


def _entries(path: str, base: Path, raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
        log.warning("plugin %s: WEB_ENTRY 必须是 list[str] (%r), 不注入主页", path, raw)
        return []
    out = []
    for e in raw:
        if not _valid_entry(e):
            log.warning("plugin %s: WEB_ENTRY 非法文件名 %r (只允许目录下第一层 .js/.css), 跳过", path, e)
            continue
        if not (base / e).is_file():
            log.warning("plugin %s: WEB_ENTRY 文件不存在 %s, 跳过", path, base / e)
            continue
        if e not in out:
            out.append(e)
    return out


def collect_web_plugins() -> dict[str, tuple[Path, list[str]]]:
    """{插件名: (资源目录, 注入文件列表)}; 未设置 LITECODE_PLUGIN_PATH → {} (不触发扫描)。"""
    if not os.environ.get("LITECODE_PLUGIN_PATH", "").strip():
        return {}
    try:
        from plugins.registry import registry
        modules = registry.external_modules()
    except Exception:
        log.exception("plugin web assets discovery unavailable")
        return {}
    found: dict[str, tuple[Path, list[str]]] = {}
    for path, mod in modules:
        try:
            raw = getattr(mod, "WEB_ASSETS", None)
            if raw is None:
                if getattr(mod, "WEB_ENTRY", None) is not None:
                    log.warning("plugin %s: 有 WEB_ENTRY 但没有 WEB_ASSETS, 忽略", path)
                continue
            name = web_plugin_name(path)
            if not _valid_plugin_name(name):
                log.warning("plugin %s: 插件名 %r 不在安全字符集 [A-Za-z0-9_.-], 跳过前端资源", path, name)
                continue
            if name in found:
                log.warning("plugin %s: 插件名 %r 重复, 跳过前端资源", path, name)
                continue
            base = _resolve_dir(path, raw)
            if base is None:
                continue
            found[name] = (base, _entries(path, base, getattr(mod, "WEB_ENTRY", None)))
        except Exception:
            log.exception("plugin %s: WEB_ASSETS 处理失败, 跳过", path)
    return found


def inject_html(page: str) -> str:
    """在最后一个 </body> 前注入插件入口; 没有任何入口时原样返回同一个字符串。"""
    tags = []
    for name, (base, entries) in sorted(collect_web_plugins().items()):
        for e in entries:
            try:
                ver = f"?v={int((base / e).stat().st_mtime)}"
            except OSError:
                ver = ""
            url = _html.escape(f"/plugin-assets/{quote(name)}/{quote(e)}{ver}", quote=True)
            attr = _html.escape(name, quote=True)
            if e.endswith(".css"):
                tags.append(f'<link rel="stylesheet" href="{url}" data-plugin="{attr}">')
            else:
                tags.append(f'<script src="{url}" defer data-plugin="{attr}"></script>')
    if not tags:
        return page
    block = "<!-- plugin web assets -->\n" + "\n".join(tags) + "\n"
    i = page.rfind("</body>")
    return page[:i] + block + page[i:] if i >= 0 else page + block


@router.get("/plugin-assets/{plugin}/{path:path}")
async def serve_plugin_asset(plugin: str, path: str):
    """只读托管插件 WEB_ASSETS 目录。未知插件/文件 → 404; 路径穿越/隐藏文件/非法后缀 → 400。"""
    if not _valid_plugin_name(plugin):
        raise HTTPException(404)
    parts = path.replace("\\", "/").split("/")
    if (not path or path.startswith("/") or "\\" in path or "\x00" in path
            or any(p in ("", ".", "..") or p.startswith(".") for p in parts)):
        raise HTTPException(400, "bad path")
    media = ASSET_MIME.get(Path(path).suffix.lower())
    if media is None:
        raise HTTPException(400, "file type not allowed")
    entry = collect_web_plugins().get(plugin)
    if entry is None:
        raise HTTPException(404)
    base = entry[0]
    fp = (base / path).resolve()
    try:
        fp.relative_to(base)          # 防符号链接逃逸
    except ValueError:
        raise HTTPException(404)
    if not fp.is_file():
        raise HTTPException(404)
    return FileResponse(fp, media_type=media, headers={"X-Content-Type-Options": "nosniff"})
