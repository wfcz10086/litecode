#!/usr/bin/env python3
"""时间线 JSON → 成片 mp4 (配音 + 字幕 + 画面 + BGM), 多轨时间严格对齐并自动校验。

用法:
  python3 timeline.py timeline.json [-o out.mp4] [--keep-build]
  python3 timeline.py --example > timeline.json        # 打印示例

对齐原则 (都是硬约束, 校验不过就报错, 不静默交付):
  - 每段时长 = 配音真实时长 (ffprobe 量) + gap, 向上取整到整帧; 配音按样本数精确补齐到该时长
    → 画面切换点、配音段起点、字幕时间全部落在同一套帧边界上, 不会累积漂移
  - 字幕时间 = 每句真实人声起止 (silencedetect 量出, 去掉首尾静音), 不按字数估
  - 固定帧率 CFR (默认 30fps), 音频统一 48kHz; BGM 按成片总长裁剪/循环, 末尾淡出对齐成片结尾, 人声出现时自动压低 (ducking)
  - 校验: 视频流/音频流时长差 ≤ 1 帧; 帧数 = 各段帧数之和; 末条字幕结束 ≤ 成片时长;
          每句字幕起止与成片人声轨实测起止误差 ≤ 100ms; 输出 report.json / report.md

timeline.json 字段见 --example 和 SKILL.md。
"""
import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "bgm-music" / "scripts"))
import tts  # noqa: E402

EXAMPLE = {
    "title": "示例成片",
    "size": [1080, 1920], "fps": 30, "sample_rate": 48000,
    "voice": "云希", "emotion": "", "rate": "", "gap": 0.35,
    "subtitle": {"burn": True, "size": 66, "max_chars": 14, "margin_v": 300},
    "bgm": {"mood": "upbeat", "seed": 1, "volume": 0.22, "duck": True, "fade_in": 1.0, "fade_out": 2.0},
    "segments": [
        {"text": "三秒钟告诉你, 为什么这条视频能火。", "visual": "color:#1e293b"},
        {"text": "第一, 开头就抛出反差。第二, 每句都有画面。", "visual": "frames/f01.jpg", "motion": "zoom_in"},
        {"text": "关注我, 下期拆更多爆款。", "visual": "clip.mp4", "voice": "晓晓", "emotion": "欢快"},
    ],
}


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(map(str, cmd[:6]))} …\n{r.stderr[-800:]}")
    return r


def probe_streams(p) -> dict:
    out = run(["ffprobe", "-v", "error", "-count_packets", "-show_entries",
               "stream=codec_type,duration,nb_read_packets,avg_frame_rate,r_frame_rate,sample_rate,width,height"
               ":format=duration", "-of", "json", str(p)]).stdout
    return json.loads(out)


def is_image(p: str) -> bool:
    return Path(p).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def cjk_font() -> str:
    try:
        fams = run(["fc-list", ":lang=zh", "family"]).stdout
    except Exception:
        return "Noto Sans CJK SC"
    for want in ("Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei", "Noto Serif CJK SC"):
        if want in fams:
            return want
    return fams.splitlines()[0].split(",")[0] if fams.strip() else "Sans"


# ---------------------------------------------------------------- 字幕
def split_caption(text: str, max_chars: int) -> list:
    """一句拆成若干屏字幕: 先按逗号类标点, 仍超长再硬切; 去掉句末标点。"""
    text = re.sub(r"[。！？!?；;…]+$", "", text.strip())
    pieces = [p for p in re.split(r"(?<=[，,、：:])", text) if p.strip()]
    out, buf = [], ""
    for p in pieces:
        if len(buf) + len(p) <= max_chars:
            buf += p
        else:
            if buf:
                out.append(buf)
            while len(p) > max_chars:
                out.append(p[:max_chars])
                p = p[max_chars:]
            buf = p
    if buf:
        out.append(buf)
    # 屏内逗号换成空格 (短视频字幕习惯), 句末/屏尾标点去掉
    return [re.sub(r"[，,、]", " ", re.sub(r"[，,、：:]$", "", o)).strip() for o in out if o.strip()]


