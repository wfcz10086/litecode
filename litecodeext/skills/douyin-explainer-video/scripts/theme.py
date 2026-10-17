#!/usr/bin/env python3
"""配色预设 + 背景样式 + 对比度校验 (render.py / bgm.py 共用, 不依赖第三方库)。

script.json 三种写法:
  "theme": "deepsea"                         用预设 (名称见 templates/themes.json, 也认中文名 "深海科技蓝")
  "theme": {"preset": "mint", "accent": "#0a7"}  在预设上改几个颜色
  "theme": {"bg": "#0a1226", "accent": "#f5c451", "alert": "#e63946"}   旧写法: 以 midnight 为底覆盖
  不写 theme                                 按 title/topic/mood 关键词自动挑; 挑不出时按标题哈希, 同脚本稳定、不同脚本多样
"bg_style": 覆盖预设推荐的背景样式 (BG_STYLES)。

用法: python3 theme.py [script.json]   打印解析结果与对比度, 不达标退出 1
      python3 theme.py --list          列出全部预设及对比度
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
THEMES_FILE = os.path.join(HERE, "..", "templates", "themes.json")

BG_STYLES = {
    "radial":    "径向渐变 + 浮尘光点 (经典)",
    "blueprint": "蓝图网格线, 缓慢平移",
    "stripes":   "斜条纹, 缓慢流动",
    "bokeh":     "柔光粒子 (大光斑虚化)",
    "paper":     "纸张噪点纹理 + 暗角",
    "mesh":      "渐变网格 (多团色块缓慢漂移)",
}
COLOR_KEYS = ("bg", "deep", "paper", "accent", "alert", "ink", "wash")
LEGACY_BASE = "midnight"          # 旧颜色对象写法以它为底: bg/deep/paper/wash 与改版前模板默认值一致
FALLBACK_MOOD = "dark"

# 关键词 → 候选预设 (同一类里再按标题哈希挑一个, 让同题材的不同视频也不总撞色)
KEYWORDS = [
    (("科技", "AI", "人工智能", "芯片", "互联网", "编程", "程序员", "算法", "机器人", "大模型", "数据", "软件", "手机", "电脑", "半导体"),
     ["deepsea", "cyber", "cloud"]),
    (("未来", "游戏", "元宇宙", "赛博", "黑客", "科幻", "外星", "太空", "宇宙", "火箭"),
     ["cyber", "deepsea", "glacier"]),
    (("历史", "古代", "朝代", "王朝", "皇帝", "传记", "民国", "三国", "唐朝", "宋朝", "明朝", "清朝", "古人", "考古", "文物"),
     ["newspaper", "burgundy", "midnight"]),
    (("财经", "股票", "股市", "商业", "金融", "投资", "富豪", "首富", "交易", "公司", "品牌", "奢侈", "经济", "创业"),
     ["burgundy", "midnight", "mono", "deepsea"]),
    (("悲剧", "悬疑", "犯罪", "陨落", "死亡", "恐怖", "谜案", "灾难", "诡异", "真相", "案件"),
     ["midnight", "mono", "cyber", "burgundy"]),
    (("美食", "做饭", "菜", "吃", "旅行", "旅游", "运动", "健身", "逆袭", "励志"),
     ["sunset", "mint", "sakura"]),
    (("情感", "爱情", "恋爱", "浪漫", "婚姻", "治愈", "美妆", "穿搭", "节日", "宠物", "猫", "狗"),
     ["sakura", "sunset", "mint"]),
    (("健康", "生活", "家居", "收纳", "教育", "学习", "育儿", "小技巧", "搞笑"),
     ["mint", "cloud", "sakura"]),
    (("自然", "动物", "植物", "森林", "环保", "地理", "鸟", "昆虫"),
     ["forest", "mint", "glacier"]),
    (("冷知识", "科普", "物理", "化学", "生物", "医学", "数学", "海洋", "极地", "冰川", "气候", "地球"),
     ["glacier", "cloud", "deepsea", "forest"]),
    (("设计", "艺术", "哲学", "极简", "摄影", "建筑"),
     ["mono", "newspaper", "cloud"]),
]
MOOD_THEMES = {
    "dark": ["midnight", "mono", "burgundy", "cyber"],
    "epic": ["burgundy", "sunset", "cyber"],
    "light": ["mint", "cloud", "sakura", "sunset"],
    "tech": ["deepsea", "cyber", "cloud"],
    "warm": ["sunset", "sakura", "newspaper"],
    "calm": ["forest", "glacier", "newspaper"],
    "upbeat": ["sunset", "mint", "sakura"],
}


def load_presets():
    with open(THEMES_FILE, encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


PRESETS = load_presets()


# ---------- 颜色与对比度 (WCAG 2.x) ----------
def parse_color(c):
    """#rgb / #rrggbb / rgb(r,g,b) → (r,g,b) 0-255; 其他格式抛 ValueError。"""
    s = str(c).strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*[\d.]+\s*)?\)", s)
    if m:
        return tuple(min(255, int(x)) for x in m.groups())
    raise ValueError(f"颜色格式不支持: {c!r} (用 #rrggbb 或 rgb(r,g,b))")


