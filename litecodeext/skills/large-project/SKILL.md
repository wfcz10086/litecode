---
name: large-project
description: "处理大型代码库、复杂项目调试与重构。触发词: 大型项目, 代码库, codebase, refactor, 重构, 全局搜索, 批量修改, 大文件, monorepo, debug 复杂问题, 整个项目, 所有文件"
---

# Large Project Skill

## 核心原则
**永远不要盲目 cat 整个目录**。大项目的 token 预算比小项目紧张 10 倍。

## Step 1 — 项目地图（先做，永远）

```bash
# 目录结构（2层，排除噪声）
find . -maxdepth 2 -not -path '*/\.*' -not -path '*/node_modules/*' \
       -not -path '*/__pycache__/*' -not -path '*/dist/*' | sort

# 统计文件规模
find . -name "*.py" | xargs wc -l 2>/dev/null | tail -1   # Python 总行数
find . -name "*.ts" -o -name "*.tsx" | xargs wc -l 2>/dev/null | tail -1

# 最近改动（告诉你问题在哪）
git log --oneline -20
git diff --stat HEAD~3
```

## Step 2 — 精准定位，不乱读文件

```bash
# 找定义
grep -rn "def target_function\|class TargetClass" --include="*.py" .

# 找调用链
grep -rn "target_function(" --include="*.py" . | head -30

# 找错误关键词
grep -rn "ERROR_MESSAGE" --include="*.log" . | tail -20

# 找配置项
grep -rn "CONFIG_KEY" --include="*.json" --include="*.yaml" --include="*.env" .

# 只读文件的特定行段（不 cat 整个文件）
sed -n '100,150p' large_file.py
```

## Step 3 — 修改策略

### 小改动 → patch_file（首选）
```
patch_file: old_str="原有代码片段（唯一）", new_str="新代码"
```
- 每次改一处，改完验证
- old_str 必须在文件中唯一出现

### 大改动 → 先读后写
```
1. read_file filepath lines="1:50"   # 只读相关段
2. 确认内容后 write_file（完整替换）
3. 验证：运行测试或 grep 关键改动点
```

### 批量改动 → 脚本化
```bash
# 批量替换（sed，比逐文件 patch 快）
find . -name "*.py" -exec sed -i 's/old_pattern/new_pattern/g' {} \;

# 验证改动
git diff --stat
git diff -- path/to/changed_file.py | head -60
```

## Step 4 — 验证闭环

每次修改后必须验证，不能只改不测：

```bash
# Python
python -m pytest tests/ -x -q 2>&1 | tail -30
python -c "import module; print('import ok')"

# Node
npm test -- --testPathPattern=changed_module 2>&1 | tail -30

# 语法检查
python -m py_compile changed_file.py && echo "syntax ok"
node --check changed_file.js && echo "syntax ok"

# 运行时快速冒烟
python changed_file.py --help 2>&1 | head -10
```

## Step 5 — 大项目 Debug 流程

```
1. 复现错误（最小命令）
2. git log 找最近变更（可能是引入 bug 的 commit）
3. grep 错误信息 → 定位文件+行号
4. read_file 只读问题区域（不读全文件）
5. 分析根因 → patch_file 修复
6. 验证修复（运行原来失败的命令）
7. 如果还有问题，扩大 grep 范围
```

## Warning vs Error 处理规则

- `returncode == 0` + stderr 有输出 → **Warning，不是错误**，继续执行
- `DeprecationWarning`, `UserWarning`, `ResourceWarning` → 忽略，除非用户关心
- `[warnings]:` 前缀的输出 → 命令成功，warnings 只是提示
- `returncode != 0` → 真正失败，需要处理

## 内存友好的读取模式

```python
# 读文件头部了解结构
read_file filepath lines="1:30"

# 读文件尾部看最新内容
read_file filepath lines="-50:-1"   # 如果支持负数
# 或
execute_shell "tail -50 filepath"

# 分段读大文件
read_file filepath lines="200:280"
```

## 不要做的事

- 不要 `cat` 超过 500 行的文件（用 `head`/`sed -n` 代替）
- 不要同时修改 5 个以上文件（分批，每批验证）
- 不要在没有 `grep` 定位的情况下猜文件位置
- 不要忽略 `git diff` 验证步骤
