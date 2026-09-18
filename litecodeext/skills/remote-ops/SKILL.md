---
name: remote-ops
description: >
  远程主机运维通用技能。覆盖: 远程 SSH 命令执行、远程 Docker 容器生命周期管理、
  推理服务/Web 服务/数据库运维、系统状态诊断、日志抓取、GPU/显卡检查、
  参数热改重启、跨主机批量操作。任何"远程/SSH/目标机/在 XXX 上/docker 容器 (远程) /
  vllm/推理服务" 类任务都要用本技能。触发关键词: 远程、ssh、remote、目标机、
  docker、container、vllm、qwen、推理、inference、显存、gpu、nvidia-smi、
  重启、recreate、参数、上下文长度、max-model-len、tensor-parallel。

## Keywords
remote 远程 ssh sshpass 目标机 target-host paramiko
docker container 容器 recreate restart 重建 重启 inspect logs
vllm qwen inference 推理 max-model-len context-length 上下文
tensor-parallel gpu 显存 nvidia-smi rtx cuda oom
your-gpu-host.example 远程主机 服务器 host

---

# Remote Ops Skill (通用远程运维)

## 🚨 铁律 · 不许绕开 LiteCode 走本机 sshpass

**只能用**: `ssh_exec` / `docker_remote_ps` / `docker_remote_inspect` / `docker_remote_recreate`
**禁止**: 本机 `sshpass -p '...' ssh ...` / `paramiko` 手写 / 让人类手动 SSH

理由:
- LiteCode 的价值就在编排 + dogfood, 绕开等于否定项目本身
- 走 LiteCode 工具, 每一步会被 SSE + memory + trace 记下来, 可复用/可审计
- 凭据集中管理 (走本地 config / 已知主机注册表), 不散落到 shell history

## 三步工作法 (对每个远程任务都要做)

### Step 1 · 思考 (self_reflect 或内部思考)
在调用任何 ssh_exec 前, 先花一步梳理:
- 目标是什么? (查状态 / 改配置 / 重建 / 排错)
- 需要哪些前置信息? (原容器 image / mounts / cmd / env)
- 会不会有破坏性操作? (stop+rm+run 是破坏性, 必须先拿到全部现状再动)
- 验证成功的信号是什么? (log 里的 "Application startup complete" / curl 200 / nvidia-smi 显存)

调 `self_reflect(question="要做 X, 我需要先..., 然后..., 验证信号是...")` 或在内部 thinking 段完成。

### Step 2 · 工具调用 (先 inspect 再改)
- **查/状态类**: `ssh_exec(cmd="...")` / `docker_remote_ps(...)`
- **要改容器参数**: 先 `docker_remote_inspect(container="...")` 拿到 Config.Cmd + Config.Entrypoint + HostConfig.Binds + PortBindings + RestartPolicy + ShmSize + DeviceRequests, 然后 `docker_remote_recreate(...)` 一次到位
- **要装包/改文件/查日志**: `ssh_exec(cmd="...", timeout=120)`

**破坏性操作前**先做一次 dry-inspect, 拿全信息再操作。

### Step 3 · 验证 + 写记忆
- 用 `ssh_exec` 复查目标状态 (`docker logs ... | grep ...` / `curl ...` / `docker ps --filter name=X`)
- 关键结果写进 session memory: `save_memory(section="Remote Ops Log", content="2026-XX-XX 在 <host> 把 <container> 的 <param> 从 A 改成 B, 验证通过: <signal>")`
- 如果发现了新的"已知主机"或新的运维套路, 用 `create_skill` 加个子 skill 供后续复用

## 已注册远程主机 (查询用)

| alias | host | port | user | 说明 |
|---|---|---|---|---|
| my-gpu-box | (你的主机 IP) | 22 | root | 示例行:GPU 服务器,跑 vllm |

凭据(密码/密钥)通过 `ssh_exec` 等工具的参数传入,或存进 session memory,**不要硬编码在本文档**。新主机往表里追加。

## docker_remote_recreate 参数拼接模板

```
docker_remote_recreate(
  host=..., port=..., user=..., password=...,
  container=<容器名>,
  image=<12位 short-id 或 name:tag>,
  run_flags="-d --name <name> --restart <policy> --gpus all --shm-size=<X>g -p <hp>:<cp> -v <host>:<container> [-e KEY=VAL ...]",
  entrypoint=<单值, 有空格要拆到 cmd>,
  cmd=<image 后面的完整 args, 空格分隔>
)
```

**坑1**: `--entrypoint` 只吃单值。原 entrypoint 是 `[vllm serve]` → 拆成 `entrypoint="vllm"` + `cmd="serve ..."`。
**坑2**: image 传 `sha256:...` 全串也行, 建议截 12 位。
**坑3**: `--restart` / `--gpus` / `--shm-size` / `-p` / `-v` / `-e` 都塞进 `run_flags`。
**坑4**: 改参数后 `docker restart` **不生效** (只热重启进程, 不换 args), 必须走 recreate = stop+rm+run。

## 常见任务范式

### 范式 A · 修改 vLLM 推理参数
```
1. docker_remote_inspect(container="vllm-v4")
   → 提取现 Cmd/Entrypoint/Image/Binds/Ports
2. 拷贝 cmd, 把要改的参数替换 (如 --max-model-len 32768 → 30000)
3. docker_remote_recreate(...)
4. ssh_exec(cmd="sleep 25 && docker logs vllm-v4 2>&1 | grep -E 'max_model_len|Application startup|error' | tail -8")
5. ssh_exec(cmd="curl -s http://127.0.0.1:8000/v1/models")
6. save_memory(section="Remote Ops Log", content="...")
```

### 范式 B · 排查远程服务问题
```
1. docker_remote_ps(all=True) 看容器状态
2. ssh_exec(cmd="docker logs <name> --tail 200 2>&1")
3. ssh_exec(cmd="nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv")
4. ssh_exec(cmd="df -h /data")
5. self_reflect(question="根据以上信号定位根因") 后再动手修
```

### 范式 C · 批量在多台远程主机跑同一个命令
遍历已注册主机列表, 对每台调 `ssh_exec(host=X.host, ..., cmd=<same>)`。结果聚合后 save_memory。

## 出错处理

- SSH 超时: 检查 host/port; `ssh_exec(cmd="echo alive", timeout=10)` 快速探活
- docker 命令 rc≠0: 看 stderr 前 500 字, 常见是 image 名写错 / port 已占用 / mount 路径不存在
- vLLM CUDA OOM: `--gpu-memory-utilization` 降到 0.85, 或 `--max-model-len` 降低
- vLLM `max_model_len > max_position_embeddings` 报错: 加 `--rope-scaling '{"type":"dynamic","factor":2}'`
- paramiko 不在容器里: `docker exec litecode pip install paramiko` (会被 restart 冲掉, 后续要固化进 Dockerfile)

## 与 LiteCode 系统的联动

- 改完远程服务参数, 如果 `/opt/litecode/config.json` 里有对应的 model 条目 (如 vllm-qwen3-30b-remote 的 context_window), 也要同步改, 保持一致
- 已注册主机表更新 → 更新本 skill 的表格 → 让后续会话看到
- 每次成功的远程运维要 save_memory, 累积成运维知识库
