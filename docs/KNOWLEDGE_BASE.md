# 内置知识库 (plugins/kb)

把文档 (手册、制度、合同、产品资料、扫描件…) 放进知识库, 对话里问「根据知识库/资料/手册…」时,
模型调用 `kb_search` 检索原文片段, 回答里用 `[n]` 标出处; Web 端把 `[n]` 做成可悬停 (看片段) / 可点击
(看原文定位) 的引用。

由一个已有的个人知识库项目改造而来 (没有引入 MaxKB 这类重平台, 全新建库),
自带 PostgreSQL + pgvector, 服务器版和桌面版都带, 桌面版默认带本地向量模型, 离线可用。

## 架构

```
Web 面板 (web_assets/kb_panel.js)           对话 (litecode_server 进程)
  │ /api/kb/* (登录鉴权)                       │ kb_search 工具 (core/tools/handlers/kb.py → plugins/kb/tool.py)
  ▼                                          │   优先 POST 本机 web_ui /api/kb/search (头 X-LiteCode-Token = server.token),
routers/kb_router.py → plugins/kb/router.py ◀┘   向量模型只在 web_ui 进程里加载一份; web_ui 不在时本进程直接检索
  │
  ├─ jobs.py   后台入库线程: 认领 (SKIP LOCKED) → 解析 → 切块 → 向量化 → 一个事务写入
  ├─ text.py   解析 (pdf/docx/pptx/xlsx/txt/md/html/图片) + 切分 450 字/重叠 80 + 出处定位 + 中文 bigram 关键词
  ├─ embed.py  ONNX 向量模型 (纯 Python WordPiece 分词, 只依赖 onnxruntime+numpy), 单例懒加载, 闲置 10 分钟卸载
  └─ store.py  PG 建表迁移 + CRUD + 混合检索 + 上下文窗口
        │
        ▼
  PostgreSQL 16 + pgvector (服务器版: kb-pg 容器; 桌面版: WSL 里的 postgresql-16)
  原始文件: WORKSPACE/kb/<库id>/files/<sha256>.<扩展名>
```

### 数据表 (首次连接自动建, 幂等)

| 表 | 说明 |
|---|---|
| `kb_bases` | 库: 名称唯一、说明 |
| `kb_documents` | 文档: `kb_id` 外键 (删库级联)、`content_hash` (sha256, 同库去重)、原文件名、状态 `queued/parsing/embedding/done/failed` + 进度 + 错误、解析后的全文 `text` |
| `kb_chunks` | 块: `start_pos/end_pos` (全文偏移)、出处 `loc` (第3页 / 第12-14段 / 第2张幻灯片 / 工作表「…」 / 图片识别)、`terms` (中文 bigram + 英文词) → `tsv` 生成列 + GIN、`embedding vector(768)` + HNSW 余弦索引 (pgvector ≥ 0.5, 否则 ivfflat) |

### 检索

1. 向量近邻 40 条 ∪ 关键词 (`tsv @@ to_tsquery('simple', 'bigram1 | bigram2 …')`) 40 条;
2. 融合分 = 0.75 × 向量余弦 + 0.25 × 问题关键词覆盖率 (问题里去掉 什么/多少/如何 等疑问词);
3. 低于 `min_score` (默认 0.5, 实测无关内容 0.45~0.5) 丢弃; 同样内容 (块哈希相同) 只留一条; 取 `top_k`;
4. **小块检索、大块返回**: 命中块以自身为中心向两边扩成上下文窗口 (`context_chars`, 默认 3000 字, 可调到 4000),
   贴着文档首尾时往另一侧补足; 同文档已被前面窗口覆盖的命中直接并入, 重叠部分裁掉, 首尾相接的窗口合成一个 ——
   同一段文字只返回一次; 所有窗口合计不超过 `total_chars` (默认 9000 字 ≈ 6k token);
5. 出处显示命中块的位置 (`第5页`), 同窗口里其他命中的位置列为「同文另见」。

## 和原项目的差异

