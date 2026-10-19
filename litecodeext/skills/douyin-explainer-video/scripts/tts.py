#!/usr/bin/env python3
"""step1: script.json → edge-tts 旁白 audio/<id>.mp3 → timeline.json (每场 start/dur/subs + total)
用法 (在项目目录): python3 <skill>/scripts/tts.py [script.json]
script.json 字段见 SKILL.md; 必填 scenes[].id / text / type。
选声 / 多角色 (voices + role) / 韵律 (rate/pitch/volume + prosody) 见 voices.py。

[2026-10-19] 默认逐句合成 ("tts_mode": "sentence"): 每句按 voices.sentences() 单独调语速/音高/音量,
句间插停顿 (问句后留悬念、短句前顿一下), 再拼成一场。整场一口气念 = 播报腔, 用户反馈"声音没感觉"。
每句真实起止写进 timeline 的 subs, engine.js 字幕按它对齐 (不再按字数比例猜)。
"tts_mode": "scene" 回到整场一次合成。
"""
import asyncio, json, os, re, subprocess, sys

import edge_tts
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voices as V  # noqa: E402

DEFAULTS = {"voice": "zh-CN-YunxiNeural", "rate": V.DEFAULT_RATE, "gap": 0.7}
SR = 24000
SUB_RE = re.compile(r"(?<=[，。！？；：])")       # 与 engine.js phrases() 同一切法


def dur_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def validate(sj):
    ids = [s.get("id") for s in sj.get("scenes", [])]
    assert ids, "scenes 为空"
    assert len(ids) == len(set(ids)), f"scene id 重复: {ids}"
    for s in sj["scenes"]:
        assert s.get("text", "").strip(), f"{s['id']} 没有 text"
        assert s.get("type"), f"{s['id']} 没有 type"
        bad = re.findall(r"[A-Za-z]{3,}", s["text"])
        assert not bad, f"{s['id']} 旁白里有英文 {bad} — TTS 会念得很怪, 改成中文"


def _add(v, d, unit):
    return V._fmt(V._num(v, unit) + d, unit)


def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16)


def trim(a, thr=200):
    """edge-tts 每句前后自带 ~0.1-0.3s 静音, 逐句拼时会叠成拖沓的空档 → 削到各留 40ms。"""
    idx = np.nonzero(np.abs(a.astype(np.int32)) > thr)[0]
    if not len(idx):
        return a
    pad = int(.04 * SR)
    return a[max(0, idx[0] - pad): idx[-1] + pad]


def subs_for(sents, spans, total):
    """逐句真实时长 → 字幕短语 {p, t0, t1} (0-1 比例)。句内再按逗号切, 按字数比例分这句的时长。"""
    out = []
    for s, (a, b) in zip(sents, spans):
        ph = [x for x in SUB_RE.split(s["text"]) if x.strip()]
        tot = sum(len(x) for x in ph) or 1
        c = 0
        for x in ph:
            t0 = a + (b - a) * c / tot
            c += len(x)
            out.append({"p": x, "t0": round(t0 / total, 4), "t1": round((a + (b - a) * c / tot) / total, 4)})
    if out:
        out[-1]["t1"] = 1.0
    return out


async def _say(text, voice, rate, pitch, volume, path, sem):
    async with sem:
        for k in range(3):
            try:
                await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume).save(path)
                return
            except Exception:
                if k == 2:
                    raise
                await asyncio.sleep(1.5 * (k + 1))


async def synth_scene(s, p, sents, sem):
    """逐句合成 → 拼一场 audio/<id>.mp3, 返回每句 (起, 止) 秒。"""
    os.makedirs("audio/parts", exist_ok=True)
    paths = [f"audio/parts/{s['id']}_{i:02d}.mp3" for i in range(len(sents))]
    await asyncio.gather(*[
        _say(x["text"], p["voice"], _add(p["rate"], x["dr"], "%"), _add(p["pitch"], x["dp"], "Hz"),
             _add(p["volume"], x["dv"], "%"), path, sem) for x, path in zip(sents, paths)])
    chunks, spans, t = [], [], 0.0
    for x, path in zip(sents, paths):
        a = trim(decode(path))
        spans.append((t, t + len(a) / SR))
        chunks += [a, np.zeros(int(x["pause"] * SR), dtype=np.int16)]
        t += len(a) / SR + x["pause"]
    pcm = np.concatenate(chunks)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-",
                    "-c:a", "libmp3lame", "-b:a", "96k", f"audio/{s['id']}.mp3"], input=pcm.tobytes(), check=True)
    return spans


async def synth(scenes, plan, mode, highlight, auto):
    sem = asyncio.Semaphore(4)
    spans = {}
    for s, p in zip(scenes, plan):
        if mode == "scene":
            await _say(s["text"], p["voice"], p["rate"], p["pitch"], p["volume"], f"audio/{s['id']}.mp3", sem)
            spans[s["id"]] = None
            n = 1
        else:
            sents = V.sentences(s["text"], highlight, auto)
            spans[s["id"]] = (sents, await synth_scene(s, p, sents, sem))
            n = len(sents)
        print(f"tts ok {s['id']} {V.VOICES[p['voice']]['cn']} {p['beat']} rate={p['rate']} pitch={p['pitch']} "
              f"vol={p['volume']} 句数={n}", flush=True)
    return spans


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "script.json"
    sj = json.load(open(src, encoding="utf-8"))
    validate(sj)
    cfg = {**DEFAULTS, **{k: sj[k] for k in DEFAULTS if k in sj}}
    mode = sj.get("tts_mode", "sentence")
    assert mode in ("sentence", "scene"), f"tts_mode 只能是 sentence / scene, 收到 {mode!r}"
    auto = str(sj.get("prosody", "auto")).lower() != "off"
    plan, tips = V.plan(sj)
    for tip in tips:
        print("  ⚠", tip, flush=True)
    os.makedirs("audio", exist_ok=True)
    spans = asyncio.run(synth(sj["scenes"], plan, mode, sj.get("highlight"), auto))
    t, scenes = 0.0, []
    for s, p in zip(sj["scenes"], plan):
        d = dur_of(f"audio/{s['id']}.mp3")
        sc = {**s, "start": round(t, 3), "dur": round(d, 3), "tts": p}
        if spans[s["id"]]:
            sc["subs"] = subs_for(*spans[s["id"]], d)
        scenes.append(sc)
        print(f"{s['id']} {s.get('act', '')}: start={t:.1f}s dur={d:.1f}s")
        t += d + cfg["gap"]
    tl = {**{k: v for k, v in sj.items() if k != "scenes"}, "scenes": scenes, "total": round(t - cfg["gap"], 3)}
    json.dump(tl, open("timeline.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"TOTAL = {tl['total']:.1f}s")


if __name__ == "__main__":
    main()
