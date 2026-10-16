#!/usr/bin/env python3
"""成片终检: final.mp4 规格核对 + 每场抽 3 帧 (30% / 60% / 90%) 拼成 qa/final_grid.png 给 vision_ocr 复查。
用法 (项目目录): python3 qa_final.py [final.mp4]     规格不对 → 非 0 退出

为什么要查成片而不是只查预览帧: 预览帧只截每场中点, 后面再改 engine.js 引入的遮挡、
只在某一瞬间出现的重叠都看不到 (2026-10 原片终章金句被竖排大字压住就是这么漏的)。
"""
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw

W, H = 270, 480   # 缩略图


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "format=duration:stream=codec_type,width,height", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def frame(path, t, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                    "-vf", f"scale={W}:{H}", out], check=True)


def main():
    mp4 = sys.argv[1] if len(sys.argv) > 1 else "final.mp4"
    tl = json.load(open("timeline.json", encoding="utf-8"))
    info = probe(mp4)
    streams = {s["codec_type"]: s for s in info["streams"]}
    dur = float(info["format"]["duration"])
    errs = []
    v = streams.get("video", {})
    if (v.get("width"), v.get("height")) != (1080, 1920):
        errs.append(f"分辨率 {v.get('width')}x{v.get('height')}, 应为 1080x1920")
    if "audio" not in streams:
        errs.append("没有音轨")
    if abs(dur - tl["total"]) > 1.5:
        errs.append(f"时长 {dur:.1f}s 跟时间轴 {tl['total']:.1f}s 对不上")

    os.makedirs("qa", exist_ok=True)
    rows = len(tl["scenes"])
    grid = Image.new("RGB", (W * 3 + 120, H * rows), "black")
    d = ImageDraw.Draw(grid)
    for i, s in enumerate(tl["scenes"]):
        d.text((8, i * H + 8), s["id"], fill="white")
        for j, f in enumerate((.3, .6, .9)):
            t = min(s["start"] + s["dur"] * f, dur - .05)
            p = f"qa/{s['id']}_{int(f * 100)}.png"
            frame(mp4, t, p)
            grid.paste(Image.open(p), (120 + j * W, i * H))
    grid.save("qa/final_grid.png")
    print(f"final.mp4 {dur:.1f}s {v.get('width')}x{v.get('height')} audio={'audio' in streams}")
    print("qa/final_grid.png: 每行一场 (30% / 60% / 90%)。下一步 vision_ocr 逐行查:")
    print("  文字互相压住 / 被图形挡住 / 出屏 / 乱码 / 字幕看不清 / 画面大片空白。有问题 → 改 → 重渲 → 再跑本脚本")
    for e in errs:
        print("  ✗", e)
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
