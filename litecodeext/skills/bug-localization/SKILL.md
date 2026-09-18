---
name: bug-localization
description: >
  重型项目代码 bug 定位技能: 在十万~百万行代码库里从症状/错误堆栈反推到具体文件行号.
  融合 systematic-debugging 的 5-step 流程 + large-project 的 token-省俭搜索策略.
  触发词: bug 定位, 定位错误, 追踪报错, 找 bug, 复现 bug, 线上事故, 错误溯源, 栈跟踪, 错误堆栈, root cause, production bug, 重型项目调试
---

# Bug Localization Skill

## 核心目标

**从症状 → 锁定 5 个候选文件 → 锁定 3 处可疑代码 → 锁定 1 个根因**
在大型项目里, 盲目 read 整个目录会爆 token; 这份 skill 是结构化打法.

## 5 步流程 (不能跳)

### Step 1: 解析症状 (30 秒)
收集的原始线索:
- 错误类型: TypeError / 500 / 空响应 / 超时 / 竞态
- 错误消息原文 (关键字要完整抓)
- 堆栈顶 / 底的函数名 + 文件名 (即便被 truncate 也抓)
- 发生时间 / 输入触发条件
- 是否复现 / 复现率

**输出**: "根据堆栈 X 在文件 Y, 错误消息 Z, 估计是 A/B/C 类 bug".

### Step 2: 精准搜索 (而非盲读)

用 Grep **检索短语**而不是 LS/cat:
```
# 错误消息原文 → 必定命中抛出点
Grep(pattern="exact error message fragment", path="src/")

# 堆栈顶函数名
Grep(pattern="def function_name|function function_name", path="src/")

# 符号是类/常量 → 找定义点
Grep(pattern="class MyClass|MyClass\s*=", path="src/")
```

**不准**:
- `find . -name "*.py" | xargs cat`   # 爆 token
- `ls src/` 然后 read 每个文件         # 盲打

**准**:
- 先 Grep → 得到 5 个候选 → 然后 Read 特定行号
- 如果 PROJECT_MAP.md 存在, 先读它: `read_file PROJECT_MAP.md` — 会直接告诉你模块映射

### Step 3: 阅读可疑点 (limit/offset 精读)

找到候选后不要整文件读, 用 `read_file` 的 offset/limit:
```
Read(file_path="src/module.py", offset=120, limit=40)
# 读 120-160 行, 覆盖堆栈指向的函数
```

关注:
- 输入处理 (用户数据 → 内部状态)
- 状态变更 (赋值/append/update)
- 外部调用 (http/DB/file) 的错误处理

### Step 4: 形成假设 (3 个不能多)

写下 3 个候选假设, 每个标:
- **证据**: 代码第几行支撑
- **反证**: 什么现象可证伪
- **验证方法**: 加 log / 断点 / 单测

示例:
```
H1: request.headers 缺 Auth 时 line 87 的 header["Authorization"] KeyError
  证据: 堆栈 frame 3 正好在 line 87
  反证: 其他 request 都有 Auth, 为何只此请求没
  验证: 在 line 86 加 log.debug(headers.keys())
```

### Step 5: 验证 / 修复

只有 Step 4 的假设之一被数据确认后才能动手修.
**严禁先改再跑** — 在错的假设上改代码是典型反模式.

## 与 tool_dispatch 的协作

- `get_project_map(path)` — 若项目有 map, 用它不必 Grep
- `ast_search(pattern, type='class|function|call')` — 按语法结构找
- `git log -S 'symbol'` — 谁引入的, 何时引入
- `git blame file:line` — 最后改这行的人 / 原因

## 重型项目的 token 预算

| 项目规模 | 首轮可读上限 | 策略 |
|---------|-------------|------|
| < 1 万行  | 30K tokens | Grep + 关键文件 Read |
| 1-10 万行 | 10K tokens | PROJECT_MAP + 精准 Read |
| > 10 万行 | 5K tokens  | PROJECT_MAP + ast_search + 子 agent |

## 反模式

- ❌ **一上来就 Read 堆栈里提到的整个文件** → 大文件 3000 行 = 30K token
- ❌ **写"先理解项目结构"就 ls 全部目录** → 对 bug 定位毫无帮助
- ❌ **找不到就换关键词乱 Grep** → Grep 失败通常是索引/路径问题, 不是关键词
- ❌ **改完不验证直接说"修好了"** → 必须能复现再确认消除

## 与 systematic-debugging 的关系

- `systematic-debugging` 关注**方法论** (假设-验证-迭代)
- `bug-localization` 关注**在大代码库里找到可疑点**
- 用户说 "生产环境 bug" → 加载两个 skill 并用

## 与 large-project 的关系

- `large-project` 提供 token-省俭的**通用搜索**原则
- `bug-localization` 是它的一个**特化**: 专门应对"错误发生了, 找根因"的场景
