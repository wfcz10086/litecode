#!/usr/bin/env python3
"""程序化生成无版权配乐 (纯 numpy 合成, 不依赖模型/音色库, 不联网)。

用法:
  python3 bgm_gen.py --mood upbeat --duration 60 -o bgm.mp3
  python3 bgm_gen.py --mood calm --bpm 70 --key D --duration 30 --loop -o loop.wav   # 无缝循环段
  python3 bgm_gen.py --list

参数:
  --mood      calm 舒缓 / upbeat 轻快 / epic 史诗 / sad 伤感 / tech 科技 / warm 温暖 / funny 搞笑 / suspense 悬疑
  --bpm       速度 (默认取情绪预设)       --key  调性主音 C C# D ... B (默认取预设)
  --duration  秒数. 非 --loop 时输出正好这么长, 带淡入淡出; --loop 时按小节取整, 首尾无缝衔接 (无淡入淡出)
  --fade-in / --fade-out   秒 (默认 1.0 / 2.5)
  --seed      同参数同 seed 输出完全一致; 换 seed 换琶音型/鼓花/配器细节
输出 wav (48k 立体声) 或 mp3 (按扩展名, ffmpeg 编码 + loudnorm 到 -16 LUFS 左右)。
"""
import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}
NOTE.update({"DB": 1, "EB": 3, "GB": 6, "AB": 8, "BB": 10})
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]

