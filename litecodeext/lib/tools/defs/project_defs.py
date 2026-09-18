"""lib/tools/defs/project_defs.py — project_init + locate/edit_symbol + update_map (4 个)."""

PROJECT_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "project_init",
            "description": (
                "Scan a project directory and build a symbol manifest + call graph. "
                "Supports Python, JS/TS, Go, Rust, Java, C/C++, Ruby, Shell and more. "
                "Outputs PROJECT_MANIFEST.md (file->symbols) and CALL_GRAPH.json (symbol->callers/callees). "
                "MUST call this before any medium/large project task. "
                "Skip re-scan if manifest exists and no files changed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Project root path (absolute or workspace-relative)"},
                    "refresh": {"type": "boolean", "description": "Force re-scan even if manifest exists (default false)"},
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "locate_symbol",
            "description": (
                "Find any function/class/variable in the project manifest. "
                "Returns: file path, exact line number, 20-line context, N-level call chain. "
                "Use BEFORE patch_file or edit_symbol when tracing bugs. Works for any language. "
                "Requires project_init first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":     {"type": "string", "description": "Symbol name to find"},
                    "filepath": {"type": "string", "description": "Narrow to this file (optional)"},
                    "depth":    {"type": "integer", "description": "Call chain depth 1-5 (default 2)"},
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_symbol",
            "description": (
                "Surgically replace ONE function/class body in any source file. "
                "Auto-detects language, finds symbol boundaries, replaces only that block, "
                "then runs syntax check (py_compile / node --check / go build / rustc etc). "
                "ALWAYS prefer this over write_file for existing symbols in files >150 lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath":    {"type": "string", "description": "Source file path"},
                    "symbol_name": {"type": "string", "description": "Exact name of function/class to replace"},
                    "new_code":    {"type": "string", "description": "Complete new definition including the header line"},
                },
                "required": ["filepath", "symbol_name", "new_code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_map",
            "description": (
                "Update PROJECT_MAP.md with DETAILED per-file information. "
                "MUST call after every 5 source files written. Workflow: read_file → extract ALL info → update_map. "
                "QUALITY STANDARD: Each file section must contain EVERY function/method with: "
                "line number (L#), full param signature with types, return type, and docstring/purpose. "
                "CODE files: routes(method/path/handler/params/doc) + "
                "functions(L#/name/params_with_types/return_type/doc) + "
                "classes(L#/name/base/methods_with_params/doc) + "
                "constants(ALL module-level constants comma-separated) + "
                "deps(imports/db_tables/ext_apis/used_by) + config(env/key/default) + "
                "progress(done/partial/todo+issues). "
                "FICTION/SCREENPLAY: scenes(scene_id/title/location/chars/status/words) + "
                "characters(name/role/arc/status) + chapters. "
                "RESEARCH/ESSAY: arguments(claim/evidence/source/strength) + chapters. "
                "Also fill: deps_graph(ASCII dependency diagram text) + "
                "core_flow(ASCII flowchart of main processing chain). "
                "Data is inserted into per-file ## sections in PROJECT_MAP.md. "
                "DO NOT skip any function — even small helpers matter for navigation. "
                "Include L# line numbers for every function/class to enable direct jump."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "Relative path of the source file (e.g. app/blueprints/hosts.py)"},
                    "file_description": {"type": "string", "description": "One-line description of this file's role (e.g. 'API Server + Agent Loop', 'Web Dashboard')"},
                    "constants": {"type": "string", "description": "Comma-separated list of ALL module-level constants/globals (e.g. 'BASE, CFG, MODEL_ID, MAX_TOKENS, PORT')"},
                    "deps_graph": {"type": "string", "description": "ASCII dependency diagram for ## 模块依赖 section (multi-line text with +-- arrows showing import relationships)"},
                    "core_flow": {"type": "string", "description": "ASCII flowchart for ## 核心流程 section (multi-line text showing main processing chain/data flow)"},
                    "routes": {
                        "type": "array",
                        "description": "API routes defined in this file",
                        "items": {
                            "type": "object",
                            "properties": {
                                "method":  {"type": "string", "description": "HTTP method: GET/POST/PUT/DELETE"},
                                "path":    {"type": "string", "description": "Route path e.g. /api/hosts/<int:id>"},
                                "handler": {"type": "string", "description": "Handler function name"},
                                "params":  {"type": "string", "description": "Input params with types e.g. 'body:{name:str,ip:str}'"},
                                "returns": {"type": "string", "description": "Response format e.g. '{id,name,status}' or 'List[Host]'"},
                                "note":    {"type": "string", "description": "Brief note"}
                            }
                        }
                    },
                    "functions": {
                        "type": "array",
                        "description": "Top-level functions (not class methods)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name":      {"type": "string"},
                                "line_no":   {"type": "integer", "description": "Line number where function is defined (for L# column)"},
                                "params":    {"type": "string", "description": "Parameters with types e.g. 'host_id: int, data: dict'"},
                                "returns":   {"type": "string", "description": "Return type e.g. 'dict | None'"},
                                "calls":     {"type": "string", "description": "Functions this calls e.g. 'db.query, send_alert'"},
                                "called_by": {"type": "string", "description": "Who calls this e.g. 'api_create_host, scheduler'"},
                                "note":      {"type": "string", "description": "Brief doc/purpose"}
                            }
                        }
                    },
                    "classes": {
                        "type": "array",
                        "description": "Class definitions",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name":    {"type": "string"},
                                "line_no": {"type": "integer", "description": "Line number where class is defined"},
                                "bases":   {"type": "string", "description": "Base classes e.g. 'db.Model'"},
                                "methods": {"type": "string", "description": "Key methods comma-separated"},
                                "note":    {"type": "string"}
                            }
                        }
                    },
                    "deps": {
                        "type": "object",
                        "description": "Module dependency info for this file",
                        "properties": {
                            "imports":    {"type": "string", "description": "Modules/packages this file imports e.g. 'models.Host, utils.notify, redis'"},
                            "db_tables":  {"type": "string", "description": "DB tables this file reads/writes e.g. 'hosts, monitor_items'"},
                            "ext_apis":   {"type": "string", "description": "External APIs/services called e.g. 'smtp, slack_webhook'"},
                            "used_by":    {"type": "string", "description": "Other files that import from this file e.g. 'api/routes.py, scheduler.py'"}
                        }
                    },
                    "config": {
                        "type": "array",
                        "description": "Config keys / env vars this file reads",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key":      {"type": "string", "description": "Config key or env var name e.g. 'SMTP_HOST'"},
                                "default":  {"type": "string", "description": "Default value if any"},
                                "required": {"type": "boolean", "description": "Whether it is required"},
                                "note":     {"type": "string"}
                            }
                        }
                    },
                    "progress": {
                        "type": "object",
                        "description": "Implementation progress of this file",
                        "properties": {
                            "status":  {"type": "string", "description": "done / partial / todo"},
                            "done":    {"type": "string", "description": "What is already implemented"},
                            "todo":    {"type": "string", "description": "What is still missing or stubbed"},
                            "issues":  {"type": "string", "description": "Known bugs or limitations"}
                        }
                    },
                    "tests": {
                        "type": "array",
                        "description": "Test coverage for this file/module. Fill for EVERY source file.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "test_file":   {"type": "string", "description": "Relative path of test file e.g. tests/test_hosts.py"},
                                "test_func":   {"type": "string", "description": "Test function name(s) covering this file e.g. test_create_host,test_delete_host"},
                                "status":      {"type": "string", "description": "pass / fail / pending / skip"},
                                "cmd":         {"type": "string", "description": "Command to run this test e.g. pytest tests/test_hosts.py -v"},
                                "covers":      {"type": "string", "description": "What behaviors/paths are covered"},
                                "note":        {"type": "string"}
                            }
                        }
                    },
                    "templates": {
                        "type": "array",
                        "description": "Template/asset/config files this source file depends on",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path":    {"type": "string", "description": "Template/asset file path e.g. templates/host_detail.html"},
                                "type":    {"type": "string", "description": "html/jinja2/yaml/json/css/sql/prompt/other"},
                                "purpose": {"type": "string", "description": "What this template/asset is used for"}
                            }
                        }
                    },
                    "scenes": {
                        "type": "array",
                        "description": "For screenplay/novel/script: scenes or episodes in this file",
                        "items": {
                            "type": "object",
                            "properties": {
                                "scene_id":   {"type": "string", "description": "Scene/episode identifier e.g. S01E03 or 第三幕第二场"},
                                "title":      {"type": "string", "description": "Scene title or summary"},
                                "location":   {"type": "string", "description": "Setting/location"},
                                "characters": {"type": "string", "description": "Characters appearing in this scene"},
                                "status":     {"type": "string", "description": "draft/done/review"},
                                "words":      {"type": "integer", "description": "Word count"},
                                "note":       {"type": "string"}
                            }
                        }
                    },
                    "characters": {
                        "type": "array",
                        "description": "For fiction/screenplay: character profiles introduced or developed in this file",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name":       {"type": "string"},
                                "role":       {"type": "string", "description": "protagonist/antagonist/supporting/minor"},
                                "arc":        {"type": "string", "description": "Character arc summary"},
                                "first_appearance": {"type": "string", "description": "File/chapter where introduced"},
                                "status":     {"type": "string", "description": "active/resolved/dormant"}
                            }
                        }
                    },
                    "chapters": {
                        "type": "array",
                        "description": "Chapter/section outlines for writing/research tasks",
                        "items": {
                            "type": "object",
                            "properties": {
                                "order":  {"type": "integer", "description": "Section order number"},
                                "title":  {"type": "string", "description": "Section title"},
                                "status": {"type": "string", "description": "draft/done/review"},
                                "words":  {"type": "integer", "description": "Approximate word count"},
                                "note":   {"type": "string"}
                            }
                        }
                    },
                    "arguments": {
                        "type": "array",
                        "description": "Core arguments/thesis points for research/analysis",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim":    {"type": "string", "description": "The argument or thesis"},
                                "evidence": {"type": "string", "description": "Supporting evidence"},
                                "source":   {"type": "string", "description": "Source reference"},
                                "strength": {"type": "string", "description": "strong/moderate/weak"}
                            }
                        }
                    },
                    "changelog": {
                        "type": "string",
                        "description": "One-line change log entry for this update"
                    }
                },
                "required": ["filepath"]
            }
        }
    },
]
