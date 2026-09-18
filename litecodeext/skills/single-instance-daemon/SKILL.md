---
name: single-instance-daemon
description: >
  在容器/主机上启动"只能有一份"的守护进程: 健康检查 / 定时器 / 监听服务。
  用 pidfile + flock 双保险防止重复起。也说清楚 "Docker 容器里为什么 cron 不可用"
  和正确的替代方案 (supervisord / 前台循环 / entrypoint 注入)。
  触发关键词: daemon / 守护进程 / 定时任务 / cron / crontab / pidfile /
  flock / 单例 / single instance / restart on boot / 容器启动。

## Keywords
daemon 守护进程 定时任务 cron crontab pidfile flock 单例
single instance restart boot 容器启动 entrypoint supervisord systemd
---

# 单例守护进程 Skill

## 问题模式 (要避免的)

```bash
# ❌ 每次都起新实例, 导致 5 分钟后有 10 个 health_daemon 在跑
nohup python3 health_daemon.py &
```

```bash
# ❌ Docker 容器里想用 cron
crontab -e       # → command not found
systemctl ...    # → 容器里没 init
```

## 单例保护: pidfile + flock

### Python 版本
```python
#!/usr/bin/env python3
"""health_daemon.py — 单例健康检查"""
import fcntl, os, sys, time, atexit
from pathlib import Path

_PID_FILE = Path("/tmp/health_daemon.pid")
_LOCK_FILE = Path("/tmp/health_daemon.lock")

def acquire_singleton() -> None:
    """抢 flock, 没抢到就退出, 不打扰已有实例"""
    fh = open(_LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[daemon] 已有实例运行, 本次退出 (lock={_LOCK_FILE})")
        sys.exit(0)
    # 写 pidfile + 清理
    _PID_FILE.write_text(str(os.getpid()))
    atexit.register(lambda: _PID_FILE.unlink(missing_ok=True))
    # 保持 fh 全局引用, 进程退出锁才释放
    globals()["_LOCK_FH"] = fh

def main_loop():
    while True:
        # 健康检查 / 业务逻辑
        time.sleep(300)

if __name__ == "__main__":
    acquire_singleton()
    main_loop()
```

### Shell 版本
```bash
#!/bin/bash
# 守护进程的启动脚本
set -e
LOCK=/tmp/my_daemon.lock

# flock -n: 抢不到立即失败; 抢到持有直到脚本结束
exec 200>$LOCK
flock -n 200 || { echo "已在运行"; exit 0; }

python3 /path/to/daemon.py
```

## 在容器里定时任务怎么做

Docker 容器是**进程级隔离**, 没 init, 所以:

- ❌ `cron` / `crontab` / `systemctl` / `service` 都不可用
- ❌ 宿主机 `crontab` 配到容器内也不优雅 (路径 / 环境变量不对)

正确方案:

### A. 进程内自循环 (推荐, 最简单)
```python
# daemon 自己带一个 while + sleep, 不靠系统调度
while True:
    do_work()
    time.sleep(INTERVAL_SECONDS)
```
配合单例保护, 容器启动时启动一次即可。

### B. supervisord (多进程管理)
Dockerfile 里装 supervisord, 用它拉起多个长跑进程:
```ini
# /etc/supervisor/conf.d/app.conf
[program:health_daemon]
command=/usr/bin/python3 /app/health_daemon.py
autostart=true
autorestart=true
```

### C. 接入容器 entrypoint (一次性启动项)
修改 `entrypoint.sh`, 在容器启动时把 daemon 一并拉起:
```bash
#!/bin/bash
# entrypoint.sh
python3 /app/health_daemon.py &   # 后台跑
exec python3 /app/main.py         # 前台主服务, PID 1
```
重启容器 = daemon 自动重启。

### D. 外部调度器 (GitHub Actions / k8s CronJob / Airflow)
容器只做"做一次就退出"的短任务, 由外部触发。

## Agent 写容器定时任务时的检查清单

1. `which crontab` / `which systemctl` — 确认不可用 (大概率都没)
2. 不要写 `crontab -l | { cat; echo ...; } | crontab -` 假装成功
3. 明确告诉用户: 容器里我用 **进程内循环 + entrypoint 拉起** (方案 A + C)
4. 写 `health_daemon.py` 时必带 `acquire_singleton()`
5. 改 `entrypoint.sh` 注入启动: `python3 health_daemon.py &`
6. 文档 `README.md` 里说清楚: "重启容器即可重启 daemon, 不依赖 cron"

## 查看 / 清理残留

```bash
# 查看所有同名实例
pgrep -fa health_daemon.py

# 清理所有旧实例 (保留最新的)
pgrep -f health_daemon.py | sort -rn | tail -n +2 | xargs -r kill

# 或直接干光重起
pkill -f health_daemon.py; sleep 1
python3 health_daemon.py &
```
