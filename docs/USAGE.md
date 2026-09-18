# LiteCode 完整使用说明书

> ⚠️ **部分过时 (2026-09-05)**: 版本/模型/计数以当前为准——默认模型 `qwen3.8-flash-next-local`(上下文 96000)、切换端点 `/api/models/switch`。操作流程大体有效,权威入口以 [`README.md`](../README.md) + [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) 为准。

> 版本：v1.10  ·  最后更新：2026-05-09
> 适用场景：开发者本地、容器、外部接入（OpenAI 兼容 SDK）

---

## 0. 一句话定位

LiteCode 是一个**自托管 AI Agent 平台**：内置 SSE 流式对话、工具调用、记忆库、定时任务、DAG 编排、多模型路由，对外暴露与 OpenAI Chat Completions **完全兼容**的 HTTP 接口，可被 langchain / openai SDK / dify / openwebui 直接接入。

---

## 1. 快速开始

### 1.1 启动

```bash
git clone <repo> && cd litecode
docker compose up -d --build
# 等 30s, 容器内 vllm/qwen 模型加载完
curl -H "Authorization: Bearer CHANGE_ME_TOKEN" http://127.0.0.1:18789/health
```

返回 `{"status":"ok","model":"Qwen3.5-35B",...}` 即就绪。

### 1.2 三个端口

| 端口 | 名字 | 用途 |
| ---- | ---- | ---- |
| 18789 | API | OpenAI 兼容 / 内部路由 |
| 18790 | Web | 浏览器 UI（密码 `CHANGE_ME_PASSWORD`） |
| 6080  | noVNC | 容器内桌面预览（写小说/截图调试用） |

### 1.3 默认 Token

环境变量 `OPENCLAW_TOKEN` 或 `litecodeext/config.json` 的 `server.token`，默认 `CHANGE_ME_TOKEN`。

---

## 2. 聊天基础

### 2.1 Web 聊天

1. 浏览器打开 `http://<host>:18790/`
2. 输 `CHANGE_ME_PASSWORD` 登录
3. 顶部"+"新建会话；侧栏列出所有历史会话
4. 输入框支持：多行（Shift+Enter）、文件上传（📎）、Esc 中止流式
5. 折叠：thinking / tool_call / tool_result 默认折叠，点头部展开；`Ctrl+O` 全展，`Ctrl+K` 全收

### 2.2 CLI 聊天

```bash
# 单次问答
python3 -m litecodeext.cli --query "1+1=?"

# 进入 REPL
python3 -m litecodeext.cli
> 写一个斐波那契
> /exit

# 输出 JSON 流（CI/脚本对接）
python3 -m litecodeext.cli --query "1+1" --output-format stream-json
# 每行一个 JSON: {"type":"reasoning"|"content"|"tool_call"|"usage"|"done"}
```

CLI 关键参数：
- `--query <s>`：单次问答，无 REPL
- `--session <sid>`：续接已有会话
- `--server <url>` `--token <t>`：远程调用
- `--check`：探活
- `--output-format text|stream-json`

### 2.3 OpenAI 兼容接口

```bash
curl -X POST http://127.0.0.1:18789/v1/chat/completions \
  -H "Authorization: Bearer CHANGE_ME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen3.5-35B","messages":[{"role":"user","content":"你好"}],
       "stream":true,"user":"my_session_001"}'
```

- `user` 字段 = session_id（**会话延续**靠它）
- `stream:true` 返回 `data: {...}\n\n` SSE
- 兼容 langchain `ChatOpenAI` / openai SDK / dify

---

## 3. 定时任务（Timer）

### 3.1 用 Web 创建

侧栏 → ⏰ Timer → 填写：
- **type**：`cron`（周期）或 `once`（单次）
- **schedule**：cron 用 `0 9 * * *`（每天 9 点）；once 用 ISO `2026-12-01T10:00:00`
- **action**：`shell` / `prompt` / `dag`
- **content**：要执行的命令 / prompt / dag 名

操作：▶ 立即执行；⏸ 暂停；🗑 删除；点条目看历史。

### 3.2 用 API 创建