| 原项目 | 现在 |
|---|---|
| 切块 4000 字, 远超模型 256 token, 块后半段进不了向量 (只检索得到块开头) | 450 字小块 + 80 字重叠; 超过 254 token 的块分段编码后按长度加权平均, 块尾也进向量; 返回给模型时再扩成 3000 字窗口 |
| 只有向量检索 | 向量 + 中文 bigram 关键词混合, 阈值 + 去重 + 相邻合并 |
| 无鉴权, `/search` 不校验用户 | 全部接口走 LiteCode 登录; 工具调用走本机 server.token |
| 导入接口可执行任意路径的 SQL 文件 | 不做任何 SQL 导入; 只收文档 (上传或工作区内路径, 路径校验在 WORKSPACE 内且不在知识库目录) |
| 依赖外部 OCR / MinIO / 35B 改写, 4 个进程各加载一份模型 | 本地解析 (pypdf/python-docx/python-pptx/openpyxl), 扫描页/图片走当前模型的视觉识别 (vision_ocr); 原始文件存本地; 模型只在 web_ui 进程加载一份, 闲置卸载 |
| 明文弱密码 | PG 账号 `litecode_kb` 非超级用户, 随机密码首次生成存 `.env` / `config.json` (不入 git) |
| PyTorch + sentence-transformers | 一次性导出 ONNX, 运行时只要 onnxruntime (镜像已带); 分词纯 Python, 与 transformers 逐 id 对齐 (测试覆盖) |

迁移: **全新建库**, 不导入原项目的数据库备份。旧资料需要的话, 把原始文档重新拖进面板即可
(同内容自动去重)。原项目的 knowledge_graph (LLM 改写增强)、
远程 embedding 切换、重排、导出导入包、网页 URL 导入列为二期, 本期未做。

## 部署

### 服务器版 (docker compose)

```bash
# 1. 向量模型 (一次): PyTorch → ONNX, 在临时 python 容器里导出, 输出到 workspace/kb/models/ (不入 git)
bash scripts/kb_export_onnx.sh <text2vec-base-chinese-paraphrase 模型目录>   # 模型可从 ModelScope / HuggingFace 下载
# 2. 数据库 + 配置 (幂等, 可重复跑):
bash scripts/kb_setup.sh
#    - .env 里生成 KB_PG_SUPER_PASSWORD / KB_PG_PASSWORD (随机, 不入 git)
#    - docker compose --profile kb up -d --no-deps kb-pg   (只起这个新容器, 不碰 litecode)
#    - 建非超级用户 litecode_kb + 库 + vector 扩展
#    - config.json 写入 kb.enabled / kb.dsn (原地改写, 保持 bind-mount 的 inode)
# 3. 让生产 web_ui / server 加载新代码 (重启前先跑 python3 scripts/litecode_idle.py --wait)
```

- `kb-pg`: `pgvector/pgvector:pg16`, host 网络但只监听 `127.0.0.1:15432`, 卷 `kb_pg_data`, 内存上限 1G,
  `profiles: ["kb"]` —— 不用知识库就不起。
- 模型目录 `workspace/kb/models/text2vec-base-chinese-paraphrase-onnx` 在 litecode 已有的 `./workspace` 卷里,
  不用改 litecode 的 volumes。
- Python 依赖: 生产镜像已带 onnxruntime / psycopg2-binary / numpy / pypdf / pypdfium2 / python-docx /
  python-pptx / openpyxl / python-multipart, 无需新装; `Dockerfile` 里显式声明了 onnxruntime + psycopg2-binary。

### 桌面版 (Windows / WSL)

- `packaging/windows/Dockerfile.deps`: 装 `postgresql-16 postgresql-16-pgvector` (Ubuntu 24.04 官方源,
  pgvector 0.6.0), 删掉发行版默认集群; 模型拷到 `/opt/litecode-models/text2vec-base-chinese-paraphrase-onnx`
  (`release.sh` 构建前从 `KB_MODEL_SRC`, 默认 `/opt/litecode/workspace/kb/models/…` 拷进构建目录, 并计入依赖层指纹)。
- `wsl/litecode-kb-pg` (由 `litecode-wsl-start` 在服务启动前调用): 数据目录 `/var/lib/litecode/kb_pg`
  (`/root` 是 700, postgres 用户进不去, 所以不放 `/root/.litecode`), 首次 initdb → 建 `litecode_kb`
  (随机密码) → 写 `config.json` 的 `kb.dsn`; 之后只启动。只监听 127.0.0.1:15432。托盘停服务是
  `wsl --terminate`, PG 被直接杀掉, 下次按 WAL 恢复 (已模拟 kill -9 验证)。起不来只写日志
  `/root/.litecode/kb_pg.log`, 不挡 LiteCode。
