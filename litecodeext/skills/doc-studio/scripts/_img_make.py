"""本地出图: 裁剪适配、matplotlib 图表、内联 SVG 图标、降级色块。不联网。"""
from __future__ import annotations

import json
import re
from pathlib import Path

# ── 裁剪 ─────────────────────────────────────────
def fit_crop(src: Path, dest: Path, ratio: float | None, max_w: int = 1920,
             focus: tuple[float, float] = (0.5, 0.45)) -> dict:
    """按目标宽高比居中裁剪 (竖向焦点略偏上, 照片主体多在中上), 限制最大宽度, 存 JPEG。
    返回 {"width","height","src_width","src_height"}。"""
    from PIL import Image, ImageOps
    im = Image.open(src)
    im = ImageOps.exif_transpose(im).convert("RGB")
    sw, sh = im.size
    if ratio:
        cur = sw / sh
        if cur > ratio * 1.01:                    # 太宽 → 裁左右
            nw = int(sh * ratio)
            x0 = int(min(max(0, sw * focus[0] - nw / 2), sw - nw))
            im = im.crop((x0, 0, x0 + nw, sh))
        elif cur < ratio * 0.99:                  # 太高 → 裁上下
            nh = int(sw / ratio)
            y0 = int(min(max(0, sh * focus[1] - nh / 2), sh - nh))
            im = im.crop((0, y0, sw, y0 + nh))
    if im.width > max_w:
        im = im.resize((max_w, int(im.height * max_w / im.width)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "JPEG", quality=86, optimize=True, progressive=True)
    return {"width": im.width, "height": im.height, "src_width": sw, "src_height": sh}


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


# ── 图表 ─────────────────────────────────────────
def _cjk_font() -> str | None:
    from matplotlib import font_manager as fm
    names = {f.name for f in fm.fontManager.ttflist}
    for want in ("Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei",
                 "SimHei", "WenQuanYi Micro Hei", "Noto Sans CJK JP"):
        if want in names:
            return want
    cjk = sorted(n for n in names if "CJK" in n)
    return cjk[0] if cjk else None


DEFAULT_PALETTE = ["#4F7EF7", "#1F2A44", "#16A34A", "#F59E0B", "#DB2777", "#0891B2", "#7C3AED"]


def make_chart(spec: dict, dest: Path, w_px: int = 960, h_px: int = 540,
               palette: list[str] | None = None, text_color: str = "#1A1A1A", scale: float = 2.0) -> Path:
    """spec: {"type": bar|barh|line|area|pie|donut, "labels": [...], "series": [{"name","data"}],
              "unit": "万人次", "title": "", "y_label": "", "highlight": 3}
    尺寸按版面像素给, 输出 scale 倍清晰度 PNG (透明背景, 贴在任何底色上都行)。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    pal = palette or DEFAULT_PALETTE
    font = _cjk_font()
    rc = {"font.family": [font, "DejaVu Sans"] if font else ["DejaVu Sans"], "axes.unicode_minus": False,
          "text.color": text_color, "axes.labelcolor": text_color, "xtick.color": text_color,
          "ytick.color": text_color, "axes.edgecolor": "#CFCFCF"}
    typ = (spec.get("type") or "bar").lower()
    labels = [str(x) for x in spec.get("labels") or []]
    series = spec.get("series") or ([{"name": "", "data": spec["data"]}] if "data" in spec else [])
    if not labels or not series:
        raise ValueError("图表 spec 需要 labels 和 series/data")
    unit = spec.get("unit") or ""
    px = 1 / 100                                   # figsize 用英寸, dpi=100*scale → 1 版面像素 = scale 输出像素
    fs = max(10.0, min(w_px, h_px) / 26) * 0.72    # 版面上约 1/26 短边的字号 (pt)
    with plt.rc_context(rc):
        fig, ax = plt.subplots(figsize=(w_px * px, h_px * px), dpi=100 * scale)
        fig.patch.set_alpha(0)
        ax.set_facecolor("none")
        if typ in ("pie", "donut"):
            data = series[0]["data"]
            wedges, _t, autot = ax.pie(
                data, labels=labels, colors=pal[:len(data)], startangle=90, counterclock=False,
                autopct=lambda p: f"{p:.0f}%", pctdistance=0.78 if typ == "donut" else 0.6,
                wedgeprops={"width": 0.42, "edgecolor": "white"} if typ == "donut" else {"edgecolor": "white"},
                textprops={"fontsize": fs})
            for t in autot:
                t.set_color("white")
                t.set_fontsize(fs * 0.9)
            ax.axis("equal")
        else:
            import numpy as np
            x = np.arange(len(labels))
            n = len(series)
            hl = spec.get("highlight")
            for i, s in enumerate(series):
                d = s["data"]
                col = pal[i % len(pal)]
                if typ in ("bar", "barh"):
                    bw = 0.7 / n
                    off = (i - (n - 1) / 2) * bw
                    cols = [col] * len(d)
                    if n == 1 and hl is not None:   # 单系列: 高亮一根, 其余淡色
                        cols = [pal[0] if j == hl else _tint(pal[0], 0.65) for j in range(len(d))]
                    bars = (ax.barh(x + off, d, bw * 0.92, color=cols, label=s.get("name"))
                            if typ == "barh" else ax.bar(x + off, d, bw * 0.92, color=cols, label=s.get("name")))
                    ax.bar_label(bars, labels=[_fmt(v) for v in d], padding=3, fontsize=fs * 0.85,
                                 color=text_color)
                else:
                    ax.plot(x, d, color=col, linewidth=2.6, marker="o", markersize=5, label=s.get("name"))
                    if typ == "area":
                        ax.fill_between(x, d, color=col, alpha=0.12)
                    ax.annotate(_fmt(d[-1]), (x[-1], d[-1]), textcoords="offset points", xytext=(0, 8),
                                ha="center", fontsize=fs * 0.85, color=col, fontweight="bold")
            if typ == "barh":
                ax.set_yticks(x, labels, fontsize=fs)
                ax.invert_yaxis()
                ax.grid(axis="x", color="#E6E6E6", linewidth=0.8)
            else:
                ax.set_xticks(x, labels, fontsize=fs)
                ax.grid(axis="y", color="#E6E6E6", linewidth=0.8)
            ax.set_axisbelow(True)
            for sp in ("top", "right", "left" if typ != "barh" else "bottom"):
                ax.spines[sp].set_visible(False)
            ax.tick_params(length=0, labelsize=fs * 0.9)
            if spec.get("y_label") or unit:
                (ax.set_xlabel if typ == "barh" else ax.set_ylabel)(spec.get("y_label") or unit,
                                                                     fontsize=fs * 0.9)
            if n > 1:
                ax.legend(frameon=False, fontsize=fs * 0.9, ncol=min(n, 4), loc="upper left")
        if spec.get("title"):
            ax.set_title(spec["title"], fontsize=fs * 1.15, fontweight="bold", loc="left", pad=12)
        fig.tight_layout(pad=0.6)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(dest, transparent=True)
        plt.close(fig)
    return dest


def _fmt(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:,.0f}" if abs(f) >= 100 or f == int(f) else f"{f:,.1f}"


def parse_chart_spec(raw: str | None, base: Path | None = None) -> dict | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw.startswith("{") and base is not None and (base / raw).exists():
        raw = (base / raw).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except ValueError:
        return None


# ── 图标 (24×24 线性, currentColor; 自绘, 无第三方授权问题) ───────────────
ICONS = {
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "star": '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8z"/>',
    "map-pin": '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>',
    "leaf": '<path d="M5 19c0-8 5-13 15-14-1 10-6 15-14 15"/><path d="M5 19l7-7"/>',
    "water": '<path d="M12 3.5s-6 6.6-6 11a6 6 0 0 0 12 0c0-4.4-6-11-6-11z"/>',
    "mountain": '<path d="M3 19l6.5-11 4 6.5 2.5-3.5L21 19z"/>',
    "camera": '<rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8.5 7l1.5-2.5h4L15.5 7"/><circle cx="12" cy="13.5" r="3.5"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "users": '<circle cx="9" cy="8.5" r="3.2"/><path d="M3 19.5c.6-3.3 3-5 6-5s5.4 1.7 6 5"/><circle cx="17" cy="9.5" r="2.5"/><path d="M16.5 14.6c2.3.2 3.9 1.7 4.5 4.4"/>',
    "chart": '<path d="M4 20V4M4 20h16"/><path d="M8 16v-4M12 16V8M16 16v-6"/>',
    "trend": '<path d="M3.5 17l6-6 4 4 7-7.5"/><path d="M15 7.5h5.5V13"/>',
    "car": '<path d="M4 16.5V12l2-5h12l2 5v4.5"/><path d="M3 12h18"/><circle cx="7.5" cy="16.5" r="1.8"/><circle cx="16.5" cy="16.5" r="1.8"/>',
    "battery": '<rect x="2.5" y="7" width="17" height="10" rx="2"/><path d="M21.5 10.5v3"/><path d="M10.5 9l-2 3.2h3.5L10 15"/>',
    "bolt": '<path d="M13 2.5L5 13.5h6l-1 8 8-11h-6z"/>',
    "globe": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.4 3.8 5.2 3.8 8.5s-1.2 6.1-3.8 8.5c-2.6-2.4-3.8-5.2-3.8-8.5S9.4 5.9 12 3.5z"/>',
    "shield": '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6z"/><path d="M8.8 12l2.2 2.2 4.2-4.4"/>',
    "cpu": '<rect x="6.5" y="6.5" width="11" height="11" rx="1.5"/><rect x="9.5" y="9.5" width="5" height="5"/><path d="M9.5 3v3.5M14.5 3v3.5M9.5 17.5V21M14.5 17.5V21M3 9.5h3.5M3 14.5h3.5M17.5 9.5H21M17.5 14.5H21"/>',
    "code": '<path d="M8.5 7.5L4 12l4.5 4.5M15.5 7.5L20 12l-4.5 4.5M13.5 5l-3 14"/>',
    "chat": '<path d="M4.5 5.5h15a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H10l-4.5 3.5v-3.5h-1a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z"/>',
    "rocket": '<path d="M12 15.5c-1.6-1.1-2.9-2.4-3.9-3.9C9.9 7.2 13.3 4.2 19.5 4.5c.3 6.2-2.7 9.6-7.5 11z"/><path d="M8.1 11.6L5 11l2.5-3.5 3.5-.5M12.4 15.9l.6 3.1 3.5-2.5.5-3.5"/><circle cx="14.8" cy="9.2" r="1.4"/><path d="M6.5 17.5l-2 2"/>',
    "book": '<path d="M4.5 5.5c2.5-1 5-1 7.5.5v13c-2.5-1.5-5-1.5-7.5-.5zM19.5 5.5c-2.5-1-5-1-7.5.5v13c2.5-1.5 5-1.5 7.5-.5z"/>',
    "heart": '<path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z"/>',
    "building": '<path d="M5 21V4.5h9V21M14 9.5h5V21M3 21h18"/><path d="M8 8h3M8 11.5h3M8 15h3"/>',
    "plane": '<path d="M10.5 13.5L3.5 11l1.5-1.5 7 1 4.5-5c.8-.8 2.2-.8 2.5 0 .3.4.2 1.4-.5 2.2l-5 4.5 1 7L13 21l-2.5-7z"/>',
    "money": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><circle cx="12" cy="12" r="2.8"/><path d="M6 9.5v5M18 9.5v5"/>',
    "cloud": '<path d="M7 18.5h10.5a4 4 0 0 0 .4-8A6 6 0 0 0 6.5 9.6 4.5 4.5 0 0 0 7 18.5z"/>',
    "tea": '<path d="M4.5 9h12v5a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5z"/><path d="M16.5 10.5h1.5a2.5 2.5 0 0 1 0 5h-1.8M8 3.5c-.8 1 .8 2 0 3M11.5 3.5c-.8 1 .8 2 0 3"/>',
    "boat": '<path d="M3 16.5h18l-2.5 3.5h-13zM12 4v12M12 5l6 9h-6"/>',
}
ICON_ALIASES = {"location": "map-pin", "pin": "map-pin", "time": "clock", "people": "users", "team": "users",
                "growth": "trend", "data": "chart", "ev": "car", "energy": "bolt", "power": "bolt",
                "security": "shield", "safe": "shield", "ai": "cpu", "chip": "cpu", "message": "chat",
                "launch": "rocket", "nature": "leaf", "lake": "water", "travel": "plane", "price": "money",
                "culture": "book", "love": "heart", "city": "building", "weather": "sun"}


def icon_svg(name: str, cls: str = "ds-icon", stroke: float = 1.8) -> str:
    key = (name or "").strip().lower()
    key = ICON_ALIASES.get(key, key)
    body = ICONS.get(key) or ICONS["star"]
    return (f'<svg class="{cls}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true" data-icon="{key}">{body}</svg>')


def guess_icon(text: str) -> str:
    """按描述猜图标 (中英关键词)。"""
    t = (text or "").lower()
    table = [("map-pin", "地点 位置 地址 景点 location"), ("sun", "天气 阳光 夏 sun"),
             ("leaf", "环保 绿色 自然 植物 春 leaf eco"), ("water", "湖 水 雨 lake water"),
             ("mountain", "山 登山 mountain"), ("camera", "拍照 摄影 photo camera"),
             ("clock", "时间 小时 效率 time"), ("calendar", "日期 日程 节日 计划 calendar"),
             ("users", "用户 团队 人 游客 users team"), ("trend", "增长 上升 趋势 growth"),
             ("chart", "数据 统计 图表 data"), ("car", "汽车 车 驾驶 car"), ("battery", "电池 充电 续航 battery"),
             ("bolt", "能源 电 快 energy"), ("globe", "全球 国际 出海 global"), ("shield", "安全 隐私 合规 security"),
             ("cpu", "芯片 智能 算力 cpu"), ("code", "代码 开发 编程 code"), ("chat", "对话 聊天 沟通 chat"),
             ("rocket", "发布 启动 创新 rocket"), ("book", "文化 历史 学习 文档 book"), ("heart", "喜爱 健康 服务 love"),
             ("building", "城市 企业 建筑 city"), ("plane", "旅行 交通 出行 travel"), ("money", "价格 成本 收入 门票 money"),
             ("cloud", "云 部署 cloud"), ("tea", "茶 龙井 tea"), ("boat", "船 游船 boat")]
    for icon, words in table:
        if any(w in t for w in words.split()):
            return icon
    return "star"


# ── 降级: 柔和色块 (没有可用照片/生图时) ─────────────────────────
def placeholder(dest: Path, w: int, h: int, color: str = "#4F7EF7") -> Path:
    from PIL import Image, ImageDraw
    w, h = max(64, int(w)), max(64, int(h))
    c = _hex(color)
    im = Image.new("RGB", (w, h))
    dr = ImageDraw.Draw(im)
    for y in range(h):                               # 竖向浅渐变: 主色 18% → 8% 混白
        k = 0.18 - 0.10 * y / h
        dr.line([(0, y), (w, y)], fill=tuple(int(255 - (255 - v) * k) for v in c))
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "PNG")
    return dest


def _tint(color: str, k: float) -> str:
    """和白色混合 k 比例 (高亮柱以外的淡色)。"""
    r, g, b = _hex(color)
    return "#%02X%02X%02X" % tuple(int(v + (255 - v) * k) for v in (r, g, b))


def _hex(s: str) -> tuple[int, int, int]:
    m = re.match(r"#?([0-9a-fA-F]{6})", (s or "").strip())
    v = m.group(1) if m else "4F7EF7"
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def css_tokens(html_text: str) -> dict:
    """抠 :root 里的 --ds-* 变量 (图表配色/降级色块用)。"""
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"--ds-([\w-]+)\s*:\s*([^;}]+)", html_text)}