# prog: 级数 (1 起), 每个和弦 1 小节; drums: kick/snare/hat 每拍 16 分音符网格
MOODS = {
    "calm":     dict(name="舒缓", scale=MAJOR, key="D", bpm=72, prog=[1, 5, 6, 4], sev=True, pad=.55, arp=.18,
                     arp_div=2, pluck_decay=3.0, bass=.25, kick=0, snare=0, hat=0, bell=.10),
    "upbeat":   dict(name="轻快", scale=MAJOR, key="G", bpm=116, prog=[1, 5, 6, 4], sev=False, pad=.30, arp=.20,
                     arp_div=4, pluck_decay=9.0, bass=.40, kick=.70, snare=.30, hat=.10, bell=0, clap=True),
    "epic":     dict(name="史诗", scale=MINOR, key="D", bpm=88, prog=[1, 6, 3, 7], sev=False, pad=.60, arp=.16,
                     arp_div=4, pluck_decay=7.0, bass=.45, kick=.80, snare=.40, hat=.05, bell=0),
    "sad":      dict(name="伤感", scale=MINOR, key="A", bpm=66, prog=[1, 6, 4, 5], sev=True, pad=.45, arp=.24,
                     arp_div=2, pluck_decay=2.2, bass=.25, kick=0, snare=0, hat=0, bell=.06),
    "tech":     dict(name="科技", scale=MINOR, key="E", bpm=122, prog=[1, 1, 6, 7], sev=False, pad=.35, arp=.18,
                     arp_div=4, pluck_decay=14.0, bass=.45, kick=.75, snare=.25, hat=.08, bell=0),
    "warm":     dict(name="温暖", scale=MAJOR, key="F", bpm=88, prog=[1, 6, 2, 5], sev=True, pad=.45, arp=.22,
                     arp_div=2, pluck_decay=4.5, bass=.32, kick=.35, snare=.15, hat=.05, bell=0),
    "funny":    dict(name="搞笑", scale=MAJOR, key="C", bpm=128, prog=[1, 4, 5, 1], sev=False, pad=.15, arp=.26,
                     arp_div=4, pluck_decay=16.0, bass=.50, kick=.50, snare=.30, hat=.06, bell=0, bounce=True),
    "suspense": dict(name="悬疑", scale=MINOR, key="C", bpm=80, prog=[1, 2, 1, 6], sev=False, pad=.50, arp=0,
                     arp_div=2, pluck_decay=3.0, bass=.35, kick=.55, snare=0, hat=0, bell=.08, heartbeat=True),
}


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def chord_notes(root_pc, scale, degree, seventh):
    """级数 → 和弦内音 (MIDI, 以 C4 附近为中心)。"""
    idx = degree - 1
    tones = [scale[(idx + k) % 7] + 12 * ((idx + k) // 7) for k in ((0, 2, 4, 6) if seventh else (0, 2, 4))]
    return [57 + ((root_pc - 9) % 12) + t for t in tones]   # 主音落在 A3~G#4


def env_adsr(n, a=0.01, r=0.2, sus=1.0):
    e = np.ones(n) * sus
    na, nr = max(1, int(a * SR)), max(1, int(r * SR))
    e[:na] = np.linspace(0, sus, na)
    if nr < n:
        e[-nr:] *= np.linspace(1, 0, nr)
    return e


def tone(freq, n, harmonics=(1, .5, .33, .25), detune=0.0, rng=None):
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k, amp in enumerate(harmonics, 1):
        ph = rng.random() * 2 * np.pi if rng is not None else 0
        out += amp * np.sin(2 * np.pi * freq * k * (1 + detune) * t + ph)
    return out / sum(harmonics)


def pluck(freq, n, decay, rng):
    t = np.arange(n) / SR
    body = tone(freq, n, (1, .6, .3, .15, .08), rng=rng)
    return body * np.exp(-decay * t) * np.minimum(1, t * 400)


def kick(n):
    t = np.arange(n) / SR
    f = 45 + 80 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def noise_hit(n, decay, rng, hp=True):
    x = rng.standard_normal(n)
    if hp:
        x = np.diff(x, prepend=0)
    return x * np.exp(-np.arange(n) / SR * decay) * 0.5


def render(mood, bpm, key, bars, seed):
    m = MOODS[mood]
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bar_n = int(round(4 * beat * SR))
    total = bar_n * (bars + 1)                  # 多一小节放尾音 (循环时折回开头)
    L, R = np.zeros(total), np.zeros(total)
    root = NOTE[key.upper()]
    six = bar_n // 16
    arp_pat = [[0, 1, 2, 1], [0, 2, 1, 2], [0, 1, 2, 3], [2, 1, 0, 1]][seed % 4]

    def add(sig, start, pan=0.0, gain=1.0):
        end = min(total, start + len(sig))
        s = sig[:end - start] * gain
        L[start:end] += s * (1 - max(pan, 0))
        R[start:end] += s * (1 + min(pan, 0))

    for b in range(bars):
        deg = m["prog"][b % len(m["prog"])]
        notes = chord_notes(root, m["scale"], deg, m["sev"])
        s0 = b * bar_n
        if m["pad"]:
            n = bar_n + int(0.6 * SR)
            pad = sum(tone(midi_hz(x), n, (1, .45, .25, .12), detune=d, rng=rng)
                      for x in notes for d in (-0.003, 0.003)) / (len(notes) * 2)
            pad *= env_adsr(n, a=0.25, r=0.6)
            add(pad, s0, -0.2, m["pad"])
            add(pad, s0, 0.2, m["pad"] * 0.8)
        if m["bass"]:
            bn = midi_hz(notes[0] - 24)
            steps = [0, 6, 8, 14] if m.get("bounce") else ([0, 8] if bpm < 100 else [0, 4, 8, 12])
            for st in steps:
                n = six * (2 if m.get("bounce") else 4)
                add(tone(bn, n, (1, .3, .1)) * env_adsr(n, 0.005, 0.05), s0 + st * six, 0, m["bass"])
        if m["arp"]:
            for i, st in enumerate(range(0, 16, max(1, 16 // (m["arp_div"] * 4)))):
                x = notes[arp_pat[i % 4] % len(notes)] + 12
                n = int(min(1.5, 4 / m["pluck_decay"]) * SR)
                add(pluck(midi_hz(x), n, m["pluck_decay"], rng), s0 + st * six, 0.35 if i % 2 else -0.35, m["arp"])
        if m["bell"] and b % 2 == 0:
            x = notes[(seed + b) % len(notes)] + 24
            n = int(2.5 * SR)
            add(pluck(midi_hz(x), n, 1.6, rng) * 0.8, s0 + 8 * six, 0.4, m["bell"])
        if m.get("heartbeat"):
            for st in (0, 3):
                add(kick(int(0.4 * SR)) * 0.9, s0 + st * six * 2, 0, m["kick"])
        elif m["kick"]:
            kicks = [0, 4, 8, 12] if bpm >= 100 else [0, 8, 10]
            for st in kicks:
                add(kick(int(0.45 * SR)), s0 + st * six, 0, m["kick"])
        if m["snare"]:
            for st in (4, 12):
                sn = noise_hit(int(0.25 * SR), 22, rng, hp=False) + 0.3 * tone(190, int(0.25 * SR)) * np.exp(
                    -np.arange(int(0.25 * SR)) / SR * 30)
                add(sn, s0 + st * six, 0.05, m["snare"])
                if m.get("clap"):
                    add(noise_hit(int(0.15 * SR), 35, rng), s0 + st * six + int(0.012 * SR), -0.1, m["snare"] * .6)
        if m["hat"]:
            for st in range(2, 16, 2) if bpm < 120 else range(0, 16, 1):
                add(noise_hit(int(0.06 * SR), 80, rng), s0 + st * six, 0.3, m["hat"] * (1.0 if st % 4 == 2 else .6))
    return np.stack([L, R], axis=1), bar_n


def finalize(audio, bar_n, bars, duration, loop, fade_in, fade_out):
    body = audio[:bar_n * bars].copy()
    tail = audio[bar_n * bars:]
    if loop:
        body[:len(tail)] += tail[:len(body)]          # 尾音折回开头 → 首尾无缝
    else:
        n = int(round(duration * SR))
        full = np.concatenate([body, tail])
        body = full[:n] if len(full) >= n else np.pad(full, ((0, n - len(full)), (0, 0)))
        fi, fo = int(fade_in * SR), int(fade_out * SR)
        if fi:
            body[:fi] *= np.linspace(0, 1, fi)[:, None]
        if fo:
            body[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
    body = np.tanh(body * 1.2)                          # 软限幅
    peak = np.max(np.abs(body)) or 1.0
    return body / peak * 0.89


def write(audio, out: Path):
    tmp = out.with_suffix(".tmp.wav") if out.suffix.lower() != ".wav" else out
    pcm = (audio * 32767).astype("<i2")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    if tmp != out:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                        "-ar", str(SR), "-b:a", "192k", str(out)], check=True)
        tmp.unlink()


def generate(mood="calm", duration=30.0, bpm=0, key="", loop=False, fade_in=1.0, fade_out=2.5, seed=1, out="bgm.mp3"):
    if mood not in MOODS:
        raise SystemExit(f"mood 只能是 {list(MOODS)}")
    m = MOODS[mood]
    bpm = bpm or m["bpm"]
    key = key or m["key"]
    bar_s = 4 * 60.0 / bpm
    bars = max(1, int(round(duration / bar_s))) if loop else max(1, int(np.ceil(duration / bar_s)))
    audio, bar_n = render(mood, bpm, key, bars, seed)
    audio = finalize(audio, bar_n, bars, duration, loop, fade_in, fade_out)
    outp = Path(out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    write(audio, outp)
    return {"ok": True, "output": str(outp.resolve()), "mood": mood, "bpm": bpm, "key": key,
            "mode": "major" if m["scale"] is MAJOR else "minor", "bars": bars,
            "duration": round(len(audio) / SR, 3), "loop": loop, "seed": seed,
            "license": "本地程序生成, 无第三方版权, 可商用"}


def main():
    ap = argparse.ArgumentParser(description="程序化生成配乐")
    ap.add_argument("--mood", default="calm")
    ap.add_argument("--bpm", type=int, default=0)
    ap.add_argument("--key", default="")
    ap.add_argument("--duration", type=float, default=30)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--fade-in", type=float, default=1.0)
    ap.add_argument("--fade-out", type=float, default=2.5)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-o", "--out", default="bgm.mp3")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for k, v in MOODS.items():
            print(f"{k:9s} {v['name']}  {v['bpm']}bpm  {v['key']}{'' if v['scale'] is MAJOR else 'm'}")
        return 0
    print(json.dumps(generate(a.mood, a.duration, a.bpm, a.key, a.loop, a.fade_in, a.fade_out, a.seed, a.out),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