- 升级保留: `setup.go` 的 `dataSpecs` 加了 `var/lib/litecode/kb_pg`; 原始文件在 `WORKSPACE`
  (`/tmp/litecode_workspace`) 本来就保留; `config.json` 由 `litecode-apply-update` 保留。
- 体积估算: PostgreSQL 16 + pgvector 解压后约 +61MB (压缩约 20MB); 模型解压 452MB (gzip 后 439MB,
  fp32 权重几乎压不动) → 安装包约 +460MB, 安装后约 +515MB。

## 配置 (config.json 的 `kb` 段)

```json
"kb": {
  "enabled": true,
  "dsn": "postgresql://litecode_kb:<随机密码>@127.0.0.1:15432/litecode_kb",
  "model_dir": "",            // 空 = 依次找 LITECODE_KB_MODEL_DIR / WORKSPACE/kb/models/… / /opt/litecode-models/…
  "chunk_size": 450, "chunk_overlap": 80,
  "top_k": 5, "min_score": 0.5,
  "context_chars": 3000, "total_chars": 9000,
  "ocr": true,                // 图片 / 扫描版 PDF 页走当前模型的视觉识别
  "max_file_mb": 200,
  "model_idle_minutes": 10    // 向量模型闲置多久卸载, 0 = 常驻
}
```

拔插: 删掉 `kb` 段 (或 `enabled: false`) 即停用 —— 接口回 `enabled: false`, 工具回「未启用」, 面板显示部署说明;
删 `routers/kb_router.py` 卸下接口; 删 `web_ui.html` 里 `kb_panel.js/css` 两行卸下前端。环境变量
`LITECODE_KB_DSN` 可覆盖 dsn。

## 接口 (/api/kb, 均需登录)

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/kb/status` | 启用/数据库/模型状态、索引类型、当前配置 |
| GET/POST | `/api/kb/bases` | 库列表 (含文档数/块数/处理中/失败) / 新建 `{name, description}` |
| PUT/DELETE | `/api/kb/bases/{id}` | 重命名改说明 / 删除 (连同文档、块和原始文件) |
| GET | `/api/kb/bases/{id}/docs` | 文档列表与状态 |
| POST | `/api/kb/bases/{id}/upload` | multipart 单文件 (字段 `file`), 同库同内容去重 (`duplicate: true`) |
| POST | `/api/kb/bases/{id}/import_path` | `{path}` 工作区内已有文件 (如对话附件 `uploads/x.pdf`) 入库 |
| POST | `/api/kb/docs/{id}/retry` | 失败 (或已完成) 的文档重新入库 |
| DELETE | `/api/kb/docs/{id}` | 删除文档及原始文件 |
| GET | `/api/kb/docs/{id}/chunks?offset&limit` | 分块预览 |
| GET | `/api/kb/docs/{id}/text?start&length` | 解析后的全文片段 (引用预览) |
| GET | `/api/kb/docs/{id}/file` | 原始文件 (路径由库 id + 哈希 + 白名单扩展名拼出并校验在库目录内) |
| POST | `/api/kb/search` | `{query, kb? / kb_id?, top_k?}` → 结果含 filename / loc / also / score / cos / coverage / snippet / context / 偏移 |

工具: `kb_search(query, kb?, top_k?)`, 在按需工具组 `kb` (lib/tool_groups.py; 关键词 知识库/资料库/文档里/手册/
根据资料/制度…), 只读。

## 实测 (2026-10-22, 第二实例 38015 + kb-pg, 模型 GLM-5.3-Flash)

| 项 | 结果 |
|---|---|
| 入库 3 份 (6.8k 字 PDF 5 页 / docx / 扫描图片 OCR) | 17 秒全部完成 (PDF 19 块约 10 秒, 向量化 ~2 块/秒, 4 线程 CPU) |
| 检索延迟 | 30~60 ms (模型已加载) |
| 内存 | web_ui 加载模型后约 0.9GB (入库峰值 1.1GB), 闲置 10 分钟自动卸载 + malloc_trim 后回落到加载前水平 (单测进程 656MB → 65MB); litecode_server 不加载模型 (81MB); kb-pg 约 42MB, 数据目录 48MB |
| 问答 | 4 题 (PDF/docx/扫描图片/文档最后一段) 全部答对并带 [1] 出处, 每题 4~5 秒 |
