#!/usr/bin/env python3
"""step4a: numpy 合成无版权氛围 BGM → bgm.wav (时长 = timeline total + 1s)
用法: python3 bgm.py [dark|epic|light]   (默认取 timeline.json 的 mood, 再默认 dark)
  dark  小调 drone + 心跳鼓 (人物陨落 / 悬疑 / 悲剧)
  epic  小调 + 快鼓 + 每场开头刺点 (逆袭 / 战争 / 商战)
  light 大调 pad + 轻拍 (科普 / 冷知识 / 搞笑)"""
import json, subprocess, sys, wave

import numpy as np

SR = 44100
MOODS = {
    "dark":  {"chord": [55, 110, 130.81, 164.81, 220], "beat": 1.15, "kick": .5, "sting": .12},
    "epic":  {"chord": [73.42, 146.83, 174.61, 220, 293.66], "beat": .62, "kick": .6, "sting": .18},
    "light": {"chord": [130.81, 164.81, 196, 261.63, 329.63], "beat": .8, "kick": .22, "sting": 0},
}


def synth(tl, mood):
    m = MOODS[mood]
    dur = tl["total"] + 1.0
    t = np.arange(int(SR * dur)) / SR
    mix = np.zeros_like(t)
    for i, f in enumerate(m["chord"]):                       # 和弦 pad, 慢 LFO 起伏 + 轻微失谐
        lfo = .75 + .25 * np.sin(2 * np.pi * (.07 + f * 1e-4) * t)
        a = .5 / (i + 1) ** .6
        mix += a * lfo * (np.sin(2 * np.pi * f * t) + .45 * np.sin(2 * np.pi * f * 1.004 * t) + .3 * np.sin(2 * np.pi * f * .9965 * t))
    for k in np.arange(0, dur, m["beat"]):                   # 鼓点 (咚-哒)
        for off, amp in [(0, m["kick"]), (.23, m["kick"] * .6)]:
            s, e = int((k + off) * SR), int((k + off + .28) * SR)
            if e > len(mix):
                break
            seg = t[s:e] - (k + off)
            f = 62 * np.exp(-seg * 9) + 38
            mix[s:e] += amp * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-seg * 13)
    if m["sting"]:                                           # 每场开头的紧张刺点
        for sc in tl["scenes"][1:]:
            s = int(sc["start"] * SR); e = min(int((sc["start"] + 1.4) * SR), len(mix))
            x = np.linspace(0, 1, e - s)
            mix[s:e] += m["sting"] * x ** 2 * np.exp(-x * 1.2) * (np.sin(2 * np.pi * 440 * t[s:e]) + np.sin(2 * np.pi * 466 * t[s:e])) * .5
    lp = np.zeros_like(mix); acc = 0.0                      # 一阶低通: 去刺耳高频
    for i in range(len(mix)):
        acc += .12 * (mix[i] - acc); lp[i] = acc
    return lp * (.18 / np.max(np.abs(lp)))                   # 约 -15dB, 给旁白让路


def main():
    tl = json.load(open("timeline.json", encoding="utf-8"))
    mood = sys.argv[1] if len(sys.argv) > 1 else tl.get("mood", "dark")
    assert mood in MOODS, f"mood 只能是 {list(MOODS)}"
    out = synth(tl, mood)
    with wave.open("bgm.wav", "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((out * 32767).astype(np.int16).tobytes())
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", "bgm.wav"],
                             capture_output=True, text=True).stdout.strip())
    assert abs(d - (tl["total"] + 1)) < .5, f"BGM 时长 {d} 与时间轴 {tl['total'] + 1} 不符"
    print(f"bgm.wav {d:.1f}s mood={mood}")


if __name__ == "__main__":
    main()
