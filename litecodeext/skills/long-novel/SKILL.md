---
name: long-novel
description: >
  长篇小说工程化架构 (10 万~100 万字 / 30-300 章). 核心: 用"世界书 + 角色档案 DB +
  滚动摘要 + 章节文件 + 项目看板"组合控制 context. 支持 中断恢复 / 任意章续写 /
  指定章修改 / 风格优化. 每次新 session 一眼看懂状态.
  触发词: 写小说, 连载, 百万字, 长篇, 章节小说, 网文, 续写, 改章, 优化小说.
---

# 长篇小说工程化架构 (v1.1 升级)

## 为什么这是"工程", 不是"写作"

100 万字 = 300 章 = 6-12 个月多 session 持续更新。单次 context 装不下全文,
模型忘得很快, 人设会漂, 武功会忘。必须用**外部档案系统**替代 LLM 记忆, 让
agent "通过读文件" 重建上下文, 像程序员读代码仓库一样。

## 目录结构 (每本小说独立目录, 一眼看全)

```
workspace/novel_{name}/
│
├── 📋 PROJECT_BOARD.md    ← ⭐ 一眼看全进度 / 下一步 / 已知问题 (每次 session 开头必看)
├── 📖 README.md           ← ⭐ 本书说明: 如何续写 / 如何改 X 章 / 如何优化文风
├── 🔄 .resume.md          ← ⭐ 3 条命令恢复工作 (load_skill / read state / 开始写)
├── 🛠 novel_state.sh      ← ⭐ 跑一下看状态 (shell 脚本)
│
├── world_bible.md         世界观圣经 (~2000 字压缩版)
├── outline.json           完整大纲 (章节 → 核心事件). 不可轻改 — 改要走 analyst
├── style_guide.md         文风守则 (ch1-3 后 critic 提炼)
│
├── state.json             运行状态: current_chapter / recent_summary (最近 3 章 1500 字)
├── characters.db          SQLite FTS5 角色档案 + 事件表
├── timeline.md            事件时间线 (每章 1 行)
├── memory_links.md        本小说与主记忆 L1 的关联点
│
├── chapters/              正文
│   ├── 001.md ~ 300.md
│   └── .ckpt/             每章写完存一份 (回滚用)
│       ├── 005_v1.md      ch5 第 1 版
│       └── 005_v2.md      ch5 修改后 v2 (带说明)
│
├── l2_summary/            每 10 章压 500 字摘要
│   ├── 001-010.md
│   ├── 011-020.md
│   └── ...
│
└── edit_log.md            所有"改 chX" 操作的记录: 改了啥 / 影响哪些章
```

### 关键文件详解

#### `PROJECT_BOARD.md` (最重要, 每次打开必看)

```markdown
# 《书名》项目看板

- **当前章节**: 第 37 章 / 共 80 章
- **总字数**: 11.2 万 / 目标 24 万
- **上次更新**: 2026-04-18 15:30 (本次 session: ch36-37)
- **下次目标**: 写 ch38 (主角下山历练)

## 状态
- ✅ 前 36 章完成
- 🟡 ch37 刚写完, 还没跑 anti-ai-tell-audit
- 🔴 ch15 读者反馈节奏慢, 待改
- 🔴 characters.db 里"李莫愁" 武功前后不一致, 待修

## 本次 session 要做
- [ ] 跑 ai_tell_audit.py chapters/037.md
- [ ] 写 ch38 (outline 第 38 条: 下山救婴)
- [ ] 修 李莫愁 武功一致性

## 已知风险
- outline 第 50 章前后, 主线可能需要调整 (伏笔太多, 圆不回来)
- 女配 陆无双 人设漂移, 待 critic 审查

## 快速命令
```
cat state.json                        # 看当前状态
./novel_state.sh                      # 状态摘要 + 统计
ls chapters/.ckpt/                    # 看所有回滚点
sqlite3 characters.db ".schema"       # 看角色表结构
```
```

