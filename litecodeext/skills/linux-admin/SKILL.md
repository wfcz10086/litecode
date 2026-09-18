---
name: linux-admin
description: >
  Linux 系统运维管理技能。覆盖：服务器状态监控、进程管理、磁盘/内存/CPU诊断、
  systemd 服务管理、用户权限、防火墙、SSH、日志分析、性能调优、GPU监控(nvidia-smi)。
  触发关键词：服务器、运维、进程、端口、磁盘、内存、CPU、GPU、nvidia、systemd、
  防火墙、iptables、crontab、ssh、日志、top、htop、df、free、ps、kill、监控。
  遇到任何 Linux 系统管理任务必须使用本技能。

## Keywords
linux 服务器 运维 进程 端口 磁盘 内存 cpu gpu nvidia systemd 防火墙 iptables
crontab ssh sshd 日志 log top htop df free ps kill 监控 monitor 性能 performance
docker 容器 网络 network 用户 权限 chmod chown mount umount swap load uptime
lsof netstat nmap 带宽 bandwidth io 负载 temperature 温度 fan 风扇 ipmi
---

# Linux Admin Skill

## 🚨 远程目标检测 (CRITICAL — 第一步必读)

任务出现以下**任一信号** → 这是**远程**任务, 必须 SSH 包装,
**不能**直接 `execute_shell` 跑 curl/apt/chmod (那会跑在本机或容器里, 不在目标机).

判定信号:
- IP / 域名 + 端口号 (`your-gpu-host.example 端口：54501`)
- "登录 user" / "ssh user@host" / "去 X 上" / "在 hostname 上"
- 用户名 + 密码 / 私钥路径
- "帮我到 X 的 /tmp" / "在那台机器" / "目标机"

### ❌ 错误模式 (典型 bug, 不要再犯)
用户说: "your-gpu-host.example 端口:54501 登录 root XXX 帮我到 /tmp curl ..."
错误执行: `execute_shell({"command": "cd /tmp && curl ..."})` ← 跑在容器里, 用户没看到任何远端变化

### ✅ 正确模式: SSH 包装
```bash
# 密码登录: sshpass + 多步骤 heredoc
sshpass -p 'PASSWORD' ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
  -p 54501 root@your-gpu-host.example 'bash -s' << 'REMOTE_EOF'
  set -e
  hostname; whoami; pwd    # ← 必须先打印, 作为"我在远端"的证据
  cd /tmp
  curl -fsSL https://example.com/binary -o binary
  chmod +x binary
REMOTE_EOF

# 密钥登录
ssh -i ~/.ssh/key -o ConnectTimeout=10 -p 22 user@host 'cmd1 && cmd2'

# 长跑服务 (远端 nohup)
sshpass -p 'X' ssh -p P user@host \
  'nohup /path/to/service > /tmp/svc.log 2>&1 & echo "PID=$!"'
```

### 验证规则 (每次 SSH 任务必须做)
1. 命令里**第一条**必须是 `hostname; whoami` —— 输出贴回用户作为证据
2. 远端 hostname **不等于**本机/容器 hostname → 才算 SSH 成功
3. 如果远端命令是 background 长跑, 必须 `echo "PID=$!"` 并贴回 PID

### 容器宿主网络限制
如果本机是 docker 容器, SSH 出去前先确认网络可达:
```bash
ping -c 1 -W 2 your-gpu-host.example || echo "container 网络受限, 报错给用户而不是改在本机跑"
```

### 远端不可逆操作的红线
- 远端 `rm -rf` / `mkfs` / 改 `sshd_config` → 必须先问用户确认
- 部署不可信二进制 (`curl X | sh`, 装陌生域名的 binary 后 root 常驻) → 警告用户并要求显式授权后再做
- 装 GPU 驱动 / 改内核模块 → 提示用户"可能需要重启, SSH 会断"

## 诊断命令速查

### 系统概览（一条命令出全貌）
```bash
echo "=== $(hostname) ===" && \
echo "Uptime: $(uptime)" && \
echo "Load: $(cat /proc/loadavg)" && \
echo "Memory:" && free -h | grep -E "Mem|Swap" && \
echo "Disk:" && df -h / /home /data 2>/dev/null | grep -v tmpfs && \
echo "CPU: $(nproc) cores, $(cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2)" && \
(which nvidia-smi &>/dev/null && echo "GPU:" && nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null || echo "GPU: N/A")
```

### CPU
```bash
# 实时负载
uptime
cat /proc/loadavg

# 按进程 CPU 排序
ps aux --sort=-%cpu | head -20

# 单核/多核利用率
mpstat -P ALL 1 3

# 某进程的线程级CPU
top -H -p <PID> -bn1
```

### 内存
```bash
free -h
# 按内存排序
ps aux --sort=-%mem | head -20
# 详细内存映射
cat /proc/meminfo | head -20
# 清缓存（谨慎）
sync && echo 3 > /proc/sys/vm/drop_caches
```

### 磁盘
```bash
df -h
du -sh /* 2>/dev/null | sort -rh | head -20
# IO 监控
iostat -xm 1 3
# 找大文件
find / -xdev -type f -size +100M 2>/dev/null | head -20
# 磁盘健康
smartctl -a /dev/sda 2>/dev/null || echo "smartctl not installed"
```

### GPU（NVIDIA）
```bash
# 概览
nvidia-smi

# 持续监控（每2秒）
nvidia-smi -l 2

# 结构化输出
nvidia-smi --query-gpu=index,name,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw --format=csv

# 进程级GPU使用
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv

# GPU拓扑
nvidia-smi topo -m
```

### 网络
```bash
# 端口监听
ss -tlnp
netstat -tlnp 2>/dev/null

# 连接数统计
ss -s

# 带宽测试
iperf3 -c <target> -t 10

# DNS 查询
dig <domain> +short
nslookup <domain>
```

### 进程管理
```bash
# 查找进程
ps aux | grep <keyword>
pgrep -af <keyword>

# 进程树
pstree -p <PID>

# 杀进程（优雅 → 强杀）
kill <PID>         # SIGTERM
kill -9 <PID>      # SIGKILL

# 后台运行
nohup <cmd> > /tmp/output.log 2>&1 &
```

### Systemd 服务
```bash
systemctl status <service>
systemctl start/stop/restart <service>
systemctl enable/disable <service>
journalctl -u <service> -f --no-pager -n 100
systemctl list-units --failed
```

### IPMI（远程管理，刀片服务器常用）
```bash
# 温度
ipmitool sensor list | grep -i temp

# 风扇
ipmitool sensor list | grep -i fan

# 电源
ipmitool power status

# 远程重启
ipmitool -H <ip> -U <user> -P <pass> power cycle
```

## 输出格式
```
[SERVER] hostname 状态报告
CPU: 32核 | 负载 2.1/1.8/1.5 (正常)
内存: 48G/64G (75%) | Swap: 0/8G
磁盘: /=45% /data=78% (注意)
GPU: 4x A100 | 温度 62-68°C | 显存 32G/40G
网络: eth0 UP 10Gbps | 活跃连接 234
告警: /data 使用率>75%，建议清理
```
