#!/usr/bin/env python3
"""把 preview/*.png 拼成一张 3 列缩略图 preview/grid.png, 交给 vision_ocr 一次查完所有关键帧。
用法: python3 grid.py [png ...]   (默认 preview/t*.png 按时间排序)"""
import glob, re, sys

from PIL import Image

files = sys.argv[1:] or sorted(glob.glob("preview/t*.png"), key=lambda p: float(re.findall(r"t([\d.]+)\.png", p)[0]))
W, H, C = 270, 480, 3
rows = (len(files) + C - 1) // C
g = Image.new("RGB", (W * C, H * rows), "black")
for i, f in enumerate(files):
    g.paste(Image.open(f).resize((W, H)), ((i % C) * W, (i // C) * H))
g.save("preview/grid.png")
print("preview/grid.png", len(files), "frames:", [f.split("/")[-1] for f in files])