#### `README.md` (告诉接手 agent / 人 "如何操作")

```markdown
# 如何使用这个小说项目

## 续写 (最常见)
1. cat PROJECT_BOARD.md       # 看进度
2. load_skill novel-common + novel-{题材}
3. read_file state.json       # 最近 3 章摘要
4. read_file outline.json     # 看下一章要写啥
5. spawn_agent(writer, task="按 outline 写第 N 章", context=<state+outline>)
6. 写完立刻: update state.json / append timeline.md / update characters.db

## 改某章 (Ch X)
1. cat chapters/X.md          # 看原文
2. cat edit_log.md             # 看这章之前改过啥
3. cp chapters/X.md chapters/.ckpt/X_vN.md   # 备份
4. 改完 append edit_log.md, 说明改动 + 可能影响的后续章
5. 如果改动大 (如人物死而复生), 更新 characters.db + 后续章要 critic 过一遍

## 优化文风
1. 选 3-5 章 sample chapter
2. spawn_agent(critic, task="对比 sample chapter, 找出风格漂移", context=<style_guide.md>)
3. critic 输出 [CRITICAL:] 就按建议改
4. 更新 style_guide.md

## 新手入口 (从 ch1 开始重写某角色)
1. sqlite3 characters.db "SELECT chapters FROM characters WHERE name='X'"
   → 拿到出场章列表
2. 逐章 read, 找出不一致
3. 用 patch_file 精修

## 中断后恢复
bash .resume.md  # 本质是 cat .resume.md 看指示
```

#### `.resume.md` (3 行命令恢复)

```markdown
# 中断后恢复 (照这个做)

1. cat PROJECT_BOARD.md
2. load_skill long-novel + novel-common + novel-{{GENRE}}
3. read_file state.json   # 继续从 current_chapter+1 写

# 如果要改章, 看 README.md 的 "改某章" 小节
```

#### `novel_state.sh` (shell 一键查状态)

```bash
#!/bin/bash
# novel_state.sh — 打印本小说项目状态摘要

cd "$(dirname "$0")"

echo "=== 项目概况 ==="
if [ -f state.json ]; then
    python3 -c "
import json
s = json.load(open('state.json'))
print(f'  当前章: {s.get(\"current_chapter\", 0)}')
print(f'  总字数: {s.get(\"total_words\", 0)}')
print(f'  最后更新: {s.get(\"last_update\", \"?\")}')
"
fi

echo ""
echo "=== 章节文件 ==="
if [ -d chapters ]; then
    COUNT=$(ls chapters/*.md 2>/dev/null | wc -l)
    LATEST=$(ls chapters/*.md 2>/dev/null | tail -1)
    echo "  已写 $COUNT 章"
    [ -n "$LATEST" ] && echo "  最新: $LATEST ($(wc -c < "$LATEST") 字节)"
fi

echo ""
echo "=== 角色档案 ==="
if [ -f characters.db ]; then
    sqlite3 characters.db "SELECT COUNT(*) FROM characters" | xargs echo "  角色数:"
fi

echo ""
echo "=== 回滚点 ==="
if [ -d chapters/.ckpt ]; then
    ls chapters/.ckpt/ | wc -l | xargs echo "  .ckpt 备份数:"
fi

echo ""
echo "=== 待办 (PROJECT_BOARD.md 里的 🔴) ==="
grep '🔴' PROJECT_BOARD.md 2>/dev/null | head -5
```

## 每章工作流 (升级版)

