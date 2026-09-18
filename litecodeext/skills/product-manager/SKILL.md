---
name: product-manager
description: 产品设计与研发经理专用 skill — 把模糊需求 / 灵感 / 用户抱怨 → 结构化 PRD (Product Requirements Doc) + 用户故事 + 优先级. 触发场景: 用户说"帮我写份需求文档 / 产品方案 / PRD / 功能规划 / 用户故事 / 我想做一个 XX / 需求怎么拆", 或明确点名"产品经理"/"产品设计"/"研发经理". 面向 中小型项目: 一个人 1-2 周就能落地的粒度. 强调"最小可行/MVP先跑起来", 拒绝无穷嵌套抽象.
---

# 产品设计研发经理 (Product & R&D Manager)

## 何时使用
- 用户抛出模糊灵感: "我想做个 XX", "帮我看看这个想法怎么样"
- 用户报抱怨: "现在的 XXX 太难用了 / 一坨屎 / 用户学不会"
- 团队开会前要 PRD / 一页纸 / 用户故事
- 需求太多不知先做哪个 → 优先级排序

## 输出 template

### 1. 一页纸 PRD (One-Pager)
```markdown
# 产品名: <name>
**一句话价值**: <who> 通过 <what>, 得到 <outcome>. 目前替代方案是 <status quo>, 痛点是 <pain>.

## 目标用户 (Persona)
- 主用户: <role, 场景, 现有工具>
- 反 persona: <明确不服务的人>

## 核心场景 (Golden Path)
1. 用户 <action> → 看到 <result>
2. ...
3. 完成时间目标: <N 秒/分钟>

## 功能范围
### 必做 (MVP · 2 周)
- [ ] F1: <one-line>
- [ ] F2:
### 可做 (V2)
- [ ] F3:
### 不做 (Anti-scope)
- 明确不做 X, 因为 <理由>

## 成功指标 (可量化)
- N-day retention ≥ X%
- 完成核心场景耗时 < Y 秒
- NPS ≥ Z

## 关键风险
- 技术: <什么可能做不出来>
- 用户: <什么可能没人用>
- 商业: <什么可能不赚钱 / 亏本>
```

### 2. 用户故事 (User Stories)
每条用 INVEST 原则 (Independent / Negotiable / Valuable / Estimable / Small / Testable):
```
作为 <role>,
我想 <action>,
以便 <benefit>.

验收标准:
- [ ] 场景 A: 输入 X → 输出 Y
- [ ] 场景 B: 边界条件 Z → 提示 W
- [ ] 场景 C: 出错时 → 显示 M

估工: <S/M/L>  (S=半天, M=1-3天, L=3天以上须拆)
```

### 3. 优先级矩阵 (RICE)
| 需求 | Reach | Impact (0.25-3) | Confidence (0-100%) | Effort (人天) | RICE |
|---|---|---|---|---|---|
| X | 500 | 2 | 80% | 5 | (500·2·0.8)/5 = 160 |

**排序法则**: RICE 分数从高到低, 前 3 名进入本迭代.

## 中小型项目的取舍原则
1. **一人两周原则**: MVP 必须一个人 2 周能跑起来, 超出即拆
2. **拒绝无用抽象**: 不写"可扩展框架", 写"能跑的第一版"
3. **先用后美**: 功能可用 > 界面好看 (但要保证不丑到吓人)
4. **拒绝无穷需求**: 每加一个需求, 必须删掉一个同级需求
5. **反 anti-pattern**: 不做"未来可能需要", 不做"竞品有所以我也做"

## 常见错误
- ❌ PRD 3000 字, 没人看得完 → 一页纸就够
- ❌ 用户故事写成"用户想要一个按钮" → 要写 outcome 不是 action
- ❌ 所有需求都 P0 → 没有优先级就是没有产品思维
- ❌ 只谈功能不谈成功指标 → 上线后不知道成没成

## 与其他 skill 组合
- 需要拆技术方案时 → 转 `project-manager`
- 需要设计 UI 时 → 转 `frontend-design`
- 需要验收测试时 → 转 `visual-qa-engineer` / `test-driven-development`

## Keywords
产品经理 产品设计 研发经理 需求 PRD 用户故事 user story 功能规划 MVP 优先级 RICE persona golden path anti-scope 一页纸 一句话价值 需求文档
