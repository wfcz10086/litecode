"""lib/tools/defs/media_defs.py — vision_ocr + html_render (2 个)."""

MEDIA_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "vision_ocr",
            "description": (
                "[v1.1] 多模态图片识别. 把一张图片 (PNG/JPG/WEBP/GIF/BMP) 送给当前激活的视觉模型. "
                "[2026-08-26] 默认 mode=auto **自适应**: 文字为主的图 (文档/截图/表格/手写) 逐字 OCR 保持版式; "
                "画面为主的图 (照片/场景/插画/图表) 描述整体画面 (主体/场景/动作/关键细节, 顺带抄出零星文字); "
                "图文混排先概括画面再输出文字. 所以**不确定图片类型时直接用默认值即可**, 不用自己先判断. "
                "使用场景: (1) 文字/表格/手写 OCR; (2) 截图分析; (3) 照片/场景理解; "
                "(4) PDF 分页导出 PNG 后识别 (配合 pdf-ocr-pipeline skill). "
                "注意: > 8MB 自动压缩到 JPEG 85 质量; 单次 max_tokens 默认 4096."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":       {"type": "string", "description": "图片绝对路径或相对 workspace 的路径"},
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "ocr", "describe"],
                        "description": (
                            "auto(默认)=模型自己判断文字图还是画面图并选对应输出; "
                            "ocr=强制只逐字提取文字 (明确知道是文档时用, 避免模型跑去描述画面); "
                            "describe=强制只描述画面 (明确知道是照片/场景图时用)"
                        ),
                    },
                    "prompt":     {"type": "string", "description": "自定义识别任务. 传了就覆盖 mode, 用于特殊需求 (如'只提取表格第2列')"},
                    "max_tokens": {"type": "integer", "description": "单次最多生成 tokens, 默认 4096. 页密集文档可设 8192."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "html_render",
            "description": (
                "[#45] 把 HTML 源码或远程 URL 用 headless chromium 渲染成 PNG 截图, "
                "自动存到本会话 artifacts 目录. 用完即关, 不留浏览器进程. "
                "适合: 表格/图表可视化, 卡片式报告输出, 静态页面回归截图. "
                "html 与 url 二选一; 大 HTML (>2MB) 会拒绝."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "html":            {"type": "string",  "description": "原始 HTML 字符串 (与 url 二选一)"},
                    "url":             {"type": "string",  "description": "远程 URL (与 html 二选一)"},
                    "viewport_width":  {"type": "integer", "description": "视口宽 (120-3840, 默认 1280)"},
                    "viewport_height": {"type": "integer", "description": "视口高 (120-4320, 默认 800)"},
                    "full_page":       {"type": "boolean", "description": "整页截图, 默认 true"},
                    "wait_selector":   {"type": "string",  "description": "等待选择器出现再截 (最长 8s)"},
                    "wait_ms":         {"type": "integer", "description": "静态额外等待 ms (0-8000)"}
                }
            }
        }
    },
]