def luminance(c):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
    r, g, b = parse_color(c)
    return .2126 * ch(r) + .7152 * ch(g) + .0722 * ch(b)


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + .05) / (lo + .05)


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(x)) for x in rgb)


def _mix(a, b, k):
    """a 与 b 按 k (b 的占比) 混合"""
    ra, rb = parse_color(a), parse_color(b)
    return _hex([x + (y - x) * k for x, y in zip(ra, rb)])


def _rgba(c, alpha):
    r, g, b = parse_color(c)
    return f"rgba({r},{g},{b},{alpha})"


def _on(color, prefer, min_ratio):
    """color 上面写字用黑还是白: prefer 够 min_ratio 就用它, 否则取对比度高的那个"""
    if contrast(prefer, color) >= min_ratio:
        return prefer
    other = "#ffffff" if prefer != "#ffffff" else "#111111"
    return max((prefer, other), key=lambda x: contrast(x, color))


def is_light(bg):
    return luminance(bg) > .4


# 每条: (前景键, 背景键, 最低对比度, 说明)
RULES = [
    ("paper", "bg", 4.5, "正文 paper 对 背景 bg"),
    ("paper", "deep", 4.5, "正文 paper 对 背景暗部 deep"),
    ("ink", "bg", 4.5, "标题/字幕 ink 对 背景 bg"),
    ("ink", "deep", 4.5, "标题/字幕 ink 对 背景暗部 deep"),
    ("accent", "bg", 3.0, "强调色 accent 对 背景 bg"),
    ("alert", "bg", 3.0, "警示色 alert 对 背景 bg"),
    ("on_accent", "accent", 4.5, "结尾卡文字 对 accent 底"),
    ("on_alert", "alert", 3.0, "VS 徽章文字 对 alert 底"),
]


def derive(base):
    """bg/deep/paper/accent/alert(/ink/wash) → 模板要用的全部 CSS 变量 (键名 _ 会变成 -)"""
    t = {k: base[k] for k in ("bg", "deep", "paper", "accent", "alert")}
    for k in t:
        parse_color(t[k])
    light = is_light(t["bg"])
    t["ink"] = base.get("ink") or (t["paper"] if light else "#ffffff")
    t["wash"] = base.get("wash") or ("#ffffff" if light else t["accent"])
    t["on_accent"] = _on(t["accent"], "#000000" if not light else "#ffffff", 4.5)
    t["on_alert"] = _on(t["alert"], "#ffffff", 3.0)
    if light:
        t.update(panel=_rgba(_mix(t["bg"], "#ffffff", .6), .88), shadow="rgba(255,255,255,.95)", glow="rgba(255,255,255,.95)",
                 vig=_rgba(t["deep"], .9), scan=t["deep"], track="rgba(0,0,0,.08)",
                 tape_bg=t["alert"], tape_fg=t["on_alert"], stamp_bg="rgba(255,255,255,.6)", line=_rgba(t["paper"], .12),
                 hard=_rgba(t["accent"], .22), drop="rgba(0,0,0,.18)")
    else:
        t.update(panel=_rgba(_mix(t["bg"], "#ffffff", .02), .85), shadow="#000000", glow="rgba(0,0,0,.9)",
                 vig="rgba(0,0,0,.75)", scan="#000000", track="rgba(255,255,255,.08)",
                 tape_bg="#000000", tape_fg=t["alert"], stamp_bg="rgba(0,0,0,.25)", line=_rgba(t["accent"], .13),
                 hard="#000000", drop="rgba(0,0,0,.6)")
    t["light"] = "1" if light else "0"
    return t


def validate(t):
    """对比度不达标 → 中文错误列表 (空 = 通过)"""
    errs = []
    for fg, bg, need, desc in RULES:
        try:
            r = contrast(t[fg], t[bg])
        except (KeyError, ValueError) as e:
            errs.append(f"{desc}: 颜色无效 ({e})")
            continue
        if r < need - 1e-9:
            errs.append(f"{desc} = {r:.2f}:1 < {need}:1 ({t[fg]} on {t[bg]}) — 调整颜色或换预设")
    return errs


# ---------- 预设挑选 ----------
def _hash(s):
    return int(hashlib.md5(str(s).encode("utf-8")).hexdigest(), 16)


def find_preset(name):
    if name in PRESETS:
        return name
    for k, v in PRESETS.items():
        if name and (name == v["name"] or name in v["name"]):
            return k
    return None


