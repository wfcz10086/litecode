#!/usr/bin/env python3
"""edge-tts 中文声音目录 + 多角色选声 + 韵律 (rate/pitch/volume) 默认值。纯离线, tts.py 调用。

script.json:
  "voice": "zh-CN-YunxiNeural"                      全片默认 (旧写法, 仍然有效; 也认 "云希" "yunxi")
  "voices": {"narrator": "云健", "quote": "晓晓", "host": "云扬"}    多角色
  场景: "role": "quote"  → 用 voices.quote;  "voice": "..." 直接指定 (优先级最高)
        "rate": "+20%" / "pitch": "+3Hz" / "volume": "+10%"  逐场韵律 (覆盖自动值)
        "beat": "hook" | "body" | "twist" | "ending"       段落类型 (不写就推断)
  "prosody": "auto" (默认) | "off"   auto 时按段落类型微调: 钩子稍快稍高、转折稍慢、结尾略慢
用法: python3 voices.py          打印声音目录
"""
import difflib
import re

DEFAULT_VOICE = "zh-CN-YunxiNeural"
DEFAULT_RATE = "+14%"

# 全部来自 edge-tts list_voices (2026-10 实测可用的中文声音; 晓辰/晓涵等 Azure 付费声音 edge-tts 不提供)
VOICES = {
    "zh-CN-YunxiNeural":   {"cn": "云希", "gender": "男", "persona": "阳光讲故事男声, 节奏感强", "suits": "人物故事 / 历史 / 通用解说"},
    "zh-CN-YunjianNeural": {"cn": "云健", "gender": "男", "persona": "沉稳有力的激情男声", "suits": "商战 / 体育 / 纪录片 / 悲剧"},
    "zh-CN-YunyangNeural": {"cn": "云扬", "gender": "男", "persona": "专业新闻播音男声", "suits": "财经 / 科技新闻 / 严肃科普"},
    "zh-CN-YunxiaNeural":  {"cn": "云夏", "gender": "男", "persona": "少年音, 可爱", "suits": "儿童科普 / 动画角色 / 搞笑"},
    "zh-CN-XiaoxiaoNeural": {"cn": "晓晓", "gender": "女", "persona": "温暖知性女声", "suits": "情感 / 生活 / 文化 / 引用原话"},
    "zh-CN-XiaoyiNeural":  {"cn": "晓伊", "gender": "女", "persona": "活泼俏皮女声", "suits": "美食 / 冷知识 / 种草 / 搞笑"},
    "zh-CN-liaoning-XiaobeiNeural": {"cn": "晓北", "gender": "女", "persona": "东北话, 幽默 (彩蛋)", "suits": "搞笑吐槽 / 东北题材"},
    "zh-CN-shaanxi-XiaoniNeural":   {"cn": "晓妮", "gender": "女", "persona": "陕西话, 明亮 (彩蛋)", "suits": "西北美食 / 地方文化"},
    "zh-HK-HiuGaaiNeural": {"cn": "晓佳", "gender": "女", "persona": "粤语女声 (彩蛋)", "suits": "港式题材 / 粤语金句"},
    "zh-HK-HiuMaanNeural": {"cn": "晓曼", "gender": "女", "persona": "粤语女声, 柔和 (彩蛋)", "suits": "港风怀旧"},
    "zh-HK-WanLungNeural": {"cn": "云龙", "gender": "男", "persona": "粤语男声 (彩蛋)", "suits": "港片 / 武侠"},
    "zh-TW-HsiaoChenNeural": {"cn": "晓臻", "gender": "女", "persona": "台湾腔女声, 亲切", "suits": "生活 / 旅行"},
    "zh-TW-HsiaoYuNeural": {"cn": "晓雨", "gender": "女", "persona": "台湾腔女声, 轻柔", "suits": "情感 / 治愈"},
    "zh-TW-YunJheNeural":  {"cn": "云哲", "gender": "男", "persona": "台湾腔男声, 温和", "suits": "生活 / 访谈"},
}
# 别名: 中文名 / 拼音 / 小写短名 → 正式名
ALIASES = {}
for _k, _v in VOICES.items():
    _short = re.sub(r"^zh-[A-Z]{2}-(?:[a-z]+-)?|Neural$", "", _k)
    ALIASES.update({_v["cn"]: _k, _short.lower(): _k, _k.lower(): _k})
