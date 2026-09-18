---
name: service-lifecycle
description: >
  启动 / 停止 / 重启本地 web 服务的标准模式, 避免"反复 pkill + nohup + sleep"
  的低效循环。涵盖 FastAPI / Flask / Node / Python 服务。
  触发关键词: 启动服务 / 重启 / pkill / nohup / port / 端口占用 / systemctl /
  uvicorn / 后台进程 / 守护进程 / 服务挂了 / healthcheck。

## Keywords
启动服务 重启服务 pkill nohup port 端口 占用 uvicorn gunicorn
后台进程 守护进程 fastapi flask healthcheck health_url background
service lifecycle systemctl supervisord pm2
---

# 服务生命周期 Skill

## 黄金法则
**不要循环 `pkill -> nohup -> sleep 3 -> curl`。** 一次跑对就好。

## 启动服务的正确姿势

### 方案 A (推荐): `execute_shell` 的 `background=True + health_url`
```python
execute_shell(
    command="cd /tmp/crypto_monitor && python3 app.py",
    background=True,
    health_url="http://127.0.0.1:45555/health",   # 会轮询最长 30s
    keep_alive=True,                              # session 结束也不杀
)
```
返回 `Background ready pid=XXXX` → 就绪; 返回 `died after Ns` → 自带 log tail 诊断。

### 方案 B: 裸 nohup (旧方式, 不推荐)
```bash
cd /tmp/crypto_monitor && nohup python3 app.py > /tmp/svc.log 2>&1 &
```
- ❌ PID 丢失, 不知道死没死
- ❌ 要自己 `lsof -i:PORT` + `tail log`
- 仅当必须控制具体重定向时用

## 判断服务是否在跑
```bash
lsof -i:PORT | grep LISTEN   # 快, 带 PID
# 或
curl -sfo /dev/null http://127.0.0.1:PORT/health && echo UP || echo DOWN
```

## 重启服务 (更新代码后)
```bash
# 一条命令完成 stop + start, 不用 sleep
(lsof -ti:PORT | xargs -r kill -9) 2>/dev/null; sleep 1
execute_shell(command="python3 app.py", background=True,
              health_url="http://127.0.0.1:PORT/health")
```
或更强: 让 uvicorn 支持 `--reload` 开发时用 (生产用 gunicorn + `--preload`)。

## 等服务就绪 (轮询 + 健康检查)
**禁用 `sleep 60/120`**。用:
```bash
until curl -sfo /dev/null http://127.0.0.1:PORT/health; do sleep 1; done
echo "ready"
```
或 Python 里:
```python
for _ in range(30):
    try:
        if requests.get("http://127.0.0.1:PORT/health", timeout=1).ok:
            break
    except: pass
    time.sleep(1)
```

## 端口占用排查
```bash
lsof -i:PORT        # 谁在听
lsof -ti:PORT       # 只拿 pid
ss -ltnp | grep :PORT   # 另一种
fuser -k PORT/tcp   # 粗暴 kill (慎用)
```

## 常见坑
1. **nohup 后 shell 退出, 子进程也死** — 必须 `nohup ... &` 配合 `disown` 或 `setsid`
2. **stdout 重定向覆盖问题** — `> log 2>&1` 重启会覆盖, 用 `>>` 追加
3. **uvicorn reload 模式在 background 下不稳** — 生产别开 `--reload`
4. **fastapi 启动报 ModuleNotFoundError** — 先 `python3 -m py_compile app.py` 确认语法, 再 `python3 -c "from app import app"` 确认可 import
5. **服务闪退无日志** — 裸 `python3 app.py` 在前台跑一次看错误, 不要依赖 bg 的 log tail
