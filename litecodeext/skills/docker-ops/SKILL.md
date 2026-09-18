---
name: docker-ops
description: >
  Docker 容器与镜像管理技能。覆盖：容器生命周期管理、镜像构建与优化、
  docker-compose 编排、日志查看、资源监控、网络配置、数据卷管理、
  多阶段构建、容器排错。触发关键词：docker、容器、镜像、compose、
  dockerfile、container、image、volume、network、registry、harbor、
  docker-compose、swarm、端口映射、挂载。
  遇到任何 Docker 相关任务必须使用本技能。

## Keywords
docker 容器 镜像 compose dockerfile container image volume network
registry harbor pull push build run exec logs inspect
docker-compose swarm 端口映射 挂载 mount bind
---

# Docker Ops Skill

## 常用命令速查

### 容器管理
```bash
# 查看运行中容器
docker ps
docker ps -a  # 包含已停止

# 启动/停止/重启
docker start/stop/restart <name|id>

# 进入容器
docker exec -it <name> bash
docker exec -it <name> sh  # alpine 镜像

# 查看日志
docker logs -f --tail 100 <name>

# 查看资源占用
docker stats --no-stream

# 清理
docker system prune -f          # 清理停止的容器+悬空镜像
docker system prune -a -f       # 清理所有未使用的镜像
docker volume prune -f          # 清理未使用的卷
```

### Docker Compose
```bash
docker-compose up -d             # 后台启动
docker-compose down              # 停止并删除
docker-compose logs -f <service> # 查看服务日志
docker-compose ps                # 查看服务状态
docker-compose restart <service> # 重启服务
docker-compose pull              # 拉取最新镜像
```

### Dockerfile 最佳实践
```dockerfile
# 多阶段构建（减小镜像体积）
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY . .
EXPOSE 8080
CMD ["python3", "main.py"]
```

### docker-compose.yml 模板
```yaml
version: "3.8"
services:
  app:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    environment:
      - DATABASE_URL=sqlite:///data/app.db
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

### 排错
```bash
# 容器无法启动
docker logs <name>
docker inspect <name> | grep -A5 "State"

# 网络问题
docker network ls
docker network inspect bridge

# 磁盘空间
docker system df
```

## GPU Docker（NVIDIA）
```bash
# 检查 nvidia-docker
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi

# GPU 容器启动
docker run -d --gpus all --name ml_train \
  -v /data:/data \
  -p 8888:8888 \
  pytorch/pytorch:latest
```
