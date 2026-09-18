# DAG · AI 版 Jenkins (极简版)

> ✅ **已落地 / 归档 (2026-09-05)**: 本"AI 版 Jenkins"方案已实现进生产 DAG。设计心智与现状以 [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) §1.3 + §3 为准,本文作立项归档。

**定位** (2026-07-30 敲定): DAG = **AI 版 Jenkins**. 就一句话 — "**一次配好, 反复跑, 结果归拢**".
心智直接照抄 Jenkins, 不搞图形化编程, 不搞数据流, 不搞事件总线. **越简单越好**.

---

## 1 · 就 3 个概念 (照抄 Jenkins)

| 我们叫 | Jenkins 里叫 | 是啥 |
|---|---|---|
| **Pipeline** | Pipeline | 配方 (存 `docs/orchestrations/*.json`) |
| **Build** | Build / Job | 一次运行 (拿一个 job_id) |
| **Stage** | Stage | Build 里的一步 |

**AI 化就 2 件事**:
1. 自然语言生成 Pipeline (已有 `/api/orchestration/generate`)
2. Build 完成后 LLM 汇总每个 Stage 结果 → 一句话结论

其他全部照 Jenkins.

---

## 2 · 就 3 个屏幕

### 屏 A · Pipeline 列表
- 左栏一列, 每条: **名字 + 上次 Build 状态点 + 上次 run 时间**
- 顶部 `+ 新 Pipeline` (空模板 / 预设 / AI 生成) 三入口
- 就这样, 不加搜索/分组/收藏

### 屏 B · Pipeline 画布 (编辑)
- drawflow 画布, 拖节点连线 (已在)
- 顶部按钮 3 个: **▶ Build Now / 💾 保存 / 🤖 AI 生成或改**
- 节点点开只有一个抽屉: tool 类型 + 参数 + `$prev.output` 占位符
- 不分 Input/Stage/Output 三色 — 全部叫 Stage
- 不搞连线自动推参 (先手填, 未来再说)

### 屏 C · Build 详情 (问题 #1 就是这个)
**上下两栏, 不搞左右三区**:

```
┌────────────────────────────────────────┐
│ Pipeline 名 · Build #12 · ▶ 运行中 5/8 │  ← 状态条
├────────────────────────────────────────┤
│ ✓ stage-1  0.3s   pptx_outline         │
│ ✓ stage-2  1.2s   web_search           │  ← Stage 列表
│ ▶ stage-3  ...    pptx_render          │     (点条目展开日志)
│ ○ stage-4         ocr                  │
├────────────────────────────────────────┤
│ [ 展开的 stage-3 日志 tail (SSE)      ] │  ← 日志区
│ [ 或 完成后 Artifact 卡片列表         ] │     (下载/预览)
└────────────────────────────────────────┘
```

- 上: 状态条 (Pipeline / Build ID / 进度 / 累计耗时)
- 中: Stage 列表 (点条目展开日志, 再点收起)
- 下: 完成后底部出 **Artifact 卡片行** (每个产物一张卡, 点开预览, 右上 ↓ 下载)

**够了**. 不搞左侧树 + 右侧详情那种三栏. 一栏够用.

---

## 3 · 分 3 期落地 (M1 就是本次)

**M1** (本次并 #1) ✅ **2026-07-30 交付**:
- 前端独立模块 `web_assets/dag_build_view.js` (223 行) — Blue Ocean 风 Build 详情面板 (Stage 列表点开日志 + Artifact 卡片行)
- `web_assets/app.js` 4 处 hook (~17 行): 生命周期挂载 / poll 更新 / done 拉 Artifacts
- 后端 `GET /api/dags/jobs/{jid}/artifacts` — 从 stages `output_preview` 抽 file path + URL
- **未做**: `logs/{stage}` 独立端点 (现状 output_preview 已扩到 8000 字符, dag_state.py:189/221; 若不够再补)

**M2** (下次): Build History
- Pipeline 卡片下方最近 10 次 Build 列表 (点进 M1 视图)
- 后端 `GET /api/dags/{name}/builds`

**M3** (以后): AI 改现有 Pipeline
- 侧栏"用自然话改这条" → LLM patch 现有 nodes
- 后端 `POST /api/orchestration/generate` 加 `patch_existing` 模式

---

## 4 · 硬约束 (不做清单)

- ❌ 不加图形化编程 (if/for/while — 要就在 tool 里写 shell)
- ❌ 不加事件总线 (要触发就 cron/webhook)
- ❌ 不加变量作用域 (Stage 参数直接引 `$stage_id.output`, 只一层)
- ❌ 不加子 Pipeline 嵌套 (复用就在 tool 层写脚本)
- ❌ 不加并行组高级 UI (`depends_on` 拓扑够用)
- ❌ 不改术语中文化 (代码/API 层保留 dag/job/step, UI 文案是否叫 Pipeline/Build/Stage 后期看情况)

**极简哲学**: 每加一个概念前先问 — Jenkins 有吗? 没有就不加.
