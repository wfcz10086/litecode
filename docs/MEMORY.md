# 记忆系统设计说明

> 代码位置: `litecodeext/core/memory/`、`litecodeext/core/memory_index.py`、
> `litecodeext/core/tools/handlers/memory_ops.py`、`litecodeext/lib/turn_sources.py`。
> 2026-10-21 版：加了全局用户档案、冲突更新、中文近义召回、可查可删接口。

## 1. 三层记忆

| 层 | 存储 | 范围 | 写入 | 读取 / 注入 |
|---|---|---|---|---|
| **全局用户档案** (新) | `<workspace>/profile/<pid>/facts.json` + `USER.md` | 所有会话共享 | `save_memory`(偏好/身份类自动或 `scope='user'`)、`update_profile file=USER.md`、规则自动捕获(仅高置信)、记忆面板 API | 每轮 `turn_context` 来源 `user_profile`：常驻块 (预算 1200 字) + 超预算部分按相关度召回；`USER.md` 进 system prompt |
| 会话记忆 L1/L2 | `sessions/<sid>/MEMORY.md`、`memory/*.md` | 本会话 | `save_memory scope='session'`、每轮规则提取、LLM 压缩 | `memory_recall` 来源：BM25 挑相关条目 (1200 字) |
| 跨会话检索索引 L3 | `<workspace>/.memory_index.db` (SQLite FTS5 + 中文 BM25 兜底) | 全部会话 | 错误经验、决策、Vault、偏好行；档案事实以 `category=profile` 同步 | `memory_index` 来源：`search_as_context` (1500 字，排除 profile 类，因为档案块已经给了最新值) |

`pid` (profile id)：Web / CLI / DAG / 定时任务都是 `owner`；`wx-*` / `wecom*` 会话按联系人各用一份档案，
别人在微信里说的话不会写进主人的档案。

## 2. 写入路径

```
用户: "记住: 我最喜欢的水果是山竹"
  ├─ 模型调 save_memory(content=…)            ─┐
  │    _route(): scope / section / facts.extract() 判断 → user
  │    ProfileStore.upsert()  抽取 key=喜欢的水果, value=山竹
  │    sync_index()           L3 写 profile 行
  │    返回 "已写入全局用户档案 (所有会话共享 …)"   ← 文案如实写出范围
  └─ 每轮结束 rule_capture_user_facts()         ─┘ (模型没调工具也能记住)
       只认: 抽得出 key 的身份/偏好句 + "记住/以后/每次/一律…" 明确长期偏好 (消息 ≤ 300 字)
       第三方信封 <<<外部内容…>>> 里的文字先剥掉
```

**冲突更新** (`ProfileStore.upsert`)：

1. 同 key (归一后，"最爱吃的水果" = "喜欢的水果") → 新值替换旧值，旧值进 `history`（只留痕，不再注入）；
2. 无 key：规范化后整句相同 → `unchanged`；两字切词 Jaccard ≥ 0.6 → 当作改写，替换；
3. "改喜欢榴莲了" 这种不带类别的说法，靠一个小词表 (水果/颜色/饮料/编程语言…) 推出 key；
   词表覆盖不到时模型可以传 `key`（工具说明里写了"用户改偏好时用同一个 key"）。
4. 替换或删除时，L3 里含旧值的 profile/preference 行一起删掉，免得 "历史经验参考" 又把山竹召回。

`update_profile file=USER.md` 写全局 `USER.md`，合并时 `- 键: 值` 形式的行按键覆盖
(`merge_user_md`)，不会出现两行互相矛盾的偏好。TOOLS.md / SOUL.md 等仍是会话副本，返回文案注明"仅本会话"。

## 3. 读取 / 召回

- **中文分词**：没有 jieba / 向量模型可用（容器与宿主机都没装，也不引入需要下载模型的依赖），
  用 `recall.tokens()`：先做同义归一 (爱吃→喜欢吃、啥→什么、姓名/称呼→名字、回答→回复…)，
  再去掉虚词/代词 (的了是吗我你…)，最后两字切词。"我爱吃啥水果" → `喜欢 / 欢吃 / 水果`，
  与 "最喜欢的水果是山竹" 重合 2/3，过召回门槛。
- **打分**：BM25 × 重要度 (1 + 0.15·importance) × 时效 (半衰期 180 天，最低 0.6)；
  L3 中文兜底同样加了时效衰减 (半衰期 90 天) 与召回次数加权。