```bash
curl -X POST http://127.0.0.1:18789/api/timers \
  -H "Authorization: Bearer CHANGE_ME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"btc-daily","type":"cron","schedule":"0 9 * * *",
       "action":"prompt","prompt":"抓取 BTC 实时价并发邮件给 admin"}'
```

API 列表：
- `GET  /api/timers`        列表
- `POST /api/timers`        新建
- `PATCH /api/timers/<id>`  修改（含 `enabled:true/false`）
- `POST /api/timers/<id>/run` 立即触发
- `DELETE /api/timers/<id>` 删除

### 3.3 cron 速查

| 表达式 | 含义 |
| ---- | ---- |
| `*/5 * * * *` | 每 5 分钟 |
| `0 9 * * *` | 每天 9:00 |
| `0 9 * * 1-5` | 工作日 9:00 |
| `0 0 1 * *` | 每月 1 号 0:00 |

---

## 4. DAG 编排

### 4.1 Web 编辑器

侧栏 → 🔀 DAG：
1. 点 `+ 新建` 输 DAG 名
2. 左侧"📦 节点类型"点击 8 类（coder / analyst / tester / writer / critic / explorer / shell / researcher）一键加到画布
3. **连线**：Shift+点源节点 → 点目标节点
4. **编辑节点**：双击节点改 task
5. **撤销** ↶ / **运行** ▶ / **AI 生成** 🤖（AI 生成会调 LLM 把"扫描+分析+报告"翻成完整 DAG）
6. 💾 保存到 `/api/dags/<name>`

### 4.2 DAG 定义文件

DAG 是 JSON：
```json
{
  "name": "scan_and_report",
  "steps": [
    {"id":"scan","label":"端口扫描","agent_type":"shell","task":"python3 scripts/scan_target.py 1.2.3.4"},
    {"id":"report","label":"生成报告","agent_type":"writer","task":"基于 scan 结果写 markdown","depends_on":["scan"]}
  ]
}
```

### 4.3 运行 DAG

- Web：DAG 面板 ▶ 运行
- API：`POST /api/dags/<name>/run`
- CLI：`python3 -m litecodeext.cli --query "跑 DAG scan_and_report"`

DAG 执行时节点会着色：黄=running / 绿=done / 红=failed。

---

## 5. 对外接口

### 5.1 兼容性

完全兼容 OpenAI Chat Completions：
- ✅ langchain `ChatOpenAI(openai_api_base='http://host:18789/v1', ...)`
- ✅ openai SDK `OpenAI(base_url='http://host:18789/v1', ...)`
- ✅ dify / openwebui / FastGPT 直接接入
- ✅ stream / non-stream 都支持
- ✅ tool_calls 字段标准 OpenAI 格式

### 5.2 langchain 例

```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model='Qwen3.5-35B',
                 openai_api_base='http://127.0.0.1:18789/v1',
                 openai_api_key='CHANGE_ME_TOKEN',
                 streaming=True)
for chunk in llm.stream('用一句话介绍自己'):
    print(chunk.content, end='', flush=True)
```

### 5.3 openai SDK 例

```python
import openai
client = openai.OpenAI(base_url='http://127.0.0.1:18789/v1', api_key='CHANGE_ME_TOKEN')
r = client.chat.completions.create(
    model='Qwen3.5-35B',
    messages=[{'role':'user','content':'你好'}],
    stream=True,
    user='my_session_001'   # ← session_id
)
for c in r:
    if c.choices[0].delta.content:
        print(c.choices[0].delta.content, end='', flush=True)
```

### 5.4 鉴权 / 错误

| 错误 | 状态码 |
| ---- | ---- |
| 错 token / 无 token | 401 |
| 空 messages | 400 |
| 模型未加载 | 503 |

---

## 6. CLI 进阶

### 6.1 折叠键位

进入 REPL 后：
- `Ctrl+O`：全展（thinking + tool_result 完整显示）
- `Ctrl+K`：全收
- `Ctrl+C`：中止当前流式
- `Ctrl+D`：退出

### 6.2 stream-json 输出

适合脚本管道处理。每行一个 JSON：
```json
{"type": "reasoning", "text": "..."}
{"type": "content", "text": "..."}
{"type": "tool_call", "name": "read_file", "args": {...}}
{"type": "tool_result", "name": "read_file", "output": "..."}
{"type": "usage", "prompt_tokens": 100, "completion_tokens": 50, ...}
{"type": "done"}
```

