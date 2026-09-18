---
name: deep-report
description: >
  深度报告 / 白皮书 / 研究报告 / 行业分析写作. 特征: 5000-20000 字 / 多章节 /
  边写边搜 / 每主要论点必须引用 / 数据可核验 / 图表推荐.
  触发词: 深度报告, 研究报告, 白皮书, 行业分析, 调研报告, 技术综述, 市场分析, 综述.
---

# 深度报告写作规范 (边查边写 + 自审回炉)

## 与普通长文的区别
| 维度 | 普通长文 | 深度报告 |
|---|---|---|
| 字数 | 2000-5000 | 5000-20000 |
| 数据 | 少 | 必须密集 (每章 3+ 数字) |
| 引用 | 可选 | **每论点 ≥ 1 条** |
| 查资料 | 写前查 | **边写边查** |
| 审核 | 一次 | 至少 2 轮 (草稿 → critic → 重写) |
| 图表 | 少 | 推荐 |

## 4 阶段工作流

### Phase 1 — 立题 (不写正文)
```
1. 和用户确认: 主题 + 受众 + 字数 + 截止时间 + 数据来源偏好
2. spawn_agent(researcher) 做背景调研
   - web_search 3-5 个核心关键词
   - 汇总权威来源 (政府 / 机构 / 论文)
3. 输出: outline.md — 大纲 + 每章要点 + 数据需求清单
4. 写入 memory (L1): 本报告题材 + 核心观点 + 避讳
```

### Phase 2 — 素材库 (data.json)
```
建立 data.json, 所有查到的数据都存这里:
{
  "facts": [
    {"claim": "2024 全球 AI 市场规模 3600 亿美元", "source": "Gartner 2024", "url": "...", "date": "2024-10"},
    ...
  ],
  "quotes": [
    {"speaker": "李彦宏", "content": "AI 原生应用是终局", "source": "百度世界 2024", "url": "..."},
    ...
  ],
  "stats": [
    {"metric": "大模型参数", "value": "1 万亿", "source": "OpenAI", "year": 2024}
  ]
}

每段正文引用任何数据/观点, 必须在 data.json 里有 entry. 否则拒绝写入.
```

### Phase 3 — 正文写作 (每章独立 writer 会话)
```
写每章前:
1. read_file outline.md → 知道本章要点
2. grep data.json → 找本章相关的 5-10 条数据
3. web_search 补充查 (writer 有 web_search 权限)
4. write 正文 2000-4000 字

写完每章立刻:
5. 用 anti-ai-tell-audit 自查  (见下)
6. spawn_agent(critic) 审内容准确性
7. 如 [CRITICAL:] 或自查不合格 → 重写本章
```

### Phase 4 — 合并 + 收尾
```
1. 拼接各章 → report.md
2. 生成摘要 / 目录 (前 300 字核心观点)
3. 生成参考文献列表 (从 data.json 自动生成)
4. 可选: 生成图表 (execute_shell + matplotlib)
```

## 报告结构模板
```markdown
# {报告题目}

## 摘要 (Abstract, 300 字)
- 1 句话背景 + 研究方法 + 核心结论 + 意义

## 1. 引言 / 背景
- 问题陈述, 为什么重要
- 相关研究概述
- 本报告结构

## 2. 现状分析
- 市场规模 / 技术现状 / 关键事件
- ≥ 5 个数据引用

## 3. 核心论点 / 发现
- 3-5 个子章节, 每节一个论点
- 每个论点: 现象 → 数据支撑 → 案例 → 归纳

## 4. 趋势 / 展望
- 短期 / 中期 / 长期
- 基于当前数据的合理推演

## 5. 风险 / 挑战
- 至少 3 个未解问题
- 不一味乐观, 要指出隐患

## 6. 建议 / 结论
- 面向目标读者的行动建议
- 不空泛, 可操作

## 参考文献
- 按引用顺序, URL + 访问时间
```

## 边写边搜的规则

**每段正文**:
- 出现 ≥ 1 个数字 → 必须引用 (search + 标 source)
- 出现 ≥ 1 个名人/公司/事件 → 必须核实 (search)
- 出现 ≥ 1 个技术术语 → 必须定义准确 (search 确认)

**发现不一致**: 主动查多个来源对比, 以"权威优先 (政府 > 机构 > 媒体 > 博客)"

## 记忆 / 状态管理

本报告的记忆结构 (放 workspace/report_{topic}/):
```
report_{topic}/
├── outline.md         结构大纲 (Phase 1 产出, 不可随意改)
├── data.json          素材库 (持续更新, 不可删旧条目)
├── memory.md          报告状态 (current_chapter + 核心观点 + 已发现的矛盾)
├── chapters/
│   ├── 01_intro.md
│   ├── 02_现状.md
│   └── ...
├── final.md           最终合并
└── critic_log.md      critic 给出的修改建议 (归档)
```

**跨会话续写**: 新 session 开始时先 read_file outline.md + memory.md + 最近一章, 快速恢复上下文。

## 与 long-novel skill 的协作
深度报告也属于"长文", 可共用 long-novel skill 的架构:
- `outline.md` ≈ long-novel 的 `outline.json`
- `memory.md` ≈ long-novel 的 `state.json`
- `data.json` = 报告特有 (小说没有)
- critic 环 (见下) = 两者通用

## critic 回炉机制 (重点)

```
writer 写完 Chapter N →
spawn_agent(critic) 审查本章:
  - 数据引用是否 ≥ 目标密度 (每 500 字 ≥ 1 条)
  - 论点是否自圆 (没逻辑断裂)
  - 有无 [无引用断言] / [数据矛盾] / [年份错误]
  - 返回 [OK] 或 [CRITICAL: 具体问题]
如 [CRITICAL:] → writer 看建议重写本章 (max 2 次)
如仍不过 → 上报主 agent 交用户决定
```

## 套话黑名单 (报告类)
- "综上所述 / 总而言之 / 值得一提的是 / 不得不说 / 必须指出"
- "随着 X 的发展"  (空话)
- "越来越多的人"
- "据悉" (没有具体来源)
- "大致可以分为" (不精准)
- "众所周知" (冒昧)
- "专家表示" + 没名字
- "有研究表明" + 没引用

## 质量自查 (写完 must-do)
```bash
python3 -c "
import re
t = open('report.md').read()
# 字数
cn = len(re.findall(r'[\u4e00-\u9fff]', t))
# 数字密度 (粗略)
nums = len(re.findall(r'[0-9]+(?:\.[0-9]+)?%?', t))
# 引用密度
citations = len(re.findall(r'\[[^\]]+\]|据.*报道|[A-Z][a-zA-Z]+\s*\(\d{4}\)', t))
# 套话
cliches = sum(t.count(w) for w in ['综上所述','总而言之','值得一提的是','越来越多的人','据悉','众所周知','专家表示'])
print(f'字数 {cn} / 数字 {nums} / 引用 {citations} / 套话 {cliches}')
print(f'每千字: 数字 {nums*1000/cn:.1f} | 引用 {citations*1000/cn:.1f} | 套话 {cliches*1000/cn:.1f}')
assert cn >= 5000, '字数不足 5000'
assert citations >= cn // 500, '引用密度 < 每 500 字 1 条'
assert cliches < cn // 3000, '套话密度超标'
print('✅ 质量检查通过')
"
```
