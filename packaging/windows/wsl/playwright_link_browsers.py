#!/usr/bin/env python3
"""pip 装的 playwright 要的浏览器版本号 (browsers.json 里的 revision) 和 vendor 缓存里的对不上时,
把缺的版本目录软链到缓存里已有的同类浏览器, 免得默认 chromium.launch() 报 "Executable doesn't exist"。
(2026-10-19: playwright 1.60 要 1223, 镜像里 vendor 缓存是 1234 → html_render / 浏览器服务全起不来)
幂等, 镜像构建和容器启动都可以跑。"""
import json, os, re, sys
from pathlib import Path

import playwright

root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "~/.cache/ms-playwright")).expanduser()
spec = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
for b in json.loads(spec.read_text())["browsers"]:
    name = b["name"].replace("-", "_")
    if name not in ("chromium", "chromium_headless_shell"):
        continue
    want = root / f"{name}-{b['revision']}"
    if want.exists():
        continue
    have = sorted((p for p in root.glob(f"{name}-*") if re.fullmatch(rf"{name}-\d+", p.name) and not p.is_symlink()),
                  key=lambda p: int(p.name.rsplit("-", 1)[1]))
    if not have:
        print(f"[playwright] 缺 {want.name}, 缓存里也没有 {name}, 跳过", file=sys.stderr)
        continue
    want.symlink_to(have[-1].name)
    print(f"[playwright] {want.name} -> {have[-1].name}")