```
┌─ pre-load (≤ 8000 字) ─────────────────────────┐
│  1. cat PROJECT_BOARD.md                       │
│  2. read_file state.json    (current + 摘要)  │
│  3. read outline.json [N]   (本章要点)         │
│  4. sqlite3 characters.db  (本章出场角色档案)   │
│  5. read 最近 2 个 l2_summary                   │
│  6. load_skill novel-common + novel-{题材}      │
└────────────────────────────────────────────────┘
       ↓ spawn_agent(writer)
┌─ writer 输出 ≤ 4000 字 ────────────────────────┐
│  # 第 N 章: 标题                                │
│  正文...                                        │
└────────────────────────────────────────────────┘
       ↓ post-process (同 session, 强制)
┌─ 写完立刻 8 步 ────────────────────────────────┐
│  1. write_file chapters/{N:03d}.md              │
│  2. cp chapters/{N:03d}.md chapters/.ckpt/{N:03d}_v1.md  (回滚点)   │
│  3. head -1 + wc 中文字数 自检 (章号 / 字数)   │
│  4. execute_shell: ai_tell_audit.py             │
│     → 不过 exit 1, 主 agent 要回炉              │
│  5. update state.json: current += 1, summary    │
│  6. update characters.db (新角色 / 状态变化)    │
│  7. append timeline.md 一行事件                 │
│  8. update PROJECT_BOARD.md 进度                │
└────────────────────────────────────────────────┘
       ↓ 每 10 章
┌─ 压缩 + 回顾 ────────────────────────────────┐
│  1. 前 10 章摘要 → l2_summary/{S:03d}-{E:03d}.md │
│  2. state.json.recent_summary 滚动               │
│  3. spawn_agent(critic, context=最近 10 章) 审查 │
│     找人设漂移 / 武功忘记 / 时间线错位           │
│  4. critic 说有 [CRITICAL:] → PROJECT_BOARD 加 🔴 │
└────────────────────────────────────────────────┘
```

## characters.db schema (定版)

```sql
CREATE TABLE characters (
  name TEXT PRIMARY KEY,
  identity TEXT,
  appearance TEXT,
  personality TEXT,
  first_chapter INTEGER,
  last_chapter INTEGER,
  status TEXT,                  -- 活/死/失踪/闭关/化神
  relationships TEXT,           -- JSON {"其他角色": "关系"}
  skills TEXT,                  -- JSON ["内功: 九阴真经", "剑法: 独孤九剑"]
  chapters TEXT                 -- JSON [1,3,5,8,...]
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chapter INTEGER,
  event TEXT,
  characters TEXT,              -- JSON ["张三","李四"]
  location TEXT,
  timestamp TEXT                -- 本书内时间 (如 "景朝 3 年春")
);

CREATE VIRTUAL TABLE char_fts USING fts5(name, identity, personality, skills);

-- v1.1: 修改日志表
CREATE TABLE edit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chapter INTEGER,
  action TEXT,                  -- "rewrite" / "patch" / "delete"
  reason TEXT,
  before TEXT,                  -- 改前摘要
  after TEXT,                   -- 改后摘要
  impact TEXT,                  -- 影响的后续章号 (JSON)
  ts INTEGER
);
```

## 初始化脚本 (spawn_agent 首次调用)

当用户说 "写一本《xxx》小说" 时, **第一步不写正文**, 而是建项目骨架:

```bash
mkdir -p workspace/novel_{name}/chapters/.ckpt
mkdir -p workspace/novel_{name}/l2_summary
cd workspace/novel_{name}

# 1. PROJECT_BOARD.md (初版)
cat > PROJECT_BOARD.md << 'EOF'
# 《{name}》项目看板

- **当前章节**: 0 / 计划 80
- **总字数**: 0 / 目标 24 万
- **题材**: {genre}

## 状态
- 🟡 项目刚创建, 待写 outline
- 🟡 待写 world_bible

## 本次 session 要做
- [ ] analyst 出 outline
- [ ] analyst 出 world_bible
- [ ] writer 写 ch1 定调
- [ ] critic 提炼 style_guide

## 快速命令
./novel_state.sh
EOF

# 2. README.md / .resume.md / novel_state.sh 用上面模板
# 3. state.json 初始化
cat > state.json << 'EOF'
{"current_chapter": 0, "total_words": 0, "last_update": null, "recent_summary": ""}
EOF

# 4. characters.db 初始化
sqlite3 characters.db < /path/to/schema.sql

# 然后才是 analyst 出 outline / world_bible
spawn_agent(analyst, task="出《{name}》完整 outline.json + world_bible.md")
```

