# Claude Skills 完整说明文档

> **适用平台**：Claude.ai / Claude Desktop / Cowork / **LiteCode** ✅  
> **打包时间**：2026-03-26  
> **技能总数**：18 个（9 Public + 9 Examples）

---

## 目录结构

```
skills-package/
├── README.md                    ← 本文件
│
├── ── Public Skills（生产可用）──
├── docx/                        文档处理
├── pdf/                         PDF 全功能处理
├── pdf-reading/                 PDF 读取专项
├── pptx/                        演示文稿处理
├── xlsx/                        电子表格处理
├── file-reading/                文件路由读取
├── frontend-design/             前端界面设计
│
├── ── Example Skills（示例/可扩展）──
├── algorithmic-art/             算法生成艺术
├── canvas-design/               视觉设计哲学
├── doc-coauthoring/             文档协作写作
├── internal-comms/              企业内部通讯
├── mcp-builder/                 MCP服务器开发
├── skill-creator/               技能创建工具
├── slack-gif-creator/           Slack GIF制作
├── theme-factory/               主题样式工厂
```

---

## 详细技能说明

### 🟢 Public Skills（生产就绪）

---

#### 1. `docx` — Word 文档处理

**触发场景**：创建/读取/编辑 `.docx` 文件、Word文档、报告、信件、备忘录  
**不适用**：PDF、Excel、Google Docs

**核心依赖**：
| 依赖 | 安装方式 | 用途 |
|------|----------|------|
| `docx` npm包 | `npm install -g docx` | 创建新文档 |
| `pandoc` | `apt install pandoc` | 读取/转换文档 |
| LibreOffice | `apt install libreoffice` | `.doc`转换、图片渲染 |
| Python `python-docx` | `pip install python-docx` | XML操作 |

**LiteCode 兼容性**：✅ **高度兼容**  
Linux环境下所有依赖均可通过包管理器安装，agent可自动调用bash完成。

---

#### 2. `pdf` — PDF 全功能处理

**触发场景**：合并/拆分PDF、添加水印、加密解密、填写表单、OCR识别  
**不适用**：与 `pdf-reading` 区分，本技能侧重**写/操作**

**核心依赖**：
| 依赖 | 安装方式 | 用途 |
|------|----------|------|
| `pypdf` | `pip install pypdf --break-system-packages` | 基础PDF操作 |
| `pdfplumber` | `pip install pdfplumber --break-system-packages` | 表格/布局提取 |
| `reportlab` | `pip install reportlab --break-system-packages` | 创建新PDF |
| `pikepdf` | `pip install pikepdf --break-system-packages` | 加密/解密 |
| `poppler-utils` | `apt install poppler-utils` | CLI工具(pdftotext等) |
| `tesseract` | `apt install tesseract-ocr` | OCR扫描件识别 |

**LiteCode 兼容性**：✅ **高度兼容**  
所有Python依赖可pip安装，CLI工具可apt安装。

---

#### 3. `pdf-reading` — PDF 读取专项

**触发场景**：读取/检查/提取PDF内容，文件不在上下文中需要从磁盘读取  
**不适用**：PDF创建、合并、拆分等写操作（用 `pdf` 技能）

**核心依赖**：
| 依赖 | 安装方式 | 用途 |
|------|----------|------|
| `pypdf` | `pip install pypdf` | 基础文本提取 |
| `pdfplumber` | `pip install pdfplumber` | 含位置信息的提取 |
| `pypdfium2` | `pip install pypdfium2` | 页面光栅化/渲染 |
| `poppler-utils` | `apt install poppler-utils` | pdfinfo, pdftotext |

**LiteCode 兼容性**：✅ **高度兼容**

---

#### 4. `pptx` — PowerPoint 演示文稿

**触发场景**：创建/读取/编辑 `.pptx` 文件，deck、slides、presentation  

**核心依赖**：
| 依赖 | 安装方式 | 用途 |
|------|----------|------|
| `python-pptx` | `pip install python-pptx` | 编辑现有PPT |
| `pptxgenjs` | `npm install pptxgenjs` | 从零创建PPT |
| `markitdown` | `pip install markitdown` | 读取PPT内容 |
| LibreOffice | `apt install libreoffice` | 缩略图生成 |
| `Pillow` | `pip install Pillow` | 图像处理 |

**LiteCode 兼容性**：✅ **高度兼容**

---

#### 5. `xlsx` — 电子表格处理

**触发场景**：打开/读取/编辑/创建 `.xlsx`、`.xlsm`、`.csv`、`.tsv` 文件  
**包含金融建模规范**：颜色编码标准、公式错误防护、文档要求

**核心依赖**：
| 依赖 | 安装方式 | 用途 |
|------|----------|------|
| `openpyxl` | `pip install openpyxl` | Excel读写 |
| `pandas` | `pip install pandas` | 数据分析 |
| `xlrd` | `pip install xlrd` | 旧版 `.xls` 读取 |
| LibreOffice | `apt install libreoffice` | 公式重算 |
| `xlsxwriter` | `pip install xlsxwriter` | 高级格式创建 |

