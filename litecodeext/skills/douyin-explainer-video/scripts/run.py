#!/usr/bin/env python3
"""一键流水线 (项目目录里跑, 建议 background=true):
  python3 run.py                     全流程: copy → fact → tts → check → render → bgm → mix → final → qa
  python3 run.py --from render       从某一步接着跑 (改完画面不用重新配音)
  python3 run.py --only check        只跑一步
任一关卡失败立即停, 退出码非 0, 日志里写明哪步、怎么修。
copy (文案闸)、fact (事实闸) 和 check (版面自检) 是硬闸: 不过不往下走。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = [
    ("copy",   ["stylecheck.py"],       "文案闸: 风格 / 深度 / 废话 / 不把干货推到下期"),
    ("fact",   ["factcheck.py"],        "旁白年份/次数/金额对账 facts + 画面数字对旁白 (numcheck)"),
    ("tts",    ["tts.py"],              "配音 + timeline.json"),
    ("check",  ["render.py", "check"],  "版面自检: 文字重叠/出屏"),
    ("render", ["render.py", "all"],    "逐帧渲染 (最慢)"),
    ("bgm",    ["bgm.py"],              "配乐"),
    ("mix",    ["merge.py", "mix"],     "旁白 + 配乐混音"),
    ("final",  ["merge.py", "final"],   "编码 final.mp4"),
    ("qa",     ["qa_final.py"],         "成片规格 + 抽帧拼图"),
]
NAMES = [s[0] for s in STEPS]


def main():
    args = sys.argv[1:]
    start, only = 0, None
    if "--from" in args:
        start = NAMES.index(args[args.index("--from") + 1])
    if "--only" in args:
        only = args[args.index("--only") + 1]
        assert only in NAMES, f"步骤只能是 {NAMES}"
    for name, cmd, desc in STEPS[start:]:
        if only and name != only:
            continue
        t0 = time.time()
        print(f"\n== [{name}] {desc}", flush=True)
        r = subprocess.run([sys.executable, os.path.join(HERE, cmd[0]), *cmd[1:]])
        if r.returncode != 0:
            print(f"\n✗ 停在 [{name}] (退出码 {r.returncode})。修好后: python3 {__file__} --from {name}", flush=True)
            print(f"RUN-END fail step={name} code={r.returncode}", flush=True)
            sys.exit(r.returncode)
        print(f"   ✓ {name} {time.time() - t0:.0f}s", flush=True)
    if not only:
        print("\n全部完成。最后一步: vision_ocr 查 qa/final_grid.png, 有问题改完 --from render 重来。")
    # 固定结束行: 等它的人 `until grep -q '^RUN-END' run.log` 一条就够 (原来成功/失败的结束行各不相同,
    # 模型猜关键词猜错 —— "== (qa|全部完成)" 匹配不上 "== [qa]" —— 白等 420s / 600s)
    print("RUN-END ok", flush=True)


if __name__ == "__main__":
    main()
