---
name: automation
description: >
  任务自动化与定时调度技能。覆盖：Cron定时任务、Systemd Timer、
  Shell脚本编排、文件监控(inotify)、批量操作、自动备份、
  健康检查自动重启、报警通知(邮件/webhook)、CI/CD流水线。
  触发关键词：自动化、定时、cron、crontab、systemd timer、
  计划任务、脚本、批量、自动备份、监控、告警、webhook、
  开机自启、守护进程、supervisor、定期执行、每天、每小时。
  遇到任何自动化调度任务必须使用本技能。

## Keywords
自动化 automation 定时 cron crontab systemd timer 计划任务 schedule
脚本 script 批量 batch 自动备份 backup 监控 monitor 告警 alert
webhook 通知 notification 邮件 email 开机自启 daemon 守护进程
supervisor 定期 every hour daily weekly 每天 每小时 每周
inotify watch 文件监控 热重载 restart 自愈 healthcheck
---

# Automation Skill

## Cron 定时任务

### 语法速查
```
* * * * * command
│ │ │ │ └── 星期 (0-7, 0和7都是周日)
│ │ │ └──── 月份 (1-12)
│ │ └────── 日期 (1-31)
│ └──────── 小时 (0-23)
└────────── 分钟 (0-59)
```

### 常用示例
```bash
# 编辑当前用户的 crontab
crontab -e

# 每5分钟执行
*/5 * * * * /path/to/script.sh >> /var/log/task.log 2>&1

# 每天凌晨3点备份
0 3 * * * /opt/scripts/backup.sh >> /var/log/backup.log 2>&1

# 每小时整点执行
0 * * * * /opt/scripts/check.sh

# 每周一早上9点
0 9 * * 1 /opt/scripts/weekly_report.sh

# 每月1号
0 0 1 * * /opt/scripts/monthly_cleanup.sh

# 查看已设置的任务
crontab -l
```

## 自动备份脚本模板
```bash
#!/bin/bash
# backup.sh — 通用备份脚本
set -euo pipefail

BACKUP_DIR="/data/backups"
DATE=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=7

mkdir -p "$BACKUP_DIR"

# 备份数据库
if [ -f "/data/app.db" ]; then
    sqlite3 /data/app.db ".backup ${BACKUP_DIR}/app_${DATE}.db"
    echo "[$(date)] DB backup: app_${DATE}.db"
fi

# 备份配置
tar czf "${BACKUP_DIR}/config_${DATE}.tar.gz" /etc/nginx/ /opt/app/config/ 2>/dev/null || true

# 清理旧备份
find "$BACKUP_DIR" -name "*.db" -mtime +${KEEP_DAYS} -delete
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +${KEEP_DAYS} -delete

echo "[$(date)] Backup done. Files in ${BACKUP_DIR}:"
ls -lh "$BACKUP_DIR" | tail -10
```

## 健康检查 + 自动重启
```bash
#!/bin/bash
# health_check.sh — 服务存活检查，挂了自动拉起
SERVICE_URL="http://localhost:8080/health"
SERVICE_CMD="cd /opt/app && python3 main.py"
LOG="/var/log/health_check.log"

status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$SERVICE_URL" 2>/dev/null || echo "000")

if [ "$status" != "200" ]; then
    echo "[$(date)] Service DOWN (HTTP $status), restarting..." >> "$LOG"
    pkill -f "main.py" 2>/dev/null || true
    sleep 2
    nohup $SERVICE_CMD >> /var/log/app.log 2>&1 &
    echo "[$(date)] Service restarted, PID: $!" >> "$LOG"
else
    echo "[$(date)] OK" >> "$LOG"
fi

# Cron: */2 * * * * /opt/scripts/health_check.sh
```

## Webhook 通知
```bash
# 企业微信机器人
send_wechat() {
    curl -s -X POST "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY" \
         -H "Content-Type: application/json" \
         -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$1\"}}"
}

# 钉钉机器人
send_dingtalk() {
    curl -s -X POST "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN" \
         -H "Content-Type: application/json" \
         -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"$1\"}}"
}

# 使用: send_wechat "服务异常告警: API 返回 500"
```

## Systemd Service（开机自启）
```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=My Application
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/app
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
```bash
systemctl daemon-reload
systemctl enable myapp
systemctl start myapp
```

## 文件监控（inotifywait）
```bash
# 监控目录变化，自动执行动作
inotifywait -m -r -e modify,create,delete /opt/app/src/ | while read path action file; do
    echo "[$(date)] $action: $path$file"
    # 自动重启服务
    systemctl restart myapp
done
```
