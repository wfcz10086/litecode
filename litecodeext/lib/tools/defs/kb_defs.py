"""lib/tools/defs/kb_defs.py — kb_search: 内置知识库检索 (plugins/kb; 按需工具组 kb)。"""

KB_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "kb_search",
            "description": (
                "检索用户上传到 LiteCode 知识库 (侧栏「📚 知识库」) 的文档: 手册、制度、合同、产品资料等。"
                "用户问「知识库/资料库/文档里/手册里/根据资料…」或问到只有内部资料才有答案的问题时先用它。"
                "返回带编号的原文片段 (含文件名、页码或段落号); 回答必须基于片段, 用到的句子后标注 [编号] 出处, "
                "没检索到就如实说没有, 不要编造。一次没找到可换关键词或换问法再搜。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查的问题或关键词 (用资料里可能出现的说法)"},
                    "kb": {"type": "string", "description": "知识库名称或 ID; 不填 = 检索全部知识库"},
                    "top_k": {"type": "integer", "description": "返回几处资料, 默认 5, 最多 10"},
                },
                "required": ["query"],
            },
        },
    },
]
