---
name: api-builder
description: >
  快速 API 服务开发技能。使用 FastAPI/Flask 快速搭建 RESTful API，
  包含：路由设计、请求验证(Pydantic)、JWT认证、CORS、数据库集成(SQLite/PostgreSQL)、
  WebSocket、文件上传、Swagger文档自动生成、异步处理、中间件。
  触发关键词：api、接口、fastapi、flask、restful、后端、服务端、
  endpoint、路由、认证、jwt、token、websocket、swagger、http服务。
  遇到任何 Web API 开发任务必须使用本技能。

## Keywords
api 接口 fastapi flask restful 后端 服务端 endpoint 路由 认证 jwt token
websocket swagger http 服务 server uvicorn gunicorn cors middleware
pydantic validation crud database sqlite postgresql 异步 async
---

# API Builder Skill

## 技术选型
- **FastAPI**（首选）：异步、自动文档、类型验证、性能好
- **Flask**：简单任务、快速原型

## FastAPI 快速模板

### 最小可用服务
```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="My API", version="1.0")

class Item(BaseModel):
    name: str
    value: float
    tags: list[str] = []

items_db: dict = {}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/items/{item_id}")
async def create_item(item_id: str, item: Item):
    if item_id in items_db:
        raise HTTPException(400, "Item exists")
    items_db[item_id] = item.model_dump()
    return {"id": item_id, **items_db[item_id]}

@app.get("/items")
async def list_items():
    return {"items": items_db, "count": len(items_db)}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
```

### JWT 认证模板
```python
import jwt
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

SECRET = "your-secret-key"
security = HTTPBearer()

def create_token(user_id: str, hours: int = 24) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.utcnow() + timedelta(hours=hours)},
        SECRET, algorithm="HS256"
    )

def verify_token(cred = Depends(security)) -> str:
    try:
        payload = jwt.decode(cred.credentials, SECRET, algorithms=["HS256"])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

# 使用: @app.get("/protected")
#        async def protected(user_id: str = Depends(verify_token)):
```

### SQLite + CRUD
```python
import sqlite3, json
from contextlib import contextmanager

DB_PATH = "data.db"

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try: yield conn
    finally: conn.close()

def init_db():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, name TEXT, value REAL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
```

## 启动规范
```bash
# 开发模式（热重载）
uvicorn main:app --host 0.0.0.0 --port 8080 --reload

# 生产模式（background=true）
execute_shell(command="cd /workspace/project && python3 -m uvicorn main:app --host 0.0.0.0 --port 8080",
              background=true, health_url="http://localhost:8080/health")
```

## 依赖
```
fastapi>=0.100
uvicorn>=0.20
pydantic>=2.0
PyJWT>=2.0
```
