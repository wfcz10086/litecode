#!/usr/bin/env python3
"""文字转语音: 默认先用 LiteCode 本地纯 CPU 引擎 (core/tts_local.py, 不联网), 失败或音色本地没有时用 edge-tts
(微软在线神经语音, 免 key; 首次缺库自动 pip 安装)。--engine local|edge 可指定; 断网时 edge 失败会退回本地。

用法:
  python3 tts.py --text "大家好" -v 云希 [--emotion 激昂] [--rate +10%] [--pitch +2Hz] -o out.mp3 [--srt out.srt]
  python3 tts.py --file 文案.txt -v 晓晓 -o voice.mp3 --srt voice.srt     # 长文本自动分句合成后拼接
  python3 tts.py --list                                                  # 列声音
  python3 tts.py ... --engine edge                                       # 强制在线 (要情绪音调变化时)
输出: 音频 (mp3/wav 按扩展名) + 同名 .json (每句 start/end 秒, 从真实音频量出) + 可选 .srt。

--emotion 预设 (edge-tts 不支持 SSML 情感风格, 这里用语速/音调/音量近似):
  平静 / 激昂 / 温柔 / 悲伤 / 欢快 / 严肃 / 悬疑   —— 显式给的 --rate/--pitch 会覆盖预设
声音克隆不在本脚本 (需要 CosyVoice / GPT-SoVITS 类模型或云端复刻接口), 方案见 SKILL.md。
"""
import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

SK_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SK_DIR / "douyin-explainer-video" / "scripts"))
try:  # 复用解说视频 skill 的中文声音目录 (中文名/拼音别名 → 正式名)
    from voices import VOICES, check_voice  # noqa: E402
except Exception:  # pragma: no cover
    VOICES = {"zh-CN-YunxiNeural": {"cn": "云希", "gender": "男", "persona": "阳光男声", "suits": "通用"},
              "zh-CN-XiaoxiaoNeural": {"cn": "晓晓", "gender": "女", "persona": "温暖女声", "suits": "通用"}}

    def check_voice(name):
        for k, v in VOICES.items():
            if name in (k, v["cn"]):
                return k, None
        return "zh-CN-YunxiNeural", f"未知声音 {name}, 用云希"

EMOTIONS = {  # (rate %, pitch Hz, volume %)
    "平静": (0, 0, 0), "激昂": (12, 4, 10), "温柔": (-8, -2, -5), "悲伤": (-15, -6, -8),
    "欢快": (10, 6, 5), "严肃": (-4, -4, 0), "悬疑": (-10, -5, -5),
}
SENT_SPLIT = re.compile(r"(?<=[。！？!?；;…])|\n+")


def ensure_edge_tts():
    try:
        import edge_tts  # noqa
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "edge-tts"], check=True)
    import edge_tts
    return edge_tts


def ffprobe_dur(p) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def speech_bounds(path, noise="-40dB", min_sil=0.05):
    """用 silencedetect 量出真正有人声的起止 (去掉 edge-tts 自带的首尾静音)。→ (start, end, total)"""
    total = ffprobe_dur(path)
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af",
                          f"silencedetect=noise={noise}:d={min_sil}", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    s, e = 0.0, total
    if starts and starts[0] <= 0.01 and ends:
        s = ends[0]
    if starts and starts[-1] > s and (len(ends) < len(starts) or ends[-1] >= total - 0.02):
        e = starts[-1]
    return max(0.0, s), max(s, min(e, total)), total


def speech_intervals(path, noise="-40dB", min_sil=0.15):
    """整条人声轨的有声区间 [(on, off)], 给时间线校验用。"""
    total = ffprobe_dur(path)
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af",
                          f"silencedetect=noise={noise}:d={min_sil}", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    on, cur = [], 0.0
    for st, en in zip(starts, ends + [total] * (len(starts) - len(ends))):
        if st > cur + 0.01:
            on.append((cur, st))
        cur = en
    if cur < total - 0.01:
        on.append((cur, total))
    return on


def _pct(v):
    return f"{'+' if v >= 0 else ''}{int(v)}%"


def _hz(v):
    return f"{'+' if v >= 0 else ''}{int(v)}Hz"


def prosody(emotion="", rate="", pitch="", volume=""):
    r, p, v = EMOTIONS.get(emotion or "平静", (0, 0, 0))
    return rate or _pct(r), pitch or _hz(p), volume or _pct(v)


def split_sentences(text: str, max_len=60) -> list:
    out = []
    for s in SENT_SPLIT.split(text or ""):
        s = s.strip()
        while len(s) > max_len:  # 超长句在逗号处再切
            cut = max((s.rfind(c, 0, max_len) for c in "，,、"), default=-1)
            cut = cut + 1 if cut > 10 else max_len
            out.append(s[:cut])
            s = s[cut:].strip()
        if s and re.search(r"[\w一-鿿]", s):
            out.append(s)
    return out


async def _synth_one(edge_tts, text, voice, rate, pitch, volume, path, retries=3):
    for i in range(retries):
        try:
            await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume).save(str(path))
            if path.exists() and path.stat().st_size > 500:
                return
        except Exception as e:  # 网络抖动
            err = e
        await asyncio.sleep(1.5 * (i + 1))
    raise RuntimeError(f"edge-tts 合成失败: {text[:20]}… ({locals().get('err')})")