**LiteCode 兼容性**：✅ **高度兼容**

---

#### 6. `file-reading` — 文件路由读取

**触发场景**：用户上传文件但内容不在上下文中，需要从 `/mnt/user-data/uploads/` 读取  
**本质**：一个路由调度器，根据文件扩展名决定用哪个工具读取

**核心依赖**：无额外依赖（调用其他技能的依赖）  
支持的格式：pdf, docx, xlsx, csv, json, jpg/png, zip/tar, epub, rtf, txt...

**LiteCode 兼容性**：⚠️ **部分兼容**  
LiteCode的文件路径约定可能不同（不是 `/mnt/user-data/uploads/`），但路由逻辑完全可用，修改路径前缀即可适配。

---

#### 7. `frontend-design` — 前端界面设计

**触发场景**：构建网页组件、落地页、仪表盘、React组件、HTML/CSS布局  
**特色**：包含避免"AI通用美学"的设计指南，强调视觉独特性

**核心依赖**：无（纯创意提示工程，生成代码在用户端运行）  
生成代码可能用到：React、Tailwind CSS、Motion库等（运行时依赖，非安装依赖）

**LiteCode 兼容性**：🌟 **完美兼容**  
纯SKILL.md提示技能，零依赖，直接放入即用。

---


**触发场景**：任何关于Claude API、LiteCode、Claude.ai产品的具体事实查询  
**作用**：防止用过时训练数据回答产品问题，强制查阅官方文档

**核心依赖**：无（网络访问文档即可）  
文档地址：docs.claude.com、support.claude.com

**LiteCode 兼容性**：✅ **兼容**（对使用Claude的LiteCode用户有参考价值）

---

### 🔵 Example Skills（示例技能）

---

#### 9. `algorithmic-art` — 算法生成艺术

**触发场景**：生成艺术、流场、粒子系统、代码艺术  
**输出**：`.md`（哲学文档）+ `.html`（交互查看器）+ `.js`（算法）

**核心依赖**：
- `p5.js`（CDN引入，无需安装）
- 基础 HTML/JS 环境

**LiteCode 兼容性**：✅ **兼容**（输出HTML文件在浏览器中运行）

---


**触发场景**：应用Anthropic官方品牌色彩和字体到文档/演示文稿  
**字体**：Poppins（标题）、Lora（正文）

**核心依赖**：
- `python-pptx` — PPTX样式应用
- 系统字体 Poppins、Lora（可选，有fallback）

**LiteCode 兼容性**：✅ **兼容**（主要用于Anthropic内部，普通用户可参考颜色规范）

---

#### 11. `canvas-design` — 视觉设计哲学

**触发场景**：创建海报、艺术作品、静态设计  
**输出**：`.md`（设计哲学）+ `.pdf` / `.png`

**核心依赖**：
| 依赖 | 安装方式 |
|------|----------|
| `Pillow` | `pip install Pillow` |
| `reportlab` | `pip install reportlab` |
| `cairosvg` | `pip install cairosvg` |

**LiteCode 兼容性**：✅ **兼容**

---

#### 12. `doc-coauthoring` — 文档协作写作

**触发场景**：协作写作技术文档、提案、PRD、设计文档、决策文档  
**流程**：3阶段工作流（上下文收集→精化结构→读者测试）

**核心依赖**：无（纯工作流提示）  
可选：Slack/Teams/Google Drive MCP集成（拉取上下文用）

**LiteCode 兼容性**：🌟 **完美兼容**  
LiteCode支持多channel，这个技能的工作流非常适合在消息平台上执行。

---

#### 13. `internal-comms` — 企业内部通讯

**触发场景**：撰写3P进度报告、公司通讯、FAQ、状态报告、事故报告  
**依赖子文件**：`examples/3p-updates.md`、`examples/company-newsletter.md` 等

**核心依赖**：无（纯写作提示）

**LiteCode 兼容性**：🌟 **完美兼容**（特别适合通过微信/Telegram等渠道输出内部通讯）

---

#### 14. `mcp-builder` — MCP服务器开发

**触发场景**：构建MCP(Model Context Protocol)服务器，集成外部API  
**推荐栈**：TypeScript + Streamable HTTP（远程） / stdio（本地）

**核心依赖**：
| 依赖 | 安装方式 |
|------|----------|
| Node.js 22+ | `nvm install 22` |
| TypeScript | `npm install -g typescript` |
| `@modelcontextprotocol/sdk` | `npm install @modelcontextprotocol/sdk` |
| Python FastMCP（可选） | `pip install fastmcp` |

**LiteCode 兼容性**：🌟 **强烈推荐**  
你的LiteCode配置中已有 `openclaw-weixin` 插件，这个技能可帮助构建更多自定义MCP扩展。

---

#### 15. `skill-creator` — 技能创建工具（元技能）

**触发场景**：创建新技能、优化现有技能、运行技能评估  
**功能**：完整的技能开发循环（设计→起草→测试→迭代→优化触发描述）

**核心依赖**：
- `eval-viewer/generate_review.py` 脚本（包内附带）
- Python环境

