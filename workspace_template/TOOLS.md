# TOOLS.md — 本 Session 环境备忘

## 工程路径
<!-- 在 session 初始化时由系统自动填充 -->

## 服务地址
<!-- 记录本 session 启动的服务端口和URL -->
<!-- 示例: LiteCode Server: http://127.0.0.1:18789 -->
<!-- 示例: Web UI: http://127.0.0.1:18790 -->
<!-- 示例: Browser Agent: http://127.0.0.1:19000 -->

## 环境变量
<!-- 记录重要的环境配置 -->

## Skills 能力列表

当需要处理以下任务时，先用 `load_skill` 加载对应 SKILL.md：

| 任务类型 | Skill 名称 |
|---------|-----------|
| 读取任何上传文件 | file-reading |
| 创建/编辑 Word 文档 | docx |
| PDF 处理（合并/拆分/水印） | pdf |
| 读取 PDF 内容 | pdf-reading |
| Excel 表格处理 | xlsx |
| PPT 演示文稿 | pptx |
| 前端/网页组件开发 | frontend-design |
| Go 语言项目 | go |
| 深度搜索研究 | deep-search |
| 研究分析（股票/加密货币） | research-analyst |
| 浏览器自动化/爬虫/登录/发帖 | browser-automation |

**规则：** 遇到上述任务，先 `load_skill` 加载对应 SKILL.md，再动手。

## 浏览器自动化

Browser Agent 服务（端口 19000）：
- 启动: `nohup python3 /opt/litecode/skills/browser-automation/browser_server.py > /tmp/bas.log 2>&1 &`
- 检查: `curl -s http://127.0.0.1:19000/health`
- VNC 可视: `http://<host>:18800/vnc.html?autoconnect=true`

## 备注
<!-- 任何与本 session 相关的环境信息 -->