## 任意章回滚 (用户说 "ch15 写崩了重写")

```bash
# 1. 查看当前 ch15 和所有备份
ls chapters/.ckpt/015_v*

# 2. 选一个回滚 (或重写)
cp chapters/.ckpt/015_v1.md chapters/015.md   # 回滚

# 3. append edit_log.md
echo "- [2026-04-18] ch15 回滚到 v1 (原因: 节奏崩), 影响 ch16-20 需审查" >> edit_log.md

# 4. 之后 ch16+ 要 critic 重审有没有依赖被回滚内容的矛盾
```

## 改某角色 (用户说 "李莫愁从 ch20 起变温柔")

```bash
# 1. 查出李莫愁出场章
sqlite3 characters.db "SELECT chapters FROM characters WHERE name='李莫愁'"
# → 假设返回 [14, 18, 20, 25, 30]

# 2. 改 characters.db 本身 (status/relationships/personality)

# 3. 从 ch20 起读每章找李莫愁戏份, patch_file 修改

# 4. append edit_log.md 记录, 更新 timeline.md
```

## ⚠️ 禁止 (v1.0 已踩过的坑)

- ❌ 一次写 10+ 章 (模型到第 5 章就套模板)
- ❌ 把完整 outline 全塞 writer (只给 outline[N])
- ❌ 跳章写 (必须 current + 1)
- ❌ 不 update state 写下一章 (必崩)
- ❌ characters.db 用 ORM 新建表 (schema 固定, 直接 sqlite3)
- ❌ 删除 .ckpt (回滚唯一凭据)

## 必做

- ✅ 每章 8 步 post-process 不能省
- ✅ 每 10 章一次 critic 回顾
- ✅ 新角色立刻写 characters.db
- ✅ 改章必须备份 .ckpt
- ✅ PROJECT_BOARD.md 每 session 首尾都看

## 触发示例 (对话流)

用户: "继续写这本小说"
→ agent:
   ```
   cat workspace/novel_*/PROJECT_BOARD.md | head
   # 发现上次 ch36 完成, 下次 ch37
   bash workspace/novel_*/.resume.md  (看恢复步骤)
   load_skill long-novel + novel-common + novel-xuanhuan
   read_file state.json
   read_file outline.json → outline[37]
   sqlite3 characters.db  (查本章角色)
   spawn_agent(writer, task="写第 37 章: {outline[37].summary}")
   → 写完 post-process 8 步
   ```

用户: "ch15 节奏太慢, 改一下"
→ agent:
   ```
   cat chapters/015.md      # 看原文
   cat edit_log.md          # 看改过啥
   cp chapters/015.md chapters/.ckpt/015_v2_before_pacing_fix.md
   # 开始改, 可能是缩减铺垫段, 或加入冲突
   patch_file chapters/015.md ...
   append edit_log.md "- [{日期}] ch15 加速节奏, 削减 500 字铺垫, 新增冲突"
   # 看影响: ch16-20 有没有依赖 ch15 原来节奏的
   grep -l "ch15" chapters/016.md chapters/017.md chapters/018.md
   ```

用户: "读一下最近 5 章看我的风格"
→ agent:
   ```
   cat chapters/0{32..36}.md | wc  # 确认字数
   # 跑 ai_tell_audit 看 AI 味
   for f in chapters/0{32..36}.md; do
     python3 ai_tell_audit.py "$f"
   done
   spawn_agent(critic, task="读这 5 章, 提炼风格摘要, 找漂移点")
   → 更新 style_guide.md
   ```
