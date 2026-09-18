"""lib/tools/defs/code_defs.py — file ops / code search / code nav / issue lookup (11 个)."""

CODE_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "github_search_issues",
            "description": (
                "[v1.0.3] 搜 GitHub 公开 issue/PR (Issues Search API, 无 auth 60 req/hr)。"
                "用于: 代码报错查已知问题 / 库 bug 定位 / 找修复 PR。"
                "query 尽量是精确的异常类+消息 (如 'ValueError: too many values to unpack')。"
                "repo=owner/name 可限定单仓库。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索词, 建议用异常类+关键消息"},
                    "repo":  {"type": "string", "description": "可选 owner/name 限定仓库 (如 'pytorch/pytorch')"},
                    "state": {"type": "string", "enum": ["all", "open", "closed"], "description": "默认 all"},
                    "top_k": {"type": "integer", "description": "返回数, 默认 5, 最多 10"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stackoverflow_search",
            "description": (
                "[v1.0.3] 搜 Stack Overflow (Stack Exchange API, 无 auth 300 req/day)。"
                "按投票排序, 返回 top_k 问答。tag 可按语言过滤。"
                "用于: 编程问题 / 报错找答案 / 最佳实践查询。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "问题描述或报错关键词"},
                    "tag":   {"type": "string", "description": "可选过滤 tag, 如 python / javascript / go"},
                    "top_k": {"type": "integer", "description": "返回数, 默认 5"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_code_error",
            "description": (
                "[v1.0.3] 高层错误诊断: 自动从 traceback 抽异常 signature, "
                "并行查 GitHub issues + Stack Overflow, 返回相关已知问题 + 解决方案。"
                "最常用, 当 execute_shell 返回 traceback 时直接把整个 stderr 丢进来即可。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "error_text": {"type": "string", "description": "完整的 stderr / traceback"},
                    "repo":       {"type": "string", "description": "可选限定 github 仓库 owner/name"},
                    "top_k":      {"type": "integer", "description": "各源返回数, 默认 3"}
                },
                "required": ["error_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file content. Use lines='start:end' to read a range (colon separator, e.g. lines='1:50' or lines='100:200'). Do NOT use dash '-' as separator.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "lines":    {"type": "string", "description": "Line range as 'start:end', e.g. '1:50'. Use COLON not dash."}
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "content":  {"type": "string"}
                },
                "required": ["filepath", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": "Atomic unique-string replace. old_str must appear exactly once.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "old_str":  {"type": "string"},
                    "new_str":  {"type": "string"},
                    "append":   {"type": "boolean"}
                },
                "required": ["filepath"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apply_blocks",
            "description": (
                "Apply multiple SEARCH/REPLACE edits across one or more files in a single call. "
                "Aider-style format. Each block: path + `<<<<<<< SEARCH` + old + `=======` + new + `>>>>>>> REPLACE`. "
                "More efficient than multiple patch_file calls. Atomic per-block (one fails, others still apply)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "blocks": {
                        "type": "string",
                        "description": "Concatenated Aider blocks (multi-block ok). See description for format."
                    }
                },
                "required": ["blocks"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_tree",
            "description": (
                "Get directory tree structure. "
                "Call at the start of any large project task to understand layout. "
                "Skips __pycache__, node_modules, .git, venv, dist, build automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":        {"type": "string",  "description": "Root dir (default: workspace)"},
                    "max_depth":   {"type": "integer", "description": "Max depth, default 3"},
                    "show_hidden": {"type": "boolean", "description": "Include hidden files, default false"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": (
                "Find files by glob/name pattern in a directory. "
                "Use before read_file to locate files in large codebases. "
                "Examples: pattern='*.py', pattern='config.json', pattern='**/*test*'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern":     {"type": "string",  "description": "File name or glob pattern"},
                    "path":        {"type": "string",  "description": "Search root (default: workspace)"},
                    "max_results": {"type": "integer", "description": "Max results, default 50"}
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search text/regex across all files in a project (like grep -r). "
                "Returns file:line:content. Essential for debugging, tracing call chains, "
                "finding usages, and understanding large codebases."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string",  "description": "Search term or Python regex"},
                    "path":        {"type": "string",  "description": "Search root (default: workspace)"},
                    "ext":         {"type": "string",  "description": "Filter by extension, e.g. '.py'"},
                    "max_results": {"type": "integer", "description": "Max matches, default 40"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": (
                "Find function or class definition by exact name from SYMBOL_INDEX.json. "
                "Faster than grep when you know the symbol name. "
                "Returns file:line locations with signatures."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Symbol name (function/class) to find"}
                },
                "required": ["name"]
            }
        }
    },
]
