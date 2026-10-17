#!/usr/bin/env python3
"""step4a: numpy 合成无版权氛围 BGM → bgm.wav (时长 = timeline total + 1s)
用法: python3 bgm.py [mood]   (默认取 script.json/timeline 的 mood, 没写就用配色预设推荐的 mood)
  dark   小调 drone + 心跳鼓 (人物陨落 / 悬疑 / 悲剧)
  epic   小调 + 快鼓 + 每场开头刺点 (逆袭 / 战争 / 商战)
  light  大调 pad + 轻拍 (科普 / 冷知识 / 搞笑)
  tech   电子脉冲: 小调 pad + 十六分音符琶音 + 踩镲 (科技 / AI / 数码)
  warm   温暖: 大七和弦 + 拨弦琶音 + 轻鼓 (生活 / 美食 / 情感)
  calm   舒缓: 长音 pad + 稀疏铃音, 无鼓 (自然 / 治愈 / 历史回望)
  upbeat 轻快: 大调 + 快拍 + 拍手 + 跳音琶音 (励志 / 运动 / 种草)
同一标题每次生成一样; 不同标题在调性 (±几个半音)、速度 (±8%)、琶音型、LFO 上各不相同 (见 plan())。"""
import hashlib, json, os, subprocess, sys, wave

import numpy as np

SR = 44100
MOODS = {
    "dark":   {"chord": [55, 110, 130.81, 164.81, 220], "beat": 1.15, "kick": .5, "sting": .12, "lp": .12},
    "epic":   {"chord": [73.42, 146.83, 174.61, 220, 293.66], "beat": .62, "kick": .6, "sting": .18, "lp": .12},
    "light":  {"chord": [130.81, 164.81, 196, 261.63, 329.63], "beat": .8, "kick": .22, "sting": 0, "lp": .12},
    "tech":   {"chord": [55, 110, 164.81, 220, 246.94], "beat": .5, "kick": .5, "sting": 0, "lp": .35, "pad": .55,
               "arp": 4, "arp_amp": .16, "arp_decay": 14, "hat": .05},
    "warm":   {"chord": [87.31, 174.61, 220, 261.63, 329.63], "beat": .9, "kick": .16, "sting": 0, "lp": .2, "pad": .8,
               "arp": 2, "arp_amp": .2, "arp_decay": 5},
    "calm":   {"chord": [65.41, 130.81, 196, 246.94, 293.66], "beat": 1.6, "kick": 0, "sting": 0, "lp": .15, "pad": .9,
               "bell": .14},
    "upbeat": {"chord": [98, 196, 246.94, 293.66, 392], "beat": .46, "kick": .45, "sting": 0, "lp": .3, "pad": .6,
               "arp": 2, "arp_amp": .17, "arp_decay": 9, "clap": .08},
}
# 各情绪允许的移调范围 (半音): 暗色系别移太高, 亮色系别移太低
TRANSPOSE = {"dark": (-2, 2), "epic": (-2, 3), "light": (-3, 2), "tech": (-3, 3), "warm": (-2, 3), "calm": (-3, 2), "upbeat": (-2, 3)}
ARP_PATTERNS = [[0, 1, 2, 3], [0, 2, 1, 3], [3, 2, 1, 0], [0, 1, 2, 3, 2, 1], [0, 2, 3, 1], [1, 3, 0, 2, 3, 1]]


def plan(title, mood):
    """标题 + 情绪 → 合成参数 (确定性: 同输入同输出)。只算参数, 不合成音频。"""
    assert mood in MOODS, f"mood 只能是 {list(MOODS)}"
    m = MOODS[mood]
    h = hashlib.sha256(f"{title}|{mood}".encode("utf-8")).digest()
    u = [b / 255 for b in h]                     # 32 个 0-1 的确定性随机数
    lo, hi = TRANSPOSE[mood]
    semi = lo + h[0] % (hi - lo + 1)
    k = 2 ** (semi / 12)
    tempo = round(.92 + .16 * u[1], 3)           # 0.92 ~ 1.08
    return {"mood": mood, "transpose": semi, "tempo": tempo,
            "chord": [round(f * k, 3) for f in m["chord"]],
            "beat": round(m["beat"] / tempo, 4),
            "lfo": round(.05 + .05 * u[2], 4),
            "arp_pattern": ARP_PATTERNS[h[3] % len(ARP_PATTERNS)],
            "swing": round(.12 * u[4], 3) if mood in ("warm", "upbeat") else 0.0,
            "seed": int.from_bytes(h[5:9], "little")}


def _lowpass(x, a):
    """一阶低通 y[n] = y[n-1] + a*(x[n]-y[n-1]) (改版前逐点循环的写法)。
    冲激响应 a*(1-a)^n 截断到 1e-7 后用 np.convolve, 结果与逐点循环一致, 快两个数量级"""
    n = int(np.ceil(np.log(1e-7) / np.log(1 - a)))
    return np.convolve(x, a * (1 - a) ** np.arange(n))[:len(x)]