def build_cues(sent_cues, offset, max_chars):
    """句级时间 (真实人声起止) → 屏级字幕; 句内按字数比例分时间, 句首/句尾严格等于实测值。"""
    cues = []
    for sc in sent_cues:
        parts = split_caption(sc["text"], max_chars) or [sc["text"]]
        total = sum(len(p) for p in parts)
        t0, span = offset + sc["start"], sc["end"] - sc["start"]
        acc = 0
        for i, p in enumerate(parts):
            s = t0 + span * acc / total
            acc += len(p)
            e = t0 + span * acc / total
            cues.append({"text": p, "start": round(s, 3), "end": round(e, 3),
                         "sent_start": i == 0, "sent_end": i == len(parts) - 1})
    return cues


def _ass_ts(s):
    cs = int(round(s * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_ass(cues, path: Path, W, H, sub):
    font = sub.get("font") or cjk_font()
    size, mv = sub.get("size", 66), sub.get("margin_v", 300)
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,%s,%d,&H00FFFFFF,&H000000FF,&H00101010,&H64000000,1,0,0,0,100,100,1,0,1,5,1,2,60,60,%d,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n") % (W, H, font, size, mv)
    body = "".join(f"Dialogue: 0,{_ass_ts(c['start'])},{_ass_ts(c['end'])},Default,,0,0,0,,{c['text']}\n" for c in cues)
    path.write_text(head + body, encoding="utf-8")
    return font


# ---------------------------------------------------------------- 画面
def render_visual(seg, i, frames, W, H, fps, build: Path, base: Path):
    vis = str(seg.get("visual") or "")
    out = build / f"v{i:03d}.mp4"
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps),
           "-g", str(fps * 2), "-an", "-frames:v", str(frames), str(out)]
    cover = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    if not vis or vis.startswith("color:"):
        c = vis[6:] if vis else ["#1e293b", "#0f3d3e", "#3b1d4a", "#4a2c12"][i % 4]
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={c}:s={W}x{H}:r={fps}", *enc])
        return out
    p = Path(vis) if Path(vis).is_absolute() else base / vis
    if not p.exists():
        raise FileNotFoundError(f"第 {i + 1} 段画面素材不存在: {p}")
    if is_image(str(p)):
        motion = seg.get("motion", "zoom_in")
        n = frames
        z = {"zoom_in": f"1+0.10*on/{n}", "zoom_out": f"1.10-0.10*on/{n}", "pan": "1.08", "none": "1"}.get(motion, "1")
        x = f"(iw-iw/zoom)*on/{n}" if motion == "pan" else "iw/2-(iw/zoom/2)"
        vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
              f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={n}:s={W}x{H}:fps={fps},setsar=1")
        run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-vf", vf, *enc])
    else:  # 视频素材: 循环/截断到正好 frames 帧, 去原声
        ss = ["-ss", str(seg["clip_start"])] if seg.get("clip_start") else []
        run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", *ss, "-i", str(p),
             "-vf", f"{cover},fps={fps}", *enc])
    return out


# ---------------------------------------------------------------- 音频
def seg_voice(seg, i, tl, build: Path, base: Path):
    """→ (wav 路径, 句级 cues 相对本段)。预录音频用 audio 字段, 否则 TTS。"""
    wav = build / f"a{i:03d}_raw.wav"
    if seg.get("audio"):
        src = Path(seg["audio"]) if Path(seg["audio"]).is_absolute() else base / seg["audio"]
        run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-ar", "48000", "-ac", "1", str(wav)])
        s0, s1, _ = tts.speech_bounds(wav)
        return wav, [{"text": seg.get("text", ""), "start": s0, "end": s1}] if seg.get("text") else []
    if not seg.get("text", "").strip():
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t",
             str(seg.get("min_dur", 2.0)), str(wav)])
        return wav, []
    meta = tts.synth(seg["text"], wav, seg.get("voice") or tl.get("voice", "云希"),
                     seg.get("emotion") or tl.get("emotion", ""), seg.get("rate") or tl.get("rate", ""),
                     seg.get("pitch", ""), "", "", tl.get("sentence_gap", 0.12))
    return wav, meta["sentences"]


