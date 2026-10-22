#!/usr/bin/env python3
"""视频文案解读素材准备: 抖音链接 / 本地视频 → 口播转写 + 关键帧 + 拼图 + analysis.json

用法:
  python3 analyze.py "<分享文本|链接|本地视频路径>" [-o 目录] [--frames 8] [--no-asr] [--keep-video]

产出 (目录默认 /tmp/litecode_workspace/douyin/<id>/analysis):
  transcript.txt      口播全文 (ASR; 没有 ASR 或纯音乐时为空)
  frames/f01_*.jpg    关键帧 (场景切换优先, 不足均匀补齐, ≤8 张, 直接喂 vision_ocr paths)
  grid.jpg            关键帧拼图 (一张图总览节奏)
  analysis.json       作品信息 + 转写 + 帧列表 + 下一步提示
之后由模型: vision_ocr(paths=frames) 识别画面和字幕 → 按 SKILL.md 的「解读模板」输出。

作品被设为不可下载时, 只临时取流做分析, 结束删除视频文件 (除非是本地文件或 --keep-video 且允许下载)。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import asr  # noqa: E402
import douyin  # noqa: E402

MAX_FRAMES = 8


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def scene_times(video: Path, dur: float, n: int) -> list:
    """ffmpeg 场景检测取切换点; 不够 n 个用均匀时间点补齐; 首帧固定取 0.5s (钩子画面)。"""
    out = _run(["ffmpeg", "-hide_banner", "-i", str(video), "-vf", "select='gt(scene,0.32)',showinfo",
                "-an", "-f", "null", "-"], timeout=300).stderr
    cuts = [float(m) for m in re.findall(r"pts_time:([\d.]+)", out)]
    times = [min(0.5, dur / 4)]
    min_gap = max(dur / (n * 2), 0.8)
    for t in cuts:
        if t < dur - 0.3 and all(abs(t + 0.4 - x) >= min_gap for x in times):
            times.append(t + 0.4)   # 切换后稍等, 避开转场模糊
    if len(times) > n:  # 太多切换 → 均匀抽样保留
        step = len(times) / n
        times = [times[int(i * step)] for i in range(n)]
    k = 1
    while len(times) < n and k < 50:
        cand = [dur * (i + 0.5) / n for i in range(n)]
        for t in cand:
            if len(times) < n and all(abs(t - x) >= min_gap / k for x in times):
                times.append(t)
        k += 1
    return sorted(times)[:n]


def extract_frames(video: Path, out: Path, times: list) -> list:
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, t in enumerate(times, 1):
        p = out / f"f{i:02d}.jpg"          # 文件名不带时间 (模型照抄路径不易出错), 时间记在 frame_times
        _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
              "-vf", "scale='min(720,iw)':-2", "-q:v", "3", str(p)])
        if p.exists():
            paths.append(str(p))
    return paths


def make_grid(frames: list, out: Path) -> str:
    if not frames:
        return ""
    cols = 4 if len(frames) > 4 else len(frames)
    rows = (len(frames) + cols - 1) // cols
    inputs = sum((["-i", f] for f in frames), [])
    filt = "".join(f"[{i}:v]scale=270:-2,pad=270:480:(ow-iw)/2:(oh-ih)/2:black[v{i}];" for i in range(len(frames)))
    if len(frames) == 1:
        filt += "[v0]copy[g]"
    else:
        layout = "|".join(f"{(i % cols) * 270}_{(i // cols) * 480}" for i in range(len(frames)))
        filt += "".join(f"[v{i}]" for i in range(len(frames))) + f"xstack=inputs={len(frames)}:layout={layout}:fill=black[g]"
    r = _run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", filt, "-map", "[g]",
              "-frames:v", "1", str(out)])
    return str(out) if out.exists() and r.returncode == 0 else ""


def main():
    ap = argparse.ArgumentParser(description="视频文案解读素材准备")
    ap.add_argument("src", help="抖音分享文本/链接, 或本地视频文件")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--frames", type=int, default=MAX_FRAMES)
    ap.add_argument("--no-asr", action="store_true")
    ap.add_argument("--keep-video", action="store_true", help="允许下载的作品保留视频文件")
    a = ap.parse_args()
    n = max(1, min(a.frames, MAX_FRAMES))

    local = Path(a.src)
    info, temp_video = {}, False
    try:
        if local.exists():
            video = local.resolve()
            base = Path(a.out) if a.out else video.parent / f"{video.stem}_analysis"
            info = {"id": video.stem, "type": "video", "desc": "", "author": "", "source": str(video)}
        else:
            info = douyin.get_info(a.src)
            base = Path(a.out) if a.out else douyin.WORKSPACE / "douyin" / info["id"] / "analysis"
            base.mkdir(parents=True, exist_ok=True)
            if info["type"] == "images":
                video = None
            else:
                video = base / f"_src_{info['id']}.mp4"
                douyin.fetch_video(info, video)
                temp_video = not (info["allow_download"] and a.keep_video)
    except douyin.DouyinError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2
    base.mkdir(parents=True, exist_ok=True)

    result = {"ok": True, "info": {k: info.get(k) for k in ("id", "type", "desc", "author", "create_time",
                                                            "duration_s", "music", "stats", "hashtags",
                                                            "allow_download", "download_block_reason", "aigc",
                                                            "share_url", "source") if k in info},
              "dir": str(base)}
    if video is None:  # 图集: 原图即素材
        frames = []
        s = douyin._session()
        (base / "frames").mkdir(exist_ok=True)
        for i, im in enumerate(info["images"][:n], 1):
            for u in im["urls"]:
                p = base / "frames" / f"img_{i:02d}.jpg"
                try:
                    douyin._download(u, p, s, "image")
                    frames.append(str(p))
                    break
                except Exception:
                    continue
        result.update(duration_s=0, transcript={"backend": "none", "text": "", "note": "图集作品, 无口播"})
    else:
        meta = douyin.probe(video)
        dur = meta.get("duration_s") or info.get("duration_s") or 0
        result["media"] = meta
        if a.no_asr or not meta.get("audio_codec"):
            tr = {"backend": "none", "text": "", "segments": [], "note": "跳过 ASR" if a.no_asr else "视频无音轨"}
        else:
            tr = asr.transcribe(str(video))
        (base / "transcript.txt").write_text(tr.get("text", ""), encoding="utf-8")
        result["transcript"] = tr
        frames_dir = base / "frames"
        for old in frames_dir.glob("f*.jpg"):   # 清掉上次分析的帧, 免得混进来
            old.unlink()
        times = scene_times(video, dur, n)
        frames = extract_frames(video, frames_dir, times)
        result["duration_s"] = dur
        if temp_video:
            video.unlink(missing_ok=True)
            result["video_kept"] = False
        else:
            result["video_kept"] = str(video)
    result["frames"] = frames
    if video is not None:
        result["frame_times"] = {Path(f).name: round(t, 2) for f, t in zip(frames, times)}
    result["grid"] = make_grid(frames, base / "grid.jpg")
    has_text = bool(result["transcript"].get("text"))
    result["next_steps"] = [
        f"vision_ocr(paths={json.dumps(frames, ensure_ascii=False)}, prompt=\"按顺序是第1~{len(frames)}张关键帧。"
        "逐张: 1) 抄出画面上的全部字幕/大字/贴纸文字 (原文); 2) 一句话描述画面 (人物/场景/镜头/情绪)。"
        "最后指出哪几张字幕是口播字幕、哪些是标题花字。\")",
        "口播全文看 transcript.txt" if has_text else "没有口播转写: 以画面字幕 OCR + 作品描述 + 背景音乐推断文案",
        "按 SKILL.md「解读模板」输出: 标题/口播全文/结构拆解/爆点分析/可复用模板",
    ]
    (base / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
