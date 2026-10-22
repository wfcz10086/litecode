#!/usr/bin/env python3
"""本地视频 / 图片去水印 (ffmpeg delogo 插值涂抹)。抖音链接请直接用 douyin.py 取无水印原片, 别走这里。

步骤:
  1) 定位: python3 dewatermark.py probe in.mp4
     → 抽 4 帧 (10/40/70/95%) 叠坐标网格, 输出 probe_*.jpg; 用 vision_ocr 看水印在哪个格子, 读出 x,y,w,h
  2) 去除: python3 dewatermark.py run in.mp4 -o out.mp4 --box 20,40,220,90 [--box 300,1500,400,120@8.5-13]
     --box x,y,w,h            整段生效
     --box x,y,w,h@t0-t1      只在 t0~t1 秒生效 (抖音水印会在左上/右下之间跳, 分两段写)
     --preset douyin          竖屏抖音常见两处 (左上 + 右下) 的经验框, 按分辨率缩放; 先 probe 核对再用
     --trim-end 3             去掉片尾 N 秒 (抖音带水印下载会拼 2~3 秒片尾)
  图片同理: run in.jpg -o out.jpg --box ...

效果局限 (要跟用户说清):
- delogo 用周围像素插值, 纯色/渐变背景几乎看不出; 复杂纹理、人脸、文字上会留下模糊块。
- 半透明大水印、满屏平铺水印、动态跟随水印处理不了, 需要 AI 修复 (LaMa/ProPainter 类模型), 当前未内置。
- 只用于自己的或已获授权的素材; 不要去除「AI 生成」等法定标识 (《人工智能生成合成内容标识办法》禁止删除)。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _probe(p: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(p)],
                         capture_output=True, text=True).stdout
    d = json.loads(out or "{}")
    st = (d.get("streams") or [{}])[0]
    return {"w": st.get("width", 0), "h": st.get("height", 0),
            "dur": float((d.get("format") or {}).get("duration") or 0)}


def cmd_probe(src: Path, out_dir: Path):
    from PIL import Image, ImageDraw
    info = _probe(src)
    w, h = info["w"], info["h"]
    out_dir.mkdir(parents=True, exist_ok=True)
    times = [0] if src.suffix.lower() in IMG_EXT else [info["dur"] * r for r in (0.1, 0.4, 0.7, 0.95)]
    outs = []
    step = max(20, round(min(w, h) / 10 / 10) * 10)
    for i, t in enumerate(times, 1):
        raw = out_dir / f"_raw_{i}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", str(raw)])
        if not raw.exists():
            continue
        im = Image.open(raw).convert("RGB")
        dr = ImageDraw.Draw(im)
        for x in range(0, w, step):
            dr.line([(x, 0), (x, h)], fill=(255, 0, 0) if x % (step * 5) == 0 else (255, 255, 0), width=1)
            if x % (step * 2) == 0:
                dr.text((x + 2, 2), str(x), fill=(255, 0, 0))
        for y in range(0, h, step):
            dr.line([(0, y), (w, y)], fill=(255, 0, 0) if y % (step * 5) == 0 else (255, 255, 0), width=1)
            if y % (step * 2) == 0:
                dr.text((2, y + 2), str(y), fill=(255, 0, 0))
        p = out_dir / f"probe_{i}_{t:.1f}s.jpg"
        im.save(p, quality=88)
        raw.unlink()
        outs.append(str(p))
    strips = {}
    if src.suffix.lower() not in IMG_EXT and info["dur"] > 0:
        # 角落时间条: 左上/右下角各裁 36%x14%, 按固定间隔平铺, 用来看水印何时换位置
        fps = min(2.0, 40 / info["dur"])
        cw, ch = int(w * 0.36) // 2 * 2, int(h * 0.14) // 2 * 2
        n = int(info["dur"] * fps) + 1
        rows = (n + 7) // 8
        for name, cx, cy in (("tl", 0, 0), ("br", w - cw, h - ch)):
            p = out_dir / f"corner_{name}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                            f"fps={fps:.4f},crop={cw}:{ch}:{cx}:{cy},scale=180:-2,tile=8x{rows}",
                            "-frames:v", "1", str(p)])
            if p.exists():
                strips[name] = str(p.resolve())
        strips["seconds_per_cell"] = round(1 / fps, 3)
    print(json.dumps({"width": w, "height": h, "duration": info["dur"], "grid_step": step,
                      "frames": [str(Path(o).resolve()) for o in outs], "corner_strips": strips},
                     ensure_ascii=False, indent=2))
    if strips:
        print(f"corner_tl/br.jpg: 角落按时间平铺 (每格 {strips['seconds_per_cell']}s, 从左到右、从上到下, 第1格=0s), "
              "看第几格水印出现/消失, 换算成秒写进 --box ...@t0-t1。", file=sys.stderr)
    print(f"\n用 vision_ocr(paths=上面 frames, prompt='图上红黄网格每格 {step}px, 坐标标在左/上边缘。"
          "找出所有水印/台标/账号文字, 给出每处的 x,y,w,h 像素框(稍放大 10px 余量) 以及出现在哪几张') 读出框。",
          file=sys.stderr)


def douyin_preset(w: int, h: int, dur: float) -> list:
    """抖音竖屏水印经验位置 (1080x1920 基准): 左上 logo+抖音号, 右下抖音号; 后半段换位置。"""
    sx, sy = w / 1080, h / 1920
    tl = (int(15 * sx), int(30 * sy), int(360 * sx), int(225 * sy))
    br = (int(w - 400 * sx), int(h - 275 * sy), int(385 * sx), int(255 * sy))
    if dur <= 0:
        return [tl, br]
    sw = round(dur * 0.3, 2)   # 实测样片在 ~30% 处从左上跳到右下; 以 probe 的 corner 条为准
    return [(*tl, 0, sw), (*br, sw, dur)]


def parse_box(s: str):
    m = re.fullmatch(r"(\d+),(\d+),(\d+),(\d+)(?:@([\d.]+)-([\d.]+))?", s.strip())
    if not m:
        raise SystemExit(f"--box 格式不对: {s} (x,y,w,h 或 x,y,w,h@t0-t1)")
    x, y, w, h = map(int, m.groups()[:4])
    if m.group(5):
        return (x, y, w, h, float(m.group(5)), float(m.group(6)))
    return (x, y, w, h)


def build_filter(boxes, W, H) -> str:
    parts = []
    for b in boxes:
        x, y, w, h = b[:4]
        x, y = max(1, x), max(1, y)               # delogo 要求框不贴边
        w, h = min(w, W - x - 1), min(h, H - y - 1)
        if w <= 2 or h <= 2:
            continue
        f = f"delogo=x={x}:y={y}:w={w}:h={h}"
        if len(b) == 6:
            f += f":enable='between(t,{b[4]},{b[5]})'"
        parts.append(f)
    return ",".join(parts) or "null"


def cmd_run(src: Path, out: Path, boxes, preset: str, trim_end: float):
    info = _probe(src)
    W, H, dur = info["w"], info["h"], info["dur"]
    if preset == "douyin":
        boxes = list(boxes) + douyin_preset(W, H, dur if src.suffix.lower() not in IMG_EXT else 0)
    if not boxes:
        raise SystemExit("没给 --box 也没给 --preset; 先 probe 定位水印")
    vf = build_filter(boxes, W, H)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src)]
    if src.suffix.lower() in IMG_EXT:
        cmd += ["-vf", vf, "-frames:v", "1", "-update", "1", str(out)]
    else:
        if trim_end > 0 and dur > trim_end:
            cmd += ["-t", f"{dur - trim_end:.3f}"]
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "copy", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg 失败: {r.stderr[-400:]}")
    print(json.dumps({"ok": True, "output": str(out.resolve()), "filter": vf, "duration": _probe(out)["dur"]},
                     ensure_ascii=False, indent=2))
    print("请抽帧 (probe 输出文件) 用 vision_ocr 复核水印区域是否已抹掉、有无明显糊块。", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="本地视频/图片去水印 (ffmpeg delogo)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("probe")
    p1.add_argument("src")
    p1.add_argument("-o", "--out", default="")
    p2 = sub.add_parser("run")
    p2.add_argument("src")
    p2.add_argument("-o", "--out", default="")
    p2.add_argument("--box", action="append", default=[])
    p2.add_argument("--preset", choices=["douyin"], default="")
    p2.add_argument("--trim-end", type=float, default=0)
    a = ap.parse_args()
    src = Path(a.src)
    if not src.exists():
        raise SystemExit(f"文件不存在: {src}")
    if a.cmd == "probe":
        cmd_probe(src, Path(a.out) if a.out else src.parent / f"{src.stem}_wmprobe")
    else:
        out = Path(a.out) if a.out else src.with_name(f"{src.stem}_nowm{src.suffix}")
        cmd_run(src, out, [parse_box(b) for b in a.box], a.preset, a.trim_end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