def auto_pick(sj):
    """返回 (预设名, 原因)。同一 title 结果稳定。"""
    title = str(sj.get("title", ""))
    text = " ".join(str(x) for x in (title, sj.get("topic", ""), " ".join(sj.get("tags", []) or [])))
    best, hits = None, 0
    for words, cands in KEYWORDS:
        n = sum(text.count(w) for w in words)
        if n > hits:
            best, hits = (words, cands), n
    if best:
        cands = best[1]
        k = cands[_hash(title) % len(cands)]
        return k, f"关键词 {[w for w in best[0] if w in text][:3]} → {cands}"
    mood = sj.get("mood")
    if mood in MOOD_THEMES:
        cands = MOOD_THEMES[mood]
        return cands[_hash(title) % len(cands)], f"mood={mood} → {cands}"
    names = sorted(PRESETS)
    return names[_hash(title) % len(names)], "无关键词, 按标题哈希"


def resolve(sj):
    """script.json(或 timeline) → {"name", "theme"(全部 CSS 变量), "bg_style", "mood", "source", "errors", "warnings"}"""
    raw = sj.get("theme")
    errors, warnings = [], []
    over = {}
    if isinstance(raw, str) and raw.strip():
        name = find_preset(raw.strip())
        if not name:
            errors.append(f"theme 预设 {raw!r} 不存在, 可选: {', '.join(PRESETS)}")
            name, why = auto_pick(sj)
            source = "auto"
        else:
            source = "preset"
    elif isinstance(raw, dict) and raw:
        base = raw.get("preset")
        name = find_preset(base) if base else LEGACY_BASE
        if base and not name:
            errors.append(f"theme.preset {base!r} 不存在, 可选: {', '.join(PRESETS)}")
            name = LEGACY_BASE
        over = {k: raw[k] for k in COLOR_KEYS if raw.get(k)}
        source = "custom"
    else:
        name, why = auto_pick(sj)
        source = "auto"
    p = PRESETS[name]
    base = {**{k: p[k] for k in COLOR_KEYS if k in p}, **over}
    # 自定义改了 bg 但没给 ink/wash: 不沿用预设里配浅/深底的那个, 让 derive 重新推
    for k in ("ink", "wash"):
        if "bg" in over and k not in over and is_light(over["bg"]) != p.get("light", False):
            base.pop(k, None)
    try:
        theme = derive(base)
        errors += validate(theme)
    except ValueError as e:
        errors.append(str(e))
        theme = derive({k: p[k] for k in COLOR_KEYS if k in p})
    style = sj.get("bg_style") or (raw.get("bg_style") if isinstance(raw, dict) else None) or p["bg_style"]
    if style not in BG_STYLES:
        errors.append(f"bg_style {style!r} 不存在, 可选: {', '.join(BG_STYLES)}")
        style = p["bg_style"]
    mood = sj.get("mood") or p.get("mood") or FALLBACK_MOOD
    return {"name": name, "theme": theme, "bg_style": style, "mood": mood, "source": source,
            "errors": errors, "warnings": warnings}


def apply_tl(tl, script=None):
    """把 script.json 里的视觉字段 (theme/bg_style/mood/highlight) 叠到 timeline 上并解析 → (新 tl, 解析结果)。
    改配色不用重新配音: render.py 每次生成 tl.js 都重新读 script.json。"""
    src = dict(tl)
    if script:
        for k in ("theme", "bg_style", "mood", "topic", "tags", "highlight", "title"):
            if k in script:
                src[k] = script[k]
            elif k in ("theme", "bg_style"):
                src.pop(k, None)
    r = resolve(src)
    out = {**src, "theme": r["theme"], "theme_name": r["name"], "bg_style": r["bg_style"], "mood": r["mood"]}
    return out, r


def describe(r):
    p = PRESETS[r["name"]]
    return f"配色 {r['name']} ({p['name']}, {r['source']}) · 背景 {r['bg_style']} · BGM {r['mood']}"


def main():
    if "--list" in sys.argv:
        bad = 0
        for k, p in PRESETS.items():
            t = derive(p)
            errs = validate(t)
            bad += bool(errs)
            print(f"{k:10s} {p['name']:10s} 浅色={p['light']!s:5s} 正文 {contrast(t['paper'], t['bg']):5.2f} "
                  f"字幕 {contrast(t['ink'], t['bg']):5.2f} 强调 {contrast(t['accent'], t['bg']):5.2f} "
                  f"警示 {contrast(t['alert'], t['bg']):5.2f} {'✗ ' + '; '.join(errs) if errs else '✓'}")
        sys.exit(1 if bad else 0)
    src = sys.argv[1] if len(sys.argv) > 1 else "script.json"
    r = resolve(json.load(open(src, encoding="utf-8")))
    print(describe(r))
    for e in r["errors"]:
        print("  ✗", e)
    sys.exit(1 if r["errors"] else 0)


if __name__ == "__main__":
    main()
