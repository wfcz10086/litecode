"""handlers/media.py — web_fetch + vision_ocr thin wrappers."""
from __future__ import annotations

from typing import Any

from tools.vision import _do_vision_ocr
from tools.web import _do_web_fetch

from . import register


@register("web_fetch")
async def h_web_fetch(sid: str, args: dict) -> tuple[str, Any]:
    return await _do_web_fetch(args["url"], args.get("max_chars", 4000)), None


# ── vision_ocr 三种模式的 prompt ────────────────────────────────────
# [2026-08-26] 原来只有一条纯 OCR prompt ("逐字输出图中所有文字"), 喂场景照/纯图
# 进去只会得到"未发现文字"或干脆瞎编。改成默认自适应, 同时保留强制指定的能力。
_VISION_PROMPTS = {
    # 自适应 — 让视觉模型自己先判断图片类型再选输出方式 (单次调用, 不做两遍分类省 token)
    "auto": (
        "先判断这张图属于哪类, 再按对应方式输出, 不要输出判断过程本身:\n"
        "1) 以文字为主 (文档/网页/聊天截图/表格/PPT/手写/代码): "
        "逐字输出全部文字, 保持原版式 (标题/段落/表格/列表结构), 不要翻译, 不要解释;\n"
        "2) 以画面为主 (照片/场景图/插画/示意图/图表, 文字很少或没有): "
        "描述整体画面 —— 主体是什么、在什么场景、在做什么、有哪些关键细节; "
        "若图中有零星文字 (招牌/水印/标签/坐标轴) 也一并抄出;\n"
        "3) 图文混排 (海报/信息图/带大量标注的截图): "
        "先用几句话概括整体画面, 再逐字输出其中的文字。"
    ),
    # 强制纯 OCR — 原默认行为, 明确知道是文档时用, 避免模型跑去描述画面
    "ocr": (
        "请逐字输出图中所有文字, 保持原版式 (标题/段落/表格/列表结构), "
        "不要翻译, 不要解释。"
    ),
    # 强制画面描述 — 明确知道是照片/场景图时用
    "describe": (
        "描述这张图的整体画面: 主体是什么、在什么场景、在做什么、有哪些关键细节 "
        "(构图/色调/氛围/数量/位置关系)。若图中有文字也一并抄出。不要逐字 OCR 无关内容。"
    ),
}


@register("vision_ocr")
async def h_vision_ocr(sid: str, args: dict) -> tuple[str, Any]:
    # 显式 prompt 优先级最高; 否则按 mode 选 (默认 auto 自适应)
    prompt = args.get("prompt")
    if not prompt:
        mode = str(args.get("mode", "auto")).strip().lower()
        prompt = _VISION_PROMPTS.get(mode, _VISION_PROMPTS["auto"])
    return await _do_vision_ocr(
        path=args.get("path", ""),
        prompt=prompt,
        max_tokens=int(args.get("max_tokens", 4096)),
    ), None
