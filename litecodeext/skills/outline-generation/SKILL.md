---
name: outline-generation
description: >
  大纲驱动的长文档生成技能：先生成大纲→再逐章并行写作→最后合并整合。
  适用于报告、论文、小说、商业方案、技术文档等长文（>3000字）生成。
  触发词：写报告、写论文、写小说、生成长文、写文章、写方案、大纲、章节。
---

# Outline Generation Skill

**核心能力**: 先用 LLM 生成结构化大纲 → spawn_agent(pipeline) 逐章写作 → 合并为完整文档

## 使用场景

- 研究报告 / 调研报告 / 分析报告
- 学术论文 / 毕业论文
- 小说 / 长篇故事
- 商业计划书 / 策划方案
- 技术文档 / 使用手册

## 核心工作流

```
1. 识别文档类型（报告/论文/小说/商业/技术）
2. 生成结构化大纲（标题 + 各章提示词）
3. spawn_agent 逐章生成内容
4. 合并整合 → write_file 输出完整文档
```

## 实现步骤

### Step 1: 确定文档类型和章节规模

根据用户需求判断：

| 类型 | 关键词 | 章节数 | 温度 |
|------|-------|--------|------|
| 论文(thesis) | 论文/学术/文献综述 | 5-7章 | 0.3 |
| 报告(report) | 报告/分析/调研 | 4-6章 | 0.4 |
| 小说(novel) | 小说/玄幻/穿越 | 5-10章 | 0.8 |
| 商业(business) | 商业计划/BP/融资 | 5-7章 | 0.5 |
| 技术(technical) | 技术文档/API/手册 | 4-6章 | 0.2 |

### Step 2: 生成大纲

**不要自己编大纲，必须用 LLM 生成！** 构建如下 prompt：

```
你是专业的长文档大纲规划师。请根据以下需求生成详细的文档大纲：

【用户需求】
{user_request}

【参考资料】
{search_results_or_context}

请严格按以下格式输出：

===OUTLINE_START===
title=文档标题
summary=文档概述(150-300字)
===CHAPTERS===
title-1=第一章标题
prompt-1=本章核心内容和重点(50字)

title-2=第二章标题
prompt-2=本章核心内容和重点(50字)

...
===OUTLINE_END===

要求：
1. 章节数量控制在{chapter_count}章
2. 章节间逻辑清晰，内容不重复
3. 每章提示词简明扼要
4. 【重要】不要在任何章节中要求生成"总结"或"小结"
```

### Step 3: 解析大纲

```python
import re

def parse_outline(text):
    """解析 LLM 生成的大纲"""
    result = {"title": "", "summary": "", "chapters": []}
    
    # 提取 title 和 summary
    m = re.search(r'title=(.+)', text)
    if m: result["title"] = m.group(1).strip()
    
    m = re.search(r'summary=(.+?)(?=\n===|\ntitle-)', text, re.S)
    if m: result["summary"] = m.group(1).strip()
    
    # 提取章节
    for m in re.finditer(r'title-(\d+)=(.+?)(?:\n)prompt-\1=(.+?)(?=\ntitle-|\n===|$)', text, re.S):
        result["chapters"].append({
            "num": int(m.group(1)),
            "title": m.group(2).strip(),
            "prompt": m.group(3).strip(),
        })
    
    return result
```

### Step 4: 逐章生成内容

```python
# 方式 A: 用 spawn_agent pipeline（章节之间有依赖）
spawn_agent(
    pipeline_tasks=[
        {
            "task": f"写文章《{title}》的第1章：{ch1_title}\n要求：{ch1_prompt}\n字数：1500-2500字",
            "agent_type": "writer",
            "label": f"第1章: {ch1_title}"
        },
        {
            "task": f"续写第2章：{ch2_title}\n要求：{ch2_prompt}\n衔接上文，字数：1500-2500字",
            "agent_type": "writer",
            "label": f"第2章: {ch2_title}"
        },
        # ...
    ]
)

# 方式 B: 用 spawn_agent DAG（章节之间独立，前后并行）
spawn_agent(
    dag_tasks=[
        {"step_id": "ch1", "task": "写第1章...", "agent_type": "writer", "label": "第1章"},
        {"step_id": "ch2", "task": "写第2章...", "agent_type": "writer", "label": "第2章"},
        {"step_id": "ch3", "task": "写第3章...", "agent_type": "writer", "label": "第3章"},
    ]
)
```

### Step 5: 合并输出

```python
# 合并所有章节为完整文档
parts = []
parts.append(f"# {outline['title']}\n")
parts.append(f"> {outline['summary']}\n")
parts.append("---\n")

for i, chapter in enumerate(chapters_content):
    parts.append(f"## {outline['chapters'][i]['title']}\n")
    parts.append(chapter['content'])
    parts.append("\n")

final = "\n".join(parts)
# write_file 输出
```

## 各文档类型的写作要求

### 论文类
- 使用第三人称，避免"我认为"
- 每个结论必须有数据或文献支持
- 结构：引言→文献综述→方法→结果→讨论→结论

### 报告类
- 数据驱动，用具体数据支撑观点
- 结构：背景→现状→分析→建议
- 先结论后论据的"执行摘要"风格

### 小说类
- 人物塑造要有性格特点和成长轨迹
- 每章有冲突点或悬念
- "显示而非告知"（Show, Don't Tell）
- 禁止：套路化开头、AI 腔调、脸谱化角色

### 商业文档
- 市场分析要有数据基础
- 回答"为什么是现在"和"为什么是你们"
- 风险分析要客观，不过度包装

### 技术文档
- 所有代码示例必须可运行
- 每个步骤要完整，不跳步
- 提供"快速开始"和"高级用法"两个层次

## 注意事项

1. **大纲是关键**：大纲质量决定最终文档质量，必要时重新生成
2. **章节提示词要具体**：不要写"介绍背景"，要写"分析2024年新能源汽车市场规模、增长率和主要玩家"
3. **字数控制**：每章 1500-3000 字，总文档根据需求调整
4. **不要在每章加总结**：总结段放在最后一章或单独的结语
5. **保持风格一致**：所有章节的语气、用词风格要统一
6. **衔接过渡**：章节之间要有逻辑承接，不是孤立的段落
