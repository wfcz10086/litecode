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
# 所有识图请求共用的"如实"规则 (2026-10-23): 识图模型最常见的错是把看不清的字"脑补"成通顺的句子、
# 数字抄错、表格串行。config.json 的 vision.ocr_rules 可整段覆盖。
_OCR_RULES = (
    "识别规则 (务必遵守):\n"
    "- 只写图里真实存在的内容, 不补全、不纠错、不翻译、不总结; 看不清的字用 [?] 标出, 整段看不清写 [模糊]。\n"
    "- 数字、金额、日期、编号、网址、邮箱、代码、英文大小写按原样逐字符抄, 小数点和千分位不能错。\n"
    "- 表格输出为 Markdown 表格, 行列与原表一致, 合并单元格重复填写或留空, 空单元格留空。\n"
    "- 多栏排版按阅读顺序 (先左栏后右栏) 输出; 竖排文字按从右到左、从上到下的顺序转成横排。\n"
    "- 数学公式用 LaTeX ($...$); 代码保留缩进并放进代码块。\n"
    "- 印章、签名、手写批注、勾选框 (☑/☐) 单独标注出来, 如 [印章: xx公司]、[手写: ...]。\n"
    "- 直接输出结果, 不要开场白和结尾总结。"
)

_VISION_PROMPTS = {
    # 自适应 — 让视觉模型自己先判断图片类型再选输出方式 (单次调用, 不做两遍分类省 token)
    "auto": (
        "先判断这张图属于哪类, 再按对应方式输出; 不要输出判断过程, 也不要写出类别编号或类别名 (如\"1) 以文字为主\"):\n"
        "1) 以文字为主 (文档/网页/聊天截图/表格/PPT/手写/代码): "
        "逐字输出全部文字, 保持原版式 (标题/段落/表格/列表结构);\n"
        "2) 以画面为主 (照片/场景图/插画/示意图/图表, 文字很少或没有): "
        "描述整体画面 —— 主体是什么、在什么场景、在做什么、有哪些关键细节; "
        "图表要读出类型、坐标轴、关键数值和趋势; 若图中有零星文字 (招牌/水印/标签/坐标轴) 也一并抄出;\n"
        "3) 图文混排 (海报/信息图/带大量标注的截图): "
        "先用几句话概括整体画面, 再逐字输出其中的文字。"
    ),
    # 强制纯 OCR — 明确知道是文档时用, 避免模型跑去描述画面
    "ocr": (
        "这是一张以文字为主的图。请逐字输出图中所有文字, 保持原版式: "
        "标题用 #, 列表保留编号, 段落之间空一行, 页眉页脚和页码放在最前/最后并标注 [页眉]/[页脚]。"
    ),
    # 只要表格 — 账单/报表/成绩单等
    "table": (
        "只提取图中的表格, 每个表格输出为一个 Markdown 表格 (表头在第一行), 表格上方写出它的标题 (没有就写 表1/表2)。"
        "表格外的文字不要输出。"
    ),
    # UI 原型 / 设计稿 / 网页或 App 截图 → 能照着写代码的结构化描述
    "ui": (
        "这是界面原型/设计稿/网页或 App 截图, 请输出能让前端照着还原的说明:\n"
        "1. 整体: 页面类型、尺寸比例 (桌面/手机)、整体风格 (配色主色/辅色的十六进制近似值、圆角、阴影、字体风格);\n"
        "2. 布局: 从上到下、从左到右列出区块 (导航栏/侧栏/卡片/表单/表格/弹窗…), 写清每块的位置、大致宽高比例、对齐方式和间距;\n"
        "3. 组件: 每个按钮/输入框/下拉/开关/图标/图片占位的文字、状态 (选中/禁用/悬停) 和层级关系;\n"
        "4. 文字: 逐字抄出所有可见文字 (标题、按钮、提示、表格内容);\n"
        "5. 交互推测: 可以看出的交互 (标签页、折叠、分页), 标明是推测。\n"
        "看不清的地方写 [?], 不要编造不存在的元素。"
    ),
    # 参考图 → 反推生图提示词 (拿去给 Qwen-Image / ComfyUI / CPU 生图复刻同风格)
    "image_prompt": (
        "把这张图当作生图的参考图, 反推出能复刻它的提示词。按以下格式输出:\n"
        "【主体】人物/物体是什么、外观、动作、表情、数量\n"
        "【场景】环境、背景元素、时间、天气\n"
        "【风格】画风 (写实照片/动漫/简笔/水彩/3D/扁平插画…)、媒介、参考的艺术风格类型\n"
        "【构图】景别 (特写/中景/全景)、视角、主体位置、画面比例\n"
        "【光线与色彩】光源方向、明暗、主色调\n"
        "【图中文字】逐字抄出 (没有写 无)\n"
        "【中文提示词】一段连贯的中文生图提示词\n"
        "【英文提示词】对应的英文 prompt (逗号分隔的短语)\n"
        "【反向提示词】应避免的元素"
    ),
    # 图表 → 数据
    "chart": (
        "这是一张图表。请输出: 图表类型和标题; 坐标轴名称、单位和刻度范围; 图例; "
        "把能读出的数据点整理成 Markdown 表格 (读不准的数值标 ≈); 最后用 2~3 句话说明主要趋势或对比结论。"
    ),
    # 报错 / 终端截图 → 诊断
    "error": (
        "这是报错或终端/日志截图。请先逐字抄出错误信息 (错误类型、错误消息、文件路径和行号、关键堆栈) 放进代码块, "
        "再说明: 出错的是哪个程序/命令、最可能的原因 (按可能性排序, 标明推测)、建议先试的修复步骤。"
    ),
    # 强制画面描述 — 明确知道是照片/场景图时用
    "describe": (
        "描述这张图的整体画面: 主体是什么、在什么场景、在做什么、有哪些关键细节 "
        "(构图/色调/氛围/数量/位置关系)。若图中有文字也一并抄出。不要逐字 OCR 无关内容。"
    ),
}


def _ocr_rules() -> str:
    try:
        from lib.config import CFG
        custom = ((CFG.get("vision") or {}).get("ocr_rules") or "").strip()
        if custom:
            return custom
    except Exception:
        pass
    return _OCR_RULES


@register("vision_ocr")
async def h_vision_ocr(sid: str, args: dict) -> tuple[str, Any]:
    # 显式 prompt 优先级最高; 否则按 mode 选 (默认 auto 自适应)
    prompt = args.get("prompt")
    if not prompt:
        mode = str(args.get("mode", "auto")).strip().lower()
        prompt = _VISION_PROMPTS.get(mode, _VISION_PROMPTS["auto"])
        if mode in ("auto", "ocr", "table", "chart", "error"):   # 自定义 prompt 不附加规则 (用户可能就是要总结/翻译)
            prompt = f"{prompt}\n\n{_ocr_rules()}"
    paths = args.get("paths")
    if isinstance(paths, str):
        paths = [paths]
    return await _do_vision_ocr(
        path=args.get("path", ""),
        prompt=prompt,
        max_tokens=int(args.get("max_tokens", 4096)),
        paths=paths if isinstance(paths, list) else None,
    ), None
