---
name: diagram
description: >
  画流程图、时序图、类图、状态图、ER 图、甘特图、思维导图、架构图、关系图: 写 Mermaid / Graphviz(dot) / PlantUML 源码,
  本地渲染成 PNG 或 SVG (不联网、不用显卡)。PPT、文档、报告、短视频里需要示意图时也用它。
  触发: "画个流程图" "时序图" "架构图" "UML" "plantuml" "mermaid" "graphviz" "思维导图" "甘特图" "ER 图"。
  照片/插画/动漫风格的图不归这里 (用 comfyui-image 或 cpu-image)。
aliases: [流程图, 时序图, 序列图, 架构图, 类图, 状态图, ER图, 甘特图, 思维导图, 脑图, 关系图, 拓扑图, 组织架构图, UML, uml, plantuml, PlantUML, mermaid, Mermaid, graphviz, dot 图]
---

# 画图 (Mermaid / Graphviz / PlantUML)

```bash
D="python3 ${LITECODE_HOME:-/opt/litecode}/skills/diagram/scripts/diagram.py"
$D check                                   # 看三种引擎是否可用
$D render 图.mmd -o 图.png                 # 按扩展名判断: .mmd · .dot/.gv · .puml
$D render 图.txt --type dot -o 图.svg      # SVG 可无损放大, 适合放进 PPT/网页
```

Mermaid 和 Graphviz 用自带浏览器渲染, 开箱即用; PlantUML 第一次用前跑一次 `$D setup-plantuml` (装 Java + jar, 国内源, 约 1 分钟)。

## 选哪种

| 要画的 | 首选 | 说明 |
|---|---|---|
| 流程图、决策分支 | Mermaid `flowchart` | 中文友好, 写法最短 |
| 时序图 / 接口调用 | Mermaid `sequenceDiagram` | 复杂的 (分组、激活条、注释多) 用 PlantUML |
| 类图、状态图、ER 图 | Mermaid `classDiagram` / `stateDiagram-v2` / `erDiagram` | 要严格 UML 规范用 PlantUML |
| 甘特图、时间线、思维导图、饼图 | Mermaid `gantt` / `timeline` / `mindmap` / `pie` | |
| 架构图、网络拓扑、依赖关系、节点很多的图 | Graphviz `digraph` | 自动布局最好, 可 `rankdir=LR`、`subgraph cluster_x` 分组 |
| 用例图、组件图、部署图、活动图 | PlantUML | |

## 流程

1. 先把源码写进文件 (`$WORKSPACE/diagrams/<名字>.mmd` 等), 再 render; 别把源码塞在命令行里。
2. 报 `语法/渲染错误` 时按报错行号改源码重试, 最多 3 次; 常见坑:
   - Mermaid 节点文字含括号、冒号、引号时用 `A["文字(含括号)"]` 包起来; 换行用 `<br/>`。
   - Graphviz 中文节点名要加引号 `"网关" -> "工具"`; 字体脚本会自动设为 Noto Sans CJK。
   - PlantUML 必须 `@startuml ... @enduml` (脚本会自动补)。
3. 渲染完用 `vision_ocr` (mode=describe) 看一眼: 文字有没有重叠、方向是否合理; 节点太挤就改 `rankdir` 或拆成两张。
4. 把图片路径写进回复 (网页会出预览)。要进 PPT / Word 时优先给 SVG 或 `--scale 3` 的 PNG。

## 风格

- Mermaid 主题 `--theme default|neutral|forest|dark|base`; 正式文档用 `neutral`, 深色 PPT 用 `dark`。
- 透明背景 `--bg transparent` (贴到有底色的幻灯片上)。
- 一张图只讲一件事, 节点 ≤ 15 个; 多了就拆图或用 subgraph 分组。
