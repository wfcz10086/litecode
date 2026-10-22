---
name: requesting-code-review
description: 完成任务、实现重要功能或合并前使用，用于验证工作成果是否符合要求
---

# 请求代码审查

派遣 code-reviewer 子代理来在问题扩散之前发现它们。审查者获得的是精心组织的评估上下文——绝不是你的会话历史。这样可以让审查者专注于工作成果而非你的思考过程，同时保留你自己的上下文以便继续工作。

**核心原则：** 早审查，勤审查。

## 何时请求审查

**必须审查：**
- 子代理驱动开发中每个任务完成后
- 完成重要功能后
- 合并到 main 之前

**可选但有价值：**
- 卡住时（换个视角）
- 重构之前（建立基线）
- 修复复杂 bug 之后

## 如何请求

**1. 获取 git SHA：**
```bash
BASE_SHA=$(git rev-parse HEAD~1)  # 或 origin/main
HEAD_SHA=$(git rev-parse HEAD)
```

**2. 派遣审查子代理：**

LiteCode 没有专门的 code-reviewer 类型。用 `spawn_agent`, `agent_type="tester"` (能跑 `git diff` 和测试、只读代码), task 填本目录 `code-reviewer.md` 的模板 (用 `read_file` 读出来替换占位符):
```
spawn_agent(agent_type="tester", workdir="/项目绝对路径",
            task="<填好占位符的 code-reviewer.md 全文>。只审查不改代码, 按 Critical/Important/Minor 分级输出, 每条带 文件:行号。")
```
- 改动大 (多个模块): 用 `parallel_tasks` 按模块拆给 2~3 个审查子代理, 各自只看自己那部分 diff; **加 `isolation="none"`**。
- 想边审边继续干活: 加 `background=true`, 之后 `agent_control(action="wait", id=...)` 取结果。
- 注意: tester 属"写类"子代理, 并行或后台运行时默认会在独立 git 工作树里跑, **看不到未提交的改动**。审查未提交改动时: 阻塞单个派发, 或并行时 `isolation="none"`; 后台模式一律在工作树里跑, 只适合审已提交的 SHA 范围。
- DAG 流水线里可以直接加一个 `agent_type="critic"` 的审查节点。
- 子代理只拿到你给的 task 文本, 看不到你的会话; 需求、计划文件路径、SHA 范围都要写进去。

**占位符说明：**
- `{WHAT_WAS_IMPLEMENTED}` - 你刚完成的内容
- `{PLAN_OR_REQUIREMENTS}` - 预期功能
- `{BASE_SHA}` - 起始提交
- `{HEAD_SHA}` - 结束提交
- `{DESCRIPTION}` - 简要说明

**审查者要做的事** (写进 task 里):
- `git diff --stat BASE..HEAD` 看范围, 再逐文件读 diff 和必要的上下文
- 跑相关测试 (`python3 -m pytest -q ...`), 有前端改动检查是否做过 Playwright 验收
- 重点: 正确性 > 安全 (注入、路径穿越、密钥外泄) > 与需求一致 > 可维护性; 风格问题最多列为 Minor

**3. 处理反馈：** (怎么判断反馈对不对见 receiving-code-review)
- Critical 问题立即修复
- Important 问题在继续之前修复
- Minor 问题记录下来稍后处理
- 如果审查者有误，用技术理由反驳

## 示例

```
[刚完成任务 2：添加验证功能]

你：让我在继续之前请求代码审查。

BASE_SHA=$(git log --oneline | grep "Task 1" | head -1 | awk '{print $1}')
HEAD_SHA=$(git rev-parse HEAD)

[派遣 code-reviewer 子代理]
  WHAT_WAS_IMPLEMENTED: 会话索引的验证和修复功能
  PLAN_OR_REQUIREMENTS: docs/deployment-plan.md 中的任务 2
  BASE_SHA: a7981ec
  HEAD_SHA: 3df7661
  DESCRIPTION: 添加了 verifyIndex() 和 repairIndex()，支持 4 种问题类型

[子代理返回]:
  优点：架构清晰，测试真实
  问题：
    Important：缺少进度指示器
    Minor：报告间隔使用了魔法数字 (100)
  评估：可以继续

你：[修复进度指示器]
[继续任务 3]
```

## 与工作流的集成

**子代理驱动开发：**
- 每个任务完成后审查
- 在问题叠加之前发现它们
- 修复后再进入下一个任务

**执行计划：**
- 每批（3 个任务）后审查
- 获取反馈，修复，继续

**临时开发：**
- 合并前审查
- 卡住时审查

## 红线

**绝不要：**
- 因为"很简单"就跳过审查
- 忽略 Critical 问题
- 带着未修复的 Important 问题继续推进
- 对合理的技术反馈进行争辩

**如果审查者有误：**
- 用技术理由反驳
- 展示证明其可行的代码/测试
- 要求澄清

参见模板：requesting-code-review/code-reviewer.md
