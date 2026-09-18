"""plugins/tools/vision.py — 把 vision_ocr 接进 plugins.registry, 让 DAG 工具步能编排 OCR。

背景 (2026-09-04): vision_ocr 的实现 (_do_vision_ocr) 原本只注册在 core/tools/handlers
(LLM 工具调用循环那套), 而 DAG 工具步走的是 plugins.registry —— registry.scan() 只扫
plugins/tools/*.py, 从不扫 handlers/。两套注册表不通, 于是 DAG 里建一个
tool_name=vision_ocr 的步能存 (schema 不校验工具是否注册), 一执行就报 "not registered"。

这个薄插件复用现成的 _do_vision_ocr, 让 registry 收录 vision_ocr, DAG 就能把 OCR
当成一个可编排步骤 (例: 上一步截图 → 本步 OCR → 下一步据文本分支)。
"""

try:
    from core.tools.vision import _do_vision_ocr
except Exception:  # pragma: no cover - 兼容不同 path 布局
    try:
        from tools.vision import _do_vision_ocr  # type: ignore
    except Exception:
        _do_vision_ocr = None


async def _vision_ocr(args, ctx):
    if _do_vision_ocr is None:
        return {"ok": False, "error": "vision_ocr 实现未找到 (core.tools.vision._do_vision_ocr)"}
    path = args.get("path") or args.get("image") or args.get("file") or ""
    prompt = args.get("prompt") or "识别图片中的所有文字, 按原排版尽量还原输出。"
    try:
        max_tokens = int(args.get("max_tokens") or 4096)
    except (TypeError, ValueError):
        max_tokens = 4096
    if not path:
        return {"ok": False, "error": "vision_ocr 需要 path (图片路径)"}
    try:
        text = await _do_vision_ocr(path, prompt, max_tokens)
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"vision_ocr 执行失败: {e!r}"}
    if isinstance(text, str) and text.startswith("ERROR"):
        return {"ok": False, "error": text}
    return {"ok": True, "result": text}


TOOLS = [
    {
        "name": "vision_ocr",
        "description": (
            "图片文字识别 / 视觉理解. 传图片 path, 用当前激活的视觉模型 (不支持时按 "
            "find_vision_fallback 路由到 Qwen 视觉) 做 OCR 或按 prompt 描述图片内容. "
            "可作为 DAG 步骤编排 —— 常见: 上一步产出/截图, 本步 OCR, 下一步据识别文本走条件分支."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "path":       {"type": "string",  "description": "图片文件路径 (相对 workspace 或绝对)"},
                "prompt":     {"type": "string",  "description": "识别/理解指令; 默认按原排版 OCR"},
                "max_tokens": {"type": "integer", "description": "输出上限, 默认 4096"},
            },
            "required": ["path"],
        },
        "capabilities": {"safe": True, "long_running": False, "produces_artifact": False, "cost": "medium"},
        "run": lambda args, ctx: _vision_ocr(args, ctx),
    },
]

VERSION = "0.1.0"
