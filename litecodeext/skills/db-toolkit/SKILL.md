---
name: db-toolkit
description: >
  数据库操作与管理技能。支持：SQLite(零配置)、PostgreSQL、MySQL、Redis。
  覆盖：建表、CRUD、索引优化、备份恢复、数据迁移、SQL查询调优、
  ORM使用(SQLAlchemy)、连接池配置。
  触发关键词：数据库、database、sql、sqlite、postgresql、mysql、redis、
  建表、查询、索引、备份、迁移、orm、sqlalchemy、select、insert、update、
  join、事务、transaction。遇到任何数据库相关任务必须使用本技能。

## Keywords
数据库 database sql sqlite postgresql mysql redis mongodb
建表 查询 索引 备份 迁移 orm sqlalchemy select insert update delete
join 事务 transaction index 性能 优化 连接池 schema migration
csv导入 数据导出 dump restore
---

# Database Toolkit Skill

## SQLite（首选，零配置）

### 快速建库
```python
import sqlite3
from contextlib import contextmanager

DB = "app.db"

@contextmanager
def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        conn.close()

def init():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
        """)
```

### CRUD 模板
```python
def create(table, **kwargs):
    cols = ", ".join(kwargs.keys())
    phs = ", ".join(["?"] * len(kwargs))
    with db() as conn:
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({phs})", list(kwargs.values()))

def query(sql, params=None):
    with db() as conn:
        rows = conn.execute(sql, params or []).fetchall()
        return [dict(r) for r in rows]

def update(table, where_col, where_val, **kwargs):
    sets = ", ".join(f"{k}=?" for k in kwargs)
    with db() as conn:
        conn.execute(f"UPDATE {table} SET {sets} WHERE {where_col}=?",
                     list(kwargs.values()) + [where_val])

def delete(table, where_col, where_val):
    with db() as conn:
        conn.execute(f"DELETE FROM {table} WHERE {where_col}=?", [where_val])
```

### CSV 导入/导出
```python
import csv

def import_csv(filepath, table):
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows: return 0
    cols = ", ".join(rows[0].keys())
    phs = ", ".join(["?"] * len(rows[0]))
    with db() as conn:
        conn.executemany(f"INSERT INTO {table} ({cols}) VALUES ({phs})",
                         [list(r.values()) for r in rows])
    return len(rows)

def export_csv(sql, filepath):
    rows = query(sql)
    if not rows: return
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
```

### 备份
```bash
# SQLite 备份
sqlite3 app.db ".backup backup_$(date +%Y%m%d).db"

# 导出SQL
sqlite3 app.db .dump > dump_$(date +%Y%m%d).sql
```

## 命令行快速查询
```bash
# 交互模式
sqlite3 app.db

# 单条SQL
sqlite3 app.db "SELECT * FROM users LIMIT 10"

# 格式化输出
sqlite3 -header -column app.db "SELECT * FROM users"
```
