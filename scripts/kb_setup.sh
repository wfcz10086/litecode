#!/bin/bash
# 服务器版内置知识库一键部署 (幂等, 可重复跑): docs/KNOWLEDGE_BASE.md
#   1. .env 里没有就生成随机密码 KB_PG_SUPER_PASSWORD (postgres 超级用户, 只用于建库) / KB_PG_PASSWORD (litecode_kb)
#   2. docker compose --profile kb up -d kb-pg   (只起这一个服务, 不碰 litecode 等其他容器)
#   3. 建非超级用户 litecode_kb + 库 litecode_kb + vector 扩展 (已存在则只同步密码)
#   4. 写 config.json 的 kb.enabled / kb.dsn (原地改写, 不换 inode: config.json 是单文件 bind-mount)
#   5. 检查向量模型, 没有就提示运行 scripts/kb_export_onnx.sh
# 环境变量: KB_ROOT (项目目录, 默认本仓库根) KB_COMPOSE_FILE KB_CONFIG KB_PROJECT (compose 项目名, 默认目录名)
#           KB_NO_CONFIG=1 不改 config.json, 只打印 DSN
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
ROOT=${KB_ROOT:-$HERE}
COMPOSE=${KB_COMPOSE_FILE:-$ROOT/docker-compose.yml}
CONFIG=${KB_CONFIG:-$ROOT/config.json}
PROJECT=${KB_PROJECT:-$(basename "$ROOT")}
ENVF=$ROOT/.env
PORT=15432
MODEL=$ROOT/workspace/kb/models/text2vec-base-chinese-paraphrase-onnx

touch "$ENVF"
get() { grep -E "^$1=" "$ENVF" | tail -1 | cut -d= -f2- || true; }
for k in KB_PG_SUPER_PASSWORD KB_PG_PASSWORD; do
  if [ -z "$(get "$k")" ]; then
    echo "$k=$(openssl rand -hex 16)" >> "$ENVF"
    echo "已生成 $k (写入 $ENVF)"
  fi
done
chmod 600 "$ENVF"
SUPER=$(get KB_PG_SUPER_PASSWORD)
APPPW=$(get KB_PG_PASSWORD)

docker compose -f "$COMPOSE" --project-directory "$ROOT" -p "$PROJECT" --env-file "$ENVF" --profile kb up -d --no-deps kb-pg
echo -n "等待 kb-pg 就绪"
for _ in $(seq 90); do
  if docker exec kb-pg pg_isready -h 127.0.0.1 -p $PORT -U postgres >/dev/null 2>&1; then break; fi
  echo -n .; sleep 1
done
echo
docker exec -i -e PGPASSWORD="$SUPER" kb-pg psql -q -v ON_ERROR_STOP=1 -h 127.0.0.1 -p $PORT -U postgres -d postgres \
  -v pw="$APPPW" <<'SQL'
SELECT format('CREATE ROLE litecode_kb LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'litecode_kb') \gexec
SELECT format('ALTER ROLE litecode_kb PASSWORD %L', :'pw') \gexec
SELECT 'CREATE DATABASE litecode_kb OWNER litecode_kb'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'litecode_kb') \gexec
\c litecode_kb
CREATE EXTENSION IF NOT EXISTS vector;
SQL
echo "数据库就绪: litecode_kb@127.0.0.1:$PORT (pgvector $(docker exec -e PGPASSWORD="$SUPER" kb-pg psql -tA -h 127.0.0.1 -p $PORT -U postgres -d litecode_kb -c "select extversion from pg_extension where extname='vector'"))"

DSN="postgresql://litecode_kb:$APPPW@127.0.0.1:$PORT/litecode_kb"
if [ "${KB_NO_CONFIG:-}" = "1" ]; then
  echo "未改 config.json (KB_NO_CONFIG=1); 手工加: \"kb\": {\"enabled\": true, \"dsn\": \"postgresql://litecode_kb:***@127.0.0.1:$PORT/litecode_kb\"}"
else
  python3 - "$CONFIG" "$DSN" <<'PY'
import json, sys
p, dsn = sys.argv[1], sys.argv[2]
with open(p, "r+", encoding="utf-8") as f:          # 原地改写, 保留 inode
    cfg = json.load(f)
    cfg["kb"] = {**(cfg.get("kb") or {}), "enabled": True, "dsn": dsn}
    f.seek(0)
    f.write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    f.truncate()
print(f"已写入 {p} 的 kb.enabled / kb.dsn")
PY
fi

if [ -f "$MODEL/model.onnx" ]; then
  echo "向量模型: $MODEL ($(du -sh "$MODEL" | cut -f1))"
else
  echo "⚠ 未找到向量模型 $MODEL/model.onnx —— 运行: bash scripts/kb_export_onnx.sh <text2vec-base-chinese-paraphrase 目录> $MODEL"
fi
echo "完成。web_ui 下次启动 (或第一次打开知识库面板) 时自动建表。"