ALIASES.update({"东北话": "zh-CN-liaoning-XiaobeiNeural", "陕西话": "zh-CN-shaanxi-XiaoniNeural", "粤语": "zh-HK-HiuGaaiNeural",
                "HiuGaai": "zh-HK-HiuGaaiNeural"})

ROLES = ("narrator", "quote", "host")
# 段落类型 → (rate 增量 %, pitch 增量 Hz, volume 增量 %)
PROSODY = {"hook": (6, 2, 0), "body": (0, 0, 0), "twist": (-6, -2, 0), "ending": (-5, -1, 0)}
TWIST_WORDS = ("然而", "但是", "可是", "没想到", "谁知", "直到", "可惜", "结果", "偏偏", "万万没想到", "不料")


def check_voice(name):
    """→ (正式名, 提示或 None)。认正式名/中文名/拼音; 写错给最接近的建议并回退默认声音。"""
    if not name:
        return DEFAULT_VOICE, None
    s = str(name).strip()
    if s in VOICES:
        return s, None
    hit = ALIASES.get(s) or ALIASES.get(s.lower())
    if hit:
        return hit, None
    keys = list(ALIASES)
    near = difflib.get_close_matches(s.lower(), [k.lower() for k in keys], n=1, cutoff=.4)
    sug = ALIASES[keys[[k.lower() for k in keys].index(near[0])]] if near else None
    tip = f"声音 {s!r} 不在白名单" + (f", 最接近的是 {sug} ({VOICES[sug]['cn']})" if sug else "") + f", 已回退默认 {DEFAULT_VOICE}"
    return DEFAULT_VOICE, tip


def _num(v, unit):
    m = re.fullmatch(r"\s*([+-]?\d+(?:\.\d+)?)\s*" + unit + r"\s*", str(v))
    if not m:
        raise ValueError(f"韵律值 {v!r} 格式应为 +10{unit} / -5{unit}")
    return float(m.group(1))


def _fmt(x, unit):
    x = int(round(x))
    return f"{'+' if x >= 0 else '-'}{abs(x)}{unit}"


def beat_of(scene, i, n):
    """段落类型: 显式 beat > 第一场/hook=钩子 > 最后一场/quote=结尾 > 转折词开头=转折 > 正文"""
    b = scene.get("beat")
    if b in PROSODY:
        return b
    if i == 0 or scene.get("type") == "hook":
        return "hook"
    if i == n - 1 or scene.get("type") == "quote":
        return "ending"
    if str(scene.get("text", "")).lstrip().startswith(TWIST_WORDS):
        return "twist"
    return "body"


def plan(sj):
    """script.json → (每场 [{"voice","rate","pitch","volume","role","beat"}...], 提示列表)。不联网。"""
    tips = []
    base_voice, tip = check_voice(sj.get("voice"))
    if tip:
        tips.append("全局 voice: " + tip)
    roles = {}
    for r, v in (sj.get("voices") or {}).items():
        vv, tip = check_voice(v)
        if tip:
            tips.append(f"voices.{r}: {tip}")
        roles[r] = vv
    narrator = roles.get("narrator", base_voice)
    base_rate = _num(sj.get("rate", DEFAULT_RATE), "%")
    auto = str(sj.get("prosody", "auto")).lower() != "off"
    scenes = sj.get("scenes", [])
    out = []
    for i, s in enumerate(scenes):
        role = s.get("role", "narrator")
        if s.get("voice"):
            voice, tip = check_voice(s["voice"])
            if tip:
                tips.append(f"{s.get('id')}.voice: {tip}")
        elif role in roles:
            voice = roles[role]
        else:
            if role != "narrator":
                tips.append(f"{s.get('id')}: role {role!r} 没在 voices 里配置, 用旁白声音")
            voice = narrator
        beat = beat_of(s, i, len(scenes))
        dr, dp, dv = PROSODY[beat] if auto else (0, 0, 0)
        rate = s["rate"] if "rate" in s else _fmt(base_rate + dr, "%")
        pitch = s["pitch"] if "pitch" in s else _fmt(dp, "Hz")
        volume = s["volume"] if "volume" in s else _fmt(dv, "%")
        _num(rate, "%"), _num(pitch, "Hz"), _num(volume, "%")       # 格式校验
        out.append({"voice": voice, "rate": rate, "pitch": pitch, "volume": volume, "role": role, "beat": beat})
    return out, tips


def main():
    for k, v in VOICES.items():
        print(f"{v['cn']:4s} {v['gender']} {k:32s} {v['persona']:18s} 适合: {v['suits']}")


if __name__ == "__main__":
    main()