- **注入预算**：档案常驻块 1200 字 (`memory.profile_max_chars`)，超出的条目每轮按相关度最多补 3 条
  (`memory.profile_recall_k`)；会话 L1 召回 1200 字；L3 1500 字。档案块首行带内容指纹，
  内容不变时同一会话只给一次，变了 (例如本会话里改了偏好) 下一轮重给。
- 关闭档案注入：`config.json` 里 `memory.user_profile=false`。

**取舍**：向量召回对 "我爱吃啥水果" 这类改写更稳，但需要嵌入模型 (几十到几百 MB) 或额外服务；
当前档案通常几十条，常驻块就能全部放下，召回只在超预算时才起作用，同义归一 + BM25 足够。
以后档案规模变大时，可在 `ProfileStore._rank` 里加一路嵌入相似度 (例如调用已部署的 embedding 端点)。

## 4. 防污染

- `lib/agent/untrusted.py` 的 `TurnGuard.prepare_memory_write()`：记忆写入文字若取自本回合外部内容
  (网页、搜索、OCR、远程输出、别人的消息)，加 `[来源=外部内容 未经主人确认]` 标记；
- 带这个标记的内容：`ProfileStore.upsert` 拒绝，`save_memory` 只写本会话 (并标注)，
  `update_profile USER.md` 只写会话副本 —— **外部内容进不了全局档案**；
- 规则自动捕获先剥掉第三方信封；长消息 (任务说明) 里的 "必须/禁止" 不进全局；
- 注入命中后，高影响工具闸同样拦截 `save_memory`/`update_profile` 写入 (`save_memory action=list` 放行)。

## 5. 可查、可删、可改

模型侧：`save_memory(action='list')` 列出全局档案与本会话偏好；`save_memory(action='forget', key=… | content=…)`
从全局档案、L3、本会话 L1 一起删除。

HTTP 接口 (gateway，web_ui 以 `/api/...` 同名转发)：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/v1/profile?sid=` | 档案事实 + 全局 USER.md |
| POST | `/v1/profile/facts` `{text, key?}` | 手动添加 (同 key 自动替换) |
| PATCH | `/v1/profile/facts/{id}` `{text}` | 纠正 (保留 id，旧值进 history) |
| DELETE | `/v1/profile/facts/{id}` | 删除 (同步删 L3) |
| PUT | `/v1/profile/user_md` `{content}` | 直接编辑全局 USER.md |
| DELETE | `/v1/memory/{sid}/entry` `{text, section?}` | 删本会话 MEMORY.md 某条 |
| GET / DELETE | `/v1/memory_index?q=&category=` / `/v1/memory_index/{id}` | 查看 / 删除跨会话索引条目 |

**前端方案** (web_assets 由别的代理在改，这里只提方案)：设置里加「记忆」页，三个分区——
"关于你" (档案事实列表，每条右侧 编辑/删除，顶部 "添加一条")、"个人说明" (USER.md 文本框，保存走 PUT)、
"历史经验" (L3 列表 + 搜索 + 删除)。对话里模型调用 `save_memory` 写全局档案时，在工具卡片上显示
"已更新记忆 · 管理" 链接 (对标 ChatGPT 的 "Memory updated")。

## 6. 与一线方案对照

| 能力 | mem0 / Letta / Zep / ChatGPT / Claude | 本实现 |
|---|---|---|
| 全局用户画像 vs 会话记忆 | 全部区分 (ChatGPT saved memories、Letta core memory block) | ✅ `profile/` 全局 + `sessions/` 会话 |
| 写入时抽取 + 去重 + 冲突更新 | mem0 LLM 判 ADD/UPDATE/DELETE；Zep 事实失效时间 | ✅ 规则抽取 + 同 key 覆盖 + 历史留痕；模型可传 key；⚠️ 无 LLM 判冲突 |
| 检索：关键词 + 语义 + 时效 + 重要度 | 向量 + BM25 + 图 (Zep) | ✅ 关键词 + 中文同义归一 + 时效 + 重要度；❌ 无向量 |
| 可见可删可改 | ChatGPT / Claude 设置页 | ✅ 工具 + API；⚠️ 前端面板待做 |
| 注入预算 | Letta core memory 有字数上限 | ✅ 各来源独立预算 + 指纹去重 |
| 防污染 | 多数没有 | ✅ 外部内容标记 → 不入全局；第三方信封剥离 |
| 多主体隔离 | 按 user_id | ✅ 主人 / 微信联系人分档 |