def build_mix(voice: Path, total_samples: int, sr: int, bgm: dict, build: Path, base: Path) -> Path:
    T = total_samples / sr
    mix = build / "mix.wav"
    vo = build / "voice_norm.wav"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(voice), "-af",
         f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample={sr},apad=whole_len={total_samples},atrim=end_sample={total_samples}",
         "-ac", "2", "-ar", str(sr), str(vo)])
    if not bgm:
        shutil.copy(vo, mix)
        return mix
    if bgm.get("file"):
        src = Path(bgm["file"]) if Path(bgm["file"]).is_absolute() else base / bgm["file"]
    else:
        import bgm_gen
        src = build / "bgm_gen.wav"
        bgm_gen.generate(bgm.get("mood", "calm"), T + 0.5, bgm.get("bpm", 0), bgm.get("key", ""), False,
                         0, 0, bgm.get("seed", 1), str(src))
    vol, fi, fo = bgm.get("volume", 0.22), bgm.get("fade_in", 1.0), min(bgm.get("fade_out", 2.0), T / 2)
    bg = (f"[1:a]aresample={sr},aformat=channel_layouts=stereo,atrim=0:{T:.4f},asetpts=PTS-STARTPTS,"
          f"volume={vol},afade=t=in:st=0:d={fi},afade=t=out:st={T - fo:.4f}:d={fo}[bg];")
    if bgm.get("duck", True):
        fc = (bg + "[0:a]asplit=2[v1][v2];[bg][v2]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=500[bgd];"
              "[v1][bgd]amix=inputs=2:normalize=0:duration=first")
    else:
        fc = bg + "[0:a][bg]amix=inputs=2:normalize=0:duration=first"
    fc += f",alimiter=limit=0.95,apad=whole_len={total_samples},atrim=end_sample={total_samples}[m]"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(vo), "-stream_loop", "-1", "-i", str(src),
         "-filter_complex", fc, "-map", "[m]", "-ar", str(sr), "-ac", "2", str(mix)])
    return mix


# ---------------------------------------------------------------- 校验
def wav_samples(p) -> int:
    out = run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration_ts",
               "-of", "csv=p=0", str(p)]).stdout.strip()
    return int(out or 0)


