#!/usr/bin/env python3
"""判断 LiteCode 现在有没有正在跑的对话回合 —— 重启容器前必须用它, 别再靠"最近几分钟有没有 iter= 日志"。

2026-10-19 事故: 27B 模型想一轮要 3 分半, 期间没有 iter= 日志, 按"3 分钟内无 iter=" 判成空闲就重启了,
把正在看图的那一轮打断 ("连接中断")。

判断法: 看最近 6 小时容器日志, 每个会话最后一条事件是 `>>>`/`iter=`/工具执行 而之后没有 `[TURN-END]` → 还在跑。
超过 45 分钟没有任何动静的会话视为已挂掉, 不算活跃 (回合异常也会打 TURN-END, 一般到不了这条)。

用法: python3 scripts/litecode_idle.py            空闲退出码 0, 有活跃回合退出码 1 并列出
      python3 scripts/litecode_idle.py --wait     一直等到空闲 (每 20 秒查一次)
"""
import calendar
import re
import subprocess
import sys
import time

STALE = 45 * 60
TAG = re.compile(r"\[((?:web|wx|wecom|cli|dag|swe|windows|qb)-[\w-]+|anon)\]")   # 会话名一定带 "-"; 原来把 [web_search] 也当成会话
START = re.compile(r">>> |iter=\d+/|pre-tool|⚙ |heavy tools")
END = re.compile(r"\[TURN-END\]|<<< ")


def active() -> dict:
    out = subprocess.run(["docker", "logs", "--since", "6h", "--timestamps", "litecode"],
                         capture_output=True, text=True, errors="replace")
    last = {}                                  # sid -> (时间戳, 是否结束)
    cur = None
    for line in (out.stdout + out.stderr).splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if "所有服务已启动" in line or "启动 LiteCode Server" in line:
            last.clear()                       # 容器重启过, 之前的回合都已不在
            continue
        m = TAG.search(line)
        ts = line[:19]                         # docker --timestamps: 2026-10-19T11:04:26.123Z (UTC)
        if m:
            cur = m.group(1)
        elif "⚙ " not in line:                 # 工具执行行不带会话标签, 归到最近出现的会话; 其余无标签行跳过
            continue
        if cur is None:
            continue
        if END.search(line):
            last[cur] = (ts, True)
            # 结束后的无标签工具行属于别的会话, 不再算到它头上
            # (2026-10-21: web-xxxx 已 TURN-END, 并行会话的 "⚙ " 行被错记给它, 误判在跑 20 分钟)
            cur = None
        elif START.search(line):
            last[cur] = (ts, False)
    now = time.time()
    res = {}
    for sid, (ts, ended) in last.items():
        if ended:
            continue
        try:
            t = calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            t = now
        age = now - t
        if age < STALE:
            res[sid] = int(age)
    return res


def main():
    wait = "--wait" in sys.argv
    while True:
        a = active()
        if not a:
            print("空闲: 没有正在跑的回合")
            return 0
        print("有回合在跑: " + ", ".join(f"{s} (最后动静 {age}s 前)" for s, age in a.items()), flush=True)
        if not wait:
            return 1
        time.sleep(20)


if __name__ == "__main__":
    sys.exit(main())
