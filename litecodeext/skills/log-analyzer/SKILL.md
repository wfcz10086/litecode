---
name: log-analyzer
description: >
  日志分析与监控技能。支持：系统日志(syslog/journalctl)、应用日志(nginx/python/java)、
  实时尾部追踪、关键词过滤、错误统计、时间范围查询、日志聚合分析、
  异常检测、日志轮转配置。触发关键词：日志、log、journalctl、syslog、
  报错、error、异常、exception、tail、grep、分析日志、日志统计、
  access.log、error.log、排查。遇到任何日志相关任务必须使用本技能。

## Keywords
日志 log journalctl syslog 报错 error 异常 exception tail grep
分析日志 日志统计 access.log error.log nginx apache python java
排查 debug trace warning fatal 告警 alert 监控 logrotate
---

# Log Analyzer Skill

## 快速诊断命令

### 系统日志
```bash
# 最近的错误
journalctl -p err --since "1 hour ago" --no-pager | tail -50

# 某服务的日志
journalctl -u nginx -f --no-pager -n 100

# 某时间段
journalctl --since "2025-01-01 00:00" --until "2025-01-01 23:59"

# 内核日志（硬件错误/OOM）
dmesg -T | tail -50
dmesg -T | grep -i "error\|oom\|kill\|fail"
```

### 应用日志分析
```bash
# 错误统计（最近1000行）
tail -1000 /var/log/app.log | grep -c "ERROR"

# 错误分类
tail -5000 /var/log/app.log | grep "ERROR" | awk -F'ERROR' '{print $2}' | sort | uniq -c | sort -rn | head -20

# 按小时统计请求量
awk '{print $4}' /var/log/nginx/access.log | cut -d: -f1-2 | sort | uniq -c | sort -rn | head -24

# Top 10 慢请求（nginx）
awk '{print $NF, $7}' /var/log/nginx/access.log | sort -rn | head -10

# Top IP
awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -20

# HTTP 状态码分布
awk '{print $9}' /var/log/nginx/access.log | sort | uniq -c | sort -rn
```

### Python 脚本分析
```python
import re
from collections import Counter, defaultdict
from datetime import datetime

def analyze_log(filepath, error_pattern=r"ERROR|EXCEPTION|FATAL"):
    errors = []
    hourly = defaultdict(int)
    with open(filepath, "r", errors="replace") as f:
        for line in f:
            if re.search(error_pattern, line, re.I):
                errors.append(line.strip())
            # 提取时间戳（常见格式）
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2})", line)
            if m:
                hourly[m.group(1)] += 1

    error_types = Counter()
    for e in errors:
        # 提取错误类型
        m = re.search(r"([\w.]+(?:Error|Exception|Failure))", e)
        if m: error_types[m.group(1)] += 1

    return {
        "total_errors": len(errors),
        "error_types": error_types.most_common(10),
        "peak_hour": max(hourly, key=hourly.get) if hourly else None,
        "recent_errors": errors[-5:],
    }
```

## 输出格式
```
[LOG] /var/log/app.log 分析报告 (最近24h)
总行数: 45,230 | 错误: 127 (0.28%)
错误分类:
  ConnectionError: 45 (35.4%)
  TimeoutError: 38 (29.9%)
  ValueError: 22 (17.3%)
  其他: 22 (17.3%)
峰值时段: 14:00-15:00 (8,320 行)
最近错误: [2025-01-01 23:45:12] ConnectionError: host unreachable
建议: ConnectionError 占比最高，检查下游服务连通性
```
