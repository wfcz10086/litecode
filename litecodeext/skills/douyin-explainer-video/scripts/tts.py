#!/usr/bin/env python3
"""step1: script.json → edge-tts 逐场旁白 audio/<id>.mp3 → timeline.json (每场 start/dur + total)
用法 (在项目目录): python3 <skill>/scripts/tts.py [script.json]
script.json 字段见 SKILL.md; 必填 scenes[].id / text / type。
选声 / 多角色 (voices + role) / 韵律 (rate/pitch/volume + prosody) 见 voices.py。
"""
import asyncio, json, os, re, subprocess, sys

import edge_tts

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voices as V  # noqa: E402

DEFAULTS = {"voice": "zh-CN-YunxiNeural", "rate": "+14%", "gap": 0.7}


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


async def synth(scenes, plan):
    for s, p in zip(scenes, plan):
        await edge_tts.Communicate(s["text"], p["voice"], rate=p["rate"], pitch=p["pitch"], volume=p["volume"]).save(f"audio/{s['id']}.mp3")
        print(f"tts ok {s['id']} {V.VOICES[p['voice']]['cn']} {p['beat']} rate={p['rate']} pitch={p['pitch']} vol={p['volume']}", flush=True)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "script.json"
    sj = json.load(open(src, encoding="utf-8"))
    validate(sj)
    cfg = {**DEFAULTS, **{k: sj[k] for k in DEFAULTS if k in sj}}
    plan, tips = V.plan(sj)
    for tip in tips:
        print("  ⚠", tip, flush=True)
    os.makedirs("audio", exist_ok=True)
    asyncio.run(synth(sj["scenes"], plan))
    t, scenes = 0.0, []
    for s, p in zip(sj["scenes"], plan):
        d = dur_of(f"audio/{s['id']}.mp3")
        scenes.append({**s, "start": round(t, 3), "dur": round(d, 3), "tts": p})
        print(f"{s['id']} {s.get('act', '')}: start={t:.1f}s dur={d:.1f}s")
        t += d + cfg["gap"]
    tl = {**{k: v for k, v in sj.items() if k != "scenes"}, "scenes": scenes, "total": round(t - cfg["gap"], 3)}
    json.dump(tl, open("timeline.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"TOTAL = {tl['total']:.1f}s")


if __name__ == "__main__":
    main()