def _note(mix, t, start, length, f, amp, decay, harm=(1, .35, .12)):
    s, e = int(start * SR), min(int((start + length) * SR), len(mix))
    if s >= e:
        return
    seg = t[s:e] - start
    env = np.exp(-seg * decay) * np.minimum(1, seg * 200)
    mix[s:e] += amp * env * sum(a * np.sin(2 * np.pi * f * (n + 1) * seg) for n, a in enumerate(harm))


def synth(tl, mood, title=None):
    m, p = MOODS[mood], plan(title if title is not None else tl.get("title", ""), mood)
    rng = np.random.default_rng(p["seed"])
    dur = tl["total"] + 1.0
    t = np.arange(int(SR * dur)) / SR
    mix = np.zeros_like(t)
    chord, beat = p["chord"], p["beat"]
    for i, f in enumerate(chord):                            # 和弦 pad, 慢 LFO 起伏 + 轻微失谐
        lfo = .75 + .25 * np.sin(2 * np.pi * (p["lfo"] + f * 1e-4) * t + i)
        a = m.get("pad", 1) * .5 / (i + 1) ** .6
        mix += a * lfo * (np.sin(2 * np.pi * f * t) + .45 * np.sin(2 * np.pi * f * 1.004 * t) + .3 * np.sin(2 * np.pi * f * .9965 * t))
    if m["kick"]:
        for k in np.arange(0, dur, beat):                    # 鼓点 (咚-哒)
            for off, amp in [(0, m["kick"]), (.23, m["kick"] * .6)]:
                s, e = int((k + off) * SR), int((k + off + .28) * SR)
                if e > len(mix):
                    break
                seg = t[s:e] - (k + off)
                f = 62 * np.exp(-seg * 9) + 38
                mix[s:e] += amp * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-seg * 13)
    if m.get("arp"):                                         # 琶音: 和弦高八度按 pattern 轮转
        step, pat, notes = beat / m["arp"], p["arp_pattern"], [f * 2 for f in chord[1:]]
        for j, k in enumerate(np.arange(0, dur, step)):
            sw = p["swing"] * step if j % 2 else 0
            _note(mix, t, k + sw, step * 2, notes[pat[j % len(pat)] % len(notes)], m["arp_amp"], m["arp_decay"])
    if m.get("bell"):                                        # 稀疏铃音 (calm)
        for k in np.arange(beat, dur, beat * 2):
            if rng.random() < .7:
                _note(mix, t, k, 3.0, chord[2 + rng.integers(0, len(chord) - 2)] * 4, m["bell"], 1.6, (1, .5, .25, .1))
    for key, every, off, ln in (("hat", .5, .5, .04), ("clap", 1, .5, .09)):   # 噪声打击: 踩镲 / 拍手
        if m.get(key):
            for k in np.arange(off * beat, dur, every * beat):
                s, e = int(k * SR), min(int((k + ln) * SR), len(mix))
                n = rng.standard_normal(e - s)
                n = np.diff(n, prepend=0)                    # 一阶差分 ≈ 高通, 去掉闷声
                mix[s:e] += m[key] * n * np.exp(-np.arange(e - s) / SR * (60 if key == "hat" else 30))
    if m["sting"]:                                           # 每场开头的紧张刺点
        for sc in tl["scenes"][1:]:
            s = int(sc["start"] * SR); e = min(int((sc["start"] + 1.4) * SR), len(mix))
            x = np.linspace(0, 1, e - s)
            f1, f2 = 440 * chord[0] / m["chord"][0], 466 * chord[0] / m["chord"][0]
            mix[s:e] += m["sting"] * x ** 2 * np.exp(-x * 1.2) * (np.sin(2 * np.pi * f1 * t[s:e]) + np.sin(2 * np.pi * f2 * t[s:e])) * .5
    lp = _lowpass(mix, m["lp"])                              # 去刺耳高频
    fade = np.minimum(1, np.minimum(t / 1.5, (dur - t) / 1.5))   # 首尾 1.5s 淡入淡出
    lp *= np.clip(fade, 0, 1)
    return lp * (.18 / np.max(np.abs(lp))), p                # 峰值约 -15dB, 给旁白让路 (merge.py 再闪避)


def resolve_mood(tl):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import theme as TH
    script = json.load(open("script.json", encoding="utf-8")) if os.path.exists("script.json") else None
    return TH.apply_tl(tl, script)[1]["mood"]


def main():
    tl = json.load(open("timeline.json", encoding="utf-8"))
    mood = sys.argv[1] if len(sys.argv) > 1 else resolve_mood(tl)
    if mood not in MOODS:
        print(f"⚠ mood {mood!r} 不认识 (可选 {list(MOODS)}), 用 dark")
        mood = "dark"
    out, p = synth(tl, mood)
    with wave.open("bgm.wav", "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((out * 32767).astype(np.int16).tobytes())
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", "bgm.wav"],
                             capture_output=True, text=True).stdout.strip())
    assert abs(d - (tl["total"] + 1)) < .5, f"BGM 时长 {d} 与时间轴 {tl['total'] + 1} 不符"
    print(f"bgm.wav {d:.1f}s mood={mood} 移调 {p['transpose']:+d} 速度 x{p['tempo']} 琶音 {p['arp_pattern']}")


if __name__ == "__main__":
    main()
