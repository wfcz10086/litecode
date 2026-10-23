---
name: longtext-processing
description: >
  长文本处理技能：文档切片→逐段翻译/摘要/改写→合并输出。适用于超长文档（>5000字）
  的翻译、摘要、格式转换、内容改写等逐段处理任务。
  触发词：翻译文档、总结长文、文档切片、逐段翻译、长文本处理、批量处理、改写文档。
aliases: ["长文本", "总结长文", "提炼总结", "翻译文档", "翻译长文", "逐段翻译", "批量翻译", "文档切片", "改写文档", "translate document", "summarize long", "摘要"]
---

# LongText Processing Skill

**核心能力**: 将超长文档切片 → 用 spawn_agent 并行处理每个切片 → 按序合并输出

## 使用场景

- 文档翻译（中→英、英→中等）
- 长文档摘要/总结
- 格式转换（Markdown→JSON、HTML→纯文本等）
- 内容改写/润色
- 任何需要"逐段处理+保持顺序"的任务

## 核心工作流

**严格按这个流程执行，不要跳步：**

```
1. 先量大小: wc -c / wc -l (不要 read_file 整篇读进上下文, 只用 read_file lines='1:50' 看开头格式)
2. 用 Python 脚本切片（句子边界对齐）, 切片落盘到 workspace/chunks/
3. spawn_agent(parallel_tasks) 并行处理: 任务里只给切片文件路径 + 输出路径, 不内联切片正文
4. 按序合并所有结果文件 → 脚本写最终输出, 回复里只给摘要和路径
```

> **整本书 / 1 万字以上的翻译、要保持 docx/epub/md/srt 格式、要全书术语统一** → 改用 `long-translate` 技能
> (脚本并发 + 断点续传 + 术语表 + 质检报告), 不要走下面的 spawn_agent 流程。
>
> 文档本身是几十 MB 以上的日志/表格而不是"文章"时, 改用 `large-file-handling` 技能 (搜索/脚本统计), 不要切片逐段读。

## 实现步骤

### Step 1: 读取并切片

```python
# write_file: split_document.py
import re, json, sys

def split_text(text, chunk_size=3000):
    """按句子边界切片，避免在句子中间断开"""
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    pos = 0
    sentence_ends = set('。！？.!?；;')
    
    while pos < len(text):
        end = min(pos + chunk_size, len(text))
        if end >= len(text):
            chunks.append(text[pos:])
            break
        
        # 向前找句子边界
        best = -1
        for i in range(end - 1, max(pos, end - chunk_size // 3), -1):
            if text[i] in sentence_ends:
                best = i + 1
                break
        
        if best <= pos:
            # 找不到句子边界，找换行符
            nl = text.rfind('\n', pos, end)
            best = nl + 1 if nl > pos else end
        
        chunks.append(text[pos:best].strip())
        pos = best
    
    return [c for c in chunks if c.strip()]

# 读取文档 (脚本里读, 不经过模型上下文)
import os
OUT = "/tmp/litecode_workspace/chunks"
os.makedirs(OUT, exist_ok=True)
text = open(sys.argv[1], 'r', encoding='utf-8', errors='replace').read()
chunks = split_text(text, chunk_size=3000)

# 保存切片信息
meta = {"total": len(chunks), "source": sys.argv[1]}
for i, chunk in enumerate(chunks):
    open(f"{OUT}/chunk_{i:03d}.txt", "w", encoding="utf-8").write(chunk)
    meta[f"chunk_{i}"] = len(chunk)

json.dump(meta, open(f"{OUT}/chunks_meta.json", "w"), ensure_ascii=False)
print(f"切片完成: {len(chunks)} 个片段，总长度 {len(text)} 字符")
```

执行：
```bash
python3 split_document.py /path/to/source.txt
```

### Step 2: 并行处理（翻译示例）

```python
# 用 spawn_agent 并行处理, 每个 agent 处理一个切片文件 (只传路径, 正文由子代理自己 read_file)
spawn_agent(
    parallel_tasks=[
        {
            "task": "read_file /tmp/litecode_workspace/chunks/chunk_000.txt, 翻译为英文且保持格式, "
                    "write_file 写到 /tmp/litecode_workspace/chunks/result_000.txt, 只回复 '完成+字数'",
            "agent_type": "writer",
            "label": "翻译-第1段"
        },
        {
            "task": "read_file /tmp/litecode_workspace/chunks/chunk_001.txt, 翻译为英文且保持格式, "
                    "write_file 写到 /tmp/litecode_workspace/chunks/result_001.txt, 只回复 '完成+字数'",
            "agent_type": "writer",
            "label": "翻译-第2段"
        },
        # ... 每个 chunk 一个 task
    ]
)
```

**注意**: 如果切片数 > 5，分批处理（每批 3-5 个），避免并发过大。

### Step 3: 合并输出

```python
# 按序读取各切片结果，合并为完整文档
import glob

results = []
for f in sorted(glob.glob("/tmp/litecode_workspace/chunks/result_*.txt")):
    results.append(open(f, encoding="utf-8").read())

final = "\n\n".join(results)
open("/workspace/output_translated.md", "w", encoding="utf-8").write(final)
print(f"合并完成: {len(final)} 字符")
```

## 切片策略选择

| 任务类型 | chunk_size | 说明 |
|---------|-----------|------|
| 翻译 | 2000-3000 | 较小，保证翻译质量 |
| 摘要 | 4000-6000 | 较大，保留更多上下文 |
| 改写 | 3000-4000 | 适中 |
| 格式转换 | 5000-8000 | 可以更大，结构转换不需要深度理解 |

## 注意事项

1. **切片必须在句子边界**：不要在句子中间切断
2. **保持切片序号**：合并时必须按原始顺序
3. **单个切片不要太小**：< 200 字的尾部切片合并到上一个
4. **翻译任务加上下文**：每个切片前加上文的最后 1-2 句作为上下文参考
5. **分批并行**：> 5 个切片时分批处理，每批 3-5 个
6. **错误处理**：某个切片处理失败，重试 2 次后跳过，最终合并时标记