**LiteCode 兼容性**：🌟 **强烈推荐**  
可用来为你的LiteCode实例创建和优化自定义技能！

---

#### 16. `slack-gif-creator` — Slack GIF制作

**触发场景**：为Slack创建优化的动画GIF（emoji大小128×128，消息大小480×480）

**核心依赖**：
| 依赖 | 安装方式 |
|------|----------|
| `Pillow` | `pip install Pillow` |
| `pygifsicle` | `pip install pygifsicle` |
| `gifsicle` CLI | `apt install gifsicle` |

**LiteCode 兼容性**：✅ **兼容**（LiteCode支持Discord/Slack频道，GIF可直接发送）

---

#### 17. `theme-factory` — 主题样式工厂

**触发场景**：为PPT/文档/HTML应用10种预设主题样式（从Ocean Depths到Midnight Galaxy）

**核心依赖**：
- `python-pptx`（应用PPT主题）
- 依赖 `theme-showcase.pdf` 主题预览文件

**LiteCode 兼容性**：✅ **兼容**

---


**触发场景**：构建需要状态管理、路由的复杂多组件HTML Artifact  
**技术栈**：React 18 + TypeScript + Vite + Parcel + Tailwind CSS + shadcn/ui

**核心依赖**：
| 依赖 | 安装方式 |
|------|----------|
| Node.js 18+ | `nvm install 18` |
| `vite` | `npm install vite` |
| `parcel` | `npm install parcel` |
| `tailwindcss` | `npm install tailwindcss` |
| shadcn/ui | `npx shadcn-ui init` |

**LiteCode 兼容性**：⚠️ **适配较复杂**（主要为claude.ai Artifact设计，可改造为输出HTML文件）

---

## LiteCode 使用指南

### 你的配置环境
根据 `openclaw.json` 分析：
- **运行环境**：CentOS Linux，Node.js
- **模型**：本地 vLLM Qwen3.5-35B（无视觉，有文本能力）
- **Gateway端口**：18789
- **已安装插件**：`openclaw-weixin`（微信集成）

### 安装技能到LiteCode

将技能文件夹放入项目的 `.agents/skills/` 目录：

```bash
# 在你的LiteCode工作目录下
mkdir -p .agents/skills/
cp -r skills-package/frontend-design .agents/skills/
cp -r skills-package/doc-coauthoring .agents/skills/
cp -r skills-package/mcp-builder .agents/skills/
# ... 以此类推
```

或直接在LiteCode对话中粘贴技能的GitHub URL，agent会自动安装。

### LiteCode 启动方式

```bash
# 全局安装（推荐 Node 24）
npm install -g openclaw@latest

# 首次引导安装（会自动安装daemon）
openclaw onboard --install-daemon

# 直接启动 Gateway
openclaw gateway

# 重启 Gateway（修改配置后）
openclaw gateway restart

# 查看状态
openclaw status
```

基于你的配置，Gateway已绑定到 LAN（`bind: "lan"`），访问地址：  
`http://<你的IP>:18789`，Token认证：`CHANGE_ME_TOKEN`

### 推荐最优先安装的技能（针对你的环境）

| 优先级 | 技能 | 原因 |
|--------|------|------|
| ⭐⭐⭐ | `mcp-builder` | 可以构建更多微信/企业集成的MCP扩展 |
| ⭐⭐⭐ | `skill-creator` | 用来创建适合Qwen模型的自定义技能 |
| ⭐⭐⭐ | `doc-coauthoring` | 通过微信/企业微信channel协作写文档 |
| ⭐⭐⭐ | `frontend-design` | 零依赖，立即可用，生成高质量UI |
| ⭐⭐ | `pdf` + `pdf-reading` | 文件处理，pip安装即可 |
| ⭐⭐ | `docx` + `xlsx` + `pptx` | 办公文档全套，npm/pip安装 |
| ⭐ | `internal-comms` | 微信渠道输出企业内部通讯 |

### 注意事项

> ⚠️ **模型视觉能力**：你的 Qwen3.5-35B 配置为 `"image"` 输入支持，但实际能力取决于vLLM部署的模型版本。视觉相关技能（如canvas-design）可能需要验证。
>
> ⚠️ **file-reading 路径适配**：该技能依赖 `/mnt/user-data/uploads/` 路径，在LiteCode环境下需修改为你的实际文件路径。
>

---

## 一键安装所有Python依赖

```bash
pip install pypdf pdfplumber pypdfium2 reportlab pikepdf \
            python-pptx pptxgenjs openpyxl pandas xlrd \
            xlsxwriter Pillow cairosvg markitdown \
            --break-system-packages
```

## 一键安装所有系统依赖（CentOS）

```bash
# poppler (PDF工具)
yum install poppler-utils -y

# pandoc (文档转换)
yum install pandoc -y

# tesseract (OCR)
yum install tesseract -y

# LibreOffice (办公文档)
yum install libreoffice -y

# gifsicle (GIF优化)
yum install gifsicle -y
```

## npm 依赖

```bash
npm install -g docx pptxgenjs
```