def _local_engine():
    """LiteCode 本地 TTS 模块 (装了 sherpa-onnx 和模型才可用), 拿不到返回 None。"""
    import os
    home = os.environ.get("LITECODE_HOME", "/opt/litecode")
    for p in (home, str(Path(home) / "core"), str(SK_DIR.parent), str(SK_DIR.parent / "core")):
        if p not in sys.path:
            sys.path.append(p)
    try:
        from core import tts_local
    except Exception:
        try:
            import tts_local
        except Exception:
            return None
    return tts_local if tts_local.available() else None


def _synth_sentence(engine, edge_tts, local, s, vname, voice, rate, pitch, volume, mp3, wav_local):
    """合成一句; 返回 (实际引擎, 音频文件)。auto: 本地优先, 失败换 edge; edge 失败 (断网) 再试本地。"""
    sid = None
    if local is not None:
        sid, _ = local.resolve(voice)
        if sid is None:
            sid, _ = local.resolve(vname)
    order = {"local": ["local", "edge"], "edge": ["edge", "local"]}.get(engine) or (["local", "edge"] if sid is not None else ["edge", "local"])
    err = None
    for eng in order:
        try:
            if eng == "local":
                if local is None:
                    continue
                local.synth_wav(s, sid if sid is not None else local.DEFAULT_SID, wav_local, rate)
                return "local", wav_local
            if edge_tts is None:
                edge_tts = ensure_edge_tts()
            asyncio.run(_synth_one(edge_tts, s, vname, rate, pitch, volume, mp3))
            return "edge", mp3
        except Exception as e:
            err = e
    raise RuntimeError(f"本地和在线语音都合成失败: {s[:20]}… ({err})")


def synth(text, out, voice="云希", emotion="", rate="", pitch="", volume="", srt="", gap=0.12, engine="auto"):
    """逐句合成 → 拼接成 out; 句子时间戳用每句真实音频时长累加 (ffprobe), 不估算。"""
    local = _local_engine() if engine in ("auto", "local") else None
    edge_tts = None
    used = set()
    vname, warn = check_voice(voice)
    rate, pitch, volume = prosody(emotion, rate, pitch, volume)
    sents = split_sentences(text)
    if not sents:
        raise SystemExit("文本为空")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f".{out.stem}_parts"
    tmp.mkdir(exist_ok=True)
    parts, cues, t = [], [], 0.0
    for i, s in enumerate(sents):
        mp3 = tmp / f"s{i:03d}.mp3"
        eng, src = _synth_sentence(engine, edge_tts, local, s, vname, voice, rate, pitch, volume, mp3,
                                   tmp / f"s{i:03d}_raw.wav")
        used.add(eng)
        wav = tmp / f"s{i:03d}.wav"   # 统一 48k 单声道 PCM, 拼接无缝无编码间隙
        pad = gap if i < len(sents) - 1 else 0
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", f"apad=pad_dur={pad}",
                        "-ar", "48000", "-ac", "1", str(wav)], check=True)
        s0, s1, _ = speech_bounds(wav)
        d_total = ffprobe_dur(wav)
        cues.append({"text": s, "start": round(t + s0, 3), "end": round(t + s1, 3)})
        t += d_total
        parts.append(wav)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    codec = ["-c:a", "pcm_s16le"] if out.suffix.lower() == ".wav" else ["-c:a", "libmp3lame", "-b:a", "192k"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), *codec,
                    "-ar", "48000", str(out)], check=True)
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()
    meta = {"output": str(out.resolve()), "voice": vname, "engine": "+".join(sorted(used)), "rate": rate, "pitch": pitch, "volume": volume,
            "duration": round(ffprobe_dur(out), 3), "sentences": cues}
    if warn:
        meta["warning"] = warn
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if srt:
        Path(srt).write_text(to_srt(cues), encoding="utf-8")
        meta["srt"] = str(Path(srt).resolve())
    return meta


def _ts(s):
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(cues):
    return "".join(f"{i}\n{_ts(c['start'])} --> {_ts(c['end'])}\n{c['text']}\n\n" for i, c in enumerate(cues, 1))


def main():
    ap = argparse.ArgumentParser(description="文字转语音 (本地纯 CPU 优先, edge-tts 兜底)")
    ap.add_argument("--engine", default="auto", choices=["auto", "local", "edge"])
    ap.add_argument("--text", default="")
    ap.add_argument("--file", default="")
    ap.add_argument("-v", "--voice", default="云希")
    ap.add_argument("--emotion", default="", choices=[""] + list(EMOTIONS))
    ap.add_argument("--rate", default="")
    ap.add_argument("--pitch", default="")
    ap.add_argument("--volume", default="")
    ap.add_argument("--gap", type=float, default=0.12, help="句间停顿秒")
    ap.add_argument("-o", "--out", default="/tmp/litecode_workspace/tts/voice.mp3")
    ap.add_argument("--srt", default="")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list:
        for k, v in VOICES.items():
            print(f"{v['cn']:4s} {v['gender']} {k:32s} {v['persona']} | {v['suits']}")
        print("情绪预设:", " / ".join(EMOTIONS))
        return 0
    text = Path(a.file).read_text(encoding="utf-8") if a.file else a.text
    meta = synth(text, a.out, a.voice, a.emotion, a.rate, a.pitch, a.volume, a.srt, a.gap, a.engine)
    meta["sentences"] = meta["sentences"][:3] + ([{"...": len(meta["sentences"]) - 3}] if len(meta["sentences"]) > 3 else [])
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