def verify(out: Path, fps, sr, exp_frames, cues, voice: Path, segs) -> dict:
    info = probe_streams(out)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    aus = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    fmt_dur = float(info["format"]["duration"])
    vdur = float(vs.get("duration") or 0)
    adur = float(aus.get("duration") or 0) if aus else 0
    frame = 1 / fps
    num, den = (int(x) for x in vs["avg_frame_rate"].split("/"))
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("固定帧率", den and abs(num / den - fps) < 1e-3 and vs["avg_frame_rate"] == vs["r_frame_rate"],
        f"avg={vs['avg_frame_rate']} r={vs['r_frame_rate']} 目标 {fps}")
    add("帧数", int(vs.get("nb_read_packets") or 0) == exp_frames,
        f"实际 {vs.get('nb_read_packets')} 期望 {exp_frames}")
    add("音频采样率", aus and int(aus["sample_rate"]) == sr, f"{aus and aus['sample_rate']} 目标 {sr}")
    add("音画时长差≤1帧", aus and abs(vdur - adur) <= frame + 1e-3,
        f"视频 {vdur:.3f}s 音频 {adur:.3f}s 差 {abs(vdur - adur) * 1000:.1f}ms (1 帧 {frame * 1000:.1f}ms)")
    last = max((c["end"] for c in cues), default=0)
    add("末条字幕≤成片时长", last <= fmt_dur + 1e-3, f"末条字幕 {last:.3f}s 成片 {fmt_dur:.3f}s")
    # 字幕 vs 人声轨实测
    iv = tts.speech_intervals(voice, min_sil=0.08)
    ons, offs = [a for a, _ in iv], [b for _, b in iv]
    worst, bad = 0.0, []
    for c in cues:
        for key, pts, flag in (("start", ons, "sent_start"), ("end", offs, "sent_end")):
            if not c.get(flag) or not pts:
                continue
            d = min(abs(c[key] - p) for p in pts)
            worst = max(worst, d)
            if d > 0.1:
                bad.append(f"{c['text'][:10]}… {key} 偏差 {d * 1000:.0f}ms")
    add("字幕与人声对齐≤100ms", not bad, f"最大偏差 {worst * 1000:.0f}ms" + (f"; 超标: {bad[:5]}" if bad else ""))
    # 每段: 画面起始帧 × 每帧样本数 == 人声轨里该段实际起始样本 (逐段量 wav 样本数累加)
    spf, acc, mism = sr // fps, 0, []
    for s in segs:
        if s["start_frame"] * spf != acc:
            mism.append(f"第{s['index']}段 画面帧{s['start_frame']} 音频样本{acc}")
        acc += s["samples"]
    add("段落边界音画一致", not mism and acc == exp_frames * spf,
        f"{len(segs)} 段, 画面切换点与配音段起点逐段一致 (样本级)" if not mism else "; ".join(mism[:3]))
    return {"ok": all(c["ok"] for c in checks), "video_s": vdur, "audio_s": adur, "format_s": fmt_dur,
            "frames": vs.get("nb_read_packets"), "checks": checks}