校验示例：
```bash
python3 -m litecodeext.cli --query "1+1" --output-format stream-json | \
  while read l; do echo "$l" | python3 -m json.tool > /dev/null || echo "BAD: $l"; done
```

### 6.3 远程调用

```bash
python3 -m litecodeext.cli --server http://1.2.3.4:18789 --token xxxxx --query "..."
```

---

## 7. 工具调用速查

LiteCode 内置工具（LLM 自动调用）：

| 工具 | 用途 |
| ---- | ---- |
| `read_file` | 读文件（支持 offset / limit） |
| `write_file` | 写文件（覆盖或追加） |
| `edit_file` | 精确字符串替换 |
| `execute_shell` | 跑 shell 命令 |
| `web_search` | 谷歌搜索（带摘要） |
| `web_fetch` | 抓 URL |
| `vision_ocr` | 图片转文字（默认 Qwen-VL） |
| `find_symbol` | 在代码库找函数/类 |
| `save_memory` | 写入长期记忆 |
| `recall_memory` | 召回记忆 |
| `create_timer` | 建定时任务 |
| `list_timers` | 列定时任务 |
| `delete_timer` | 删定时任务 |
| `spawn_agent` | 起子 agent（支持 parallel / serial） |

LLM 决定何时调用，调用过程在 thinking / tool_call / tool_result 三段折叠中可见。

---

## 8. 故障排查

### 8.1 启动失败

```bash
docker compose logs litecode | tail -100
```

常见：
- vllm 模型 OOM → 改 `config.json` 的 `model.max_model_len`
- 端口被占 → 改 `docker-compose.yml` 的 ports
- `/health` 一直 503 → 容器内 `nvidia-smi` 看 GPU

### 8.2 CLI 卡住

`Ctrl+C` 一次中止流式，再次 `Ctrl+C` 退出 REPL。

### 8.3 Web UI 白屏

浏览器 F12 → Network → 看 `/assets/*.js` 是否 200。LiteCode 不依赖任何 CDN，全部本地静态。

### 8.4 第三方 SDK 连不上

1. `curl -H "Authorization: Bearer CHANGE_ME_TOKEN" http://host:18789/v1/models` 必须返回模型列表
2. base_url 末尾**有 /v1**
3. user 字段填 session_id（不填每次新会话）

### 8.5 微信桥接报"sdk 未装"

容器内 `pip show wcferry`。Dockerfile 已装；容器外（VM 直跑）需要手动 `pip install wcferry`（见 P53）。

### 8.6 DAG 切换残留连线

v1.10 P56 已修：`web_assets/dag_editor.js fromJSON` 清空 state 后立即清 SVG。如还遇到，强刷 Ctrl+Shift+R。

---

## 9. 测试 / 自检

```bash
# 冒烟
bash tests/smoke.sh

# 全量测试矩阵
python3 litecodeext/tests/test_full.py --offline

# 第三方接口冒烟（OpenAI 兼容路径）
curl -X POST http://127.0.0.1:18789/v1/chat/completions \
  -H "Authorization: Bearer CHANGE_ME_TOKEN" -H "Content-Type: application/json" \
  -d '{"model":"Qwen3.5-35B","messages":[{"role":"user","content":"hi"}]}'

# 完整 E2E（参考 reports/e2e_full_*/tools/runner.py）
ls reports/e2e_full_*/
```

---

## 10. 附录：文件结构

```
litecode/
├── litecodeext/           主代码
│   ├── litecode_server.py SSE / 路由
│   ├── cli.py             CLI REPL
│   ├── web_ui.py          Web 后端
│   ├── web_ui.html        Web 入口 HTML
│   ├── web_assets/        前端 JS/CSS
│   ├── lib/               工具实现
│   ├── prompts/           系统提示词
│   └── config.json        全局配置
├── tests/                 测试
├── scripts/               运维脚本
├── docs/                  文档（含本文）
├── workspace/             运行时 / 用户数据
└── reports/               测试报告归档
```

---

完。问题往 `BACKLOG.md` 的 Inbox 段写。