# ---------------------------------------------------------------- 主流程
def render(tl_path: Path, out: Path, keep=False) -> dict:
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    base = tl_path.parent.resolve()
    W, H = tl.get("size", [1080, 1920])
    fps = int(tl.get("fps", 30))
    sr = int(tl.get("sample_rate", 48000))
    if sr % fps:
        raise SystemExit(f"sample_rate {sr} 不能被 fps {fps} 整除, 请用 24/25/30/60 fps")
    spf = sr // fps                          # 每帧样本数
    gap = float(tl.get("gap", 0.35))
    sub = tl.get("subtitle", {}) or {}
    max_chars = int(sub.get("max_chars", 14))
    build = out.parent / f".{out.stem}_build"
    build.mkdir(parents=True, exist_ok=True)

    seg_info, cues, wavs, t_frames = [], [], [], 0
    for i, seg in enumerate(tl["segments"]):
        raw, sent = seg_voice(seg, i, tl, build, base)
        speech = tts.ffprobe_dur(raw)
        dur = max(speech + float(seg.get("gap", gap)), float(seg.get("min_dur", 0)))
        frames = math.ceil(dur * fps - 1e-9)
        nsamp = frames * spf
        wav = build / f"a{i:03d}.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af", f"apad=whole_len={nsamp},atrim=end_sample={nsamp}",
             "-ar", str(sr), "-ac", "1", str(wav)])
        start = t_frames / fps
        cues += build_cues(sent, start, max_chars)
        seg_info.append({"index": i + 1, "start": round(start, 4), "start_frame": t_frames,
                         "samples": wav_samples(wav), "frames": frames, "duration": round(frames / fps, 4),
                         "speech": round(speech, 3), "visual": seg.get("visual", ""), "text": seg.get("text", "")})
        render_visual(seg, i, frames, W, H, fps, build, base)
        wavs.append(wav)
        t_frames += frames
    total_samples = t_frames * spf
    T = t_frames / fps

    # 人声轨 + 画面轨拼接
    (build / "a_list.txt").write_text("".join(f"file '{w.name}'\n" for w in wavs), encoding="utf-8")
    voice = build / "voice.wav"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(build / "a_list.txt"),
         "-c:a", "pcm_s16le", str(voice)])
    (build / "v_list.txt").write_text("".join(f"file 'v{i:03d}.mp4'\n" for i in range(len(wavs))), encoding="utf-8")
    vraw = build / "video_raw.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(build / "v_list.txt"), "-c", "copy",
         str(vraw)])
    mix = build_mix(voice, total_samples, sr, tl.get("bgm"), build, base)

    srt = out.with_suffix(".srt")
    srt.write_text(tts.to_srt(cues), encoding="utf-8")
    vf = "setpts=N/(%d*TB)" % fps
    font = ""
    if sub.get("burn", True) and cues:
        ass = build / "subs.ass"
        font = write_ass(cues, ass, W, H, sub)
        vf += f",subtitles={ass}"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(vraw), "-i", str(mix), "-map", "0:v", "-map", "1:a",
         "-vf", vf, "-r", str(fps), "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", str(sr), "-frames:v", str(t_frames),
         "-movflags", "+faststart", str(out)])

    rep = verify(out, fps, sr, t_frames, cues, voice, seg_info)
    if not rep["ok"] and abs(rep["video_s"] - rep["audio_s"]) > 1 / fps:   # 自动修正一次: 音频按视频时长重切
        fixed = out.with_name(out.stem + "_fix.mp4")
        run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-c:v", "copy", "-af",
             f"apad,atrim=0:{rep['video_s']:.4f}", "-c:a", "aac", "-b:a", "192k", str(fixed)])
        fixed.replace(out)
        rep = verify(out, fps, sr, t_frames, cues, voice, seg_info)
        rep["auto_fixed"] = "音频按视频时长重新裁剪"
    report = {"output": str(out.resolve()), "srt": str(srt.resolve()), "fps": fps, "sample_rate": sr,
              "size": [W, H], "total_s": round(T, 4), "total_frames": t_frames, "font": font,
              "bgm": tl.get("bgm"), "segments": seg_info, "cues": len(cues), "verify": rep}
    (out.with_suffix(".report.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# 时间线校验报告: {out.name}", "", f"- 总长 {T:.3f}s / {t_frames} 帧 @ {fps}fps CFR, 音频 {sr}Hz",
          f"- 结论: {'通过' if rep['ok'] else '未通过'}", "", "| 检查 | 结果 | 详情 |", "|---|---|---|"]
    md += [f"| {c['name']} | {'OK' if c['ok'] else 'FAIL'} | {c['detail']} |" for c in rep["checks"]]
    md += ["", "| 段 | 起点 s | 帧数 | 时长 s | 人声 s | 画面 |", "|---|---|---|---|---|---|"]
    md += [f"| {s['index']} | {s['start']} | {s['frames']} | {s['duration']} | {s['speech']} | {s['visual'] or '-'} |"
           for s in seg_info]
    (out.with_suffix(".report.md")).write_text("\n".join(md) + "\n", encoding="utf-8")
    if not keep:
        shutil.rmtree(build, ignore_errors=True)
    return report


def main():
    ap = argparse.ArgumentParser(description="时间线 → 成片 (多轨对齐 + 校验)")
    ap.add_argument("timeline", nargs="?")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--keep-build", action="store_true")
    ap.add_argument("--example", action="store_true")
    a = ap.parse_args()
    if a.example or not a.timeline:
        print(json.dumps(EXAMPLE, ensure_ascii=False, indent=2))
        return 0
    tlp = Path(a.timeline).resolve()
    out = Path(a.out).resolve() if a.out else tlp.with_suffix(".mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    rep = render(tlp, out, a.keep_build)
    v = rep["verify"]
    print(json.dumps({"ok": v["ok"], "output": rep["output"], "srt": rep["srt"],
                      "report": str(out.with_suffix(".report.md")), "total_s": rep["total_s"],
                      "frames": rep["total_frames"], "checks": [f"{'OK ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}"
                                                                for c in v["checks"]]},
                     ensure_ascii=False, indent=2))
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
