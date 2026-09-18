"""lib/tools/defs/agent_defs.py — skill / agent / memory / self-reflect (7 个)."""

AGENT_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": (
                "Load a skill's full instructions by name. "
                "Call this at the START of a task when you recognize the domain "
                "(e.g. Go code → load_skill('go'), stock analysis → load_skill('research-analyst'), "
                "React UI → load_skill('frontend-design'), PDF → load_skill('pdf')). "
                "You decide which skill fits — do NOT wait for keywords. "
                "Use list_skills() first if unsure what's available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name as shown in the skills list"},
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List all available skills with descriptions. Call when unsure which skill to load.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": (
                "Spawn a focused subagent to handle a specific subtask in parallel or sequentially. "
                "Use when a task has clearly separable parts: e.g. 'research' + 'code' + 'verify'. "
                "The subagent runs its own tool loop and returns results. "
                "agent_type options:\n"
                "  explorer   – understands codebase structure (get_tree, find_files, search_code, read_file)\n"
                "  researcher – searches web and fetches data (web_fetch, web_search)\n"
                "  coder      – writes and runs code in any language (write_file, patch_file, execute_shell)\n"
                "  analyst    – fetches data + analyzes + runs code (web_fetch, execute_shell, write_file)\n"
                "  tester     – runs tests and verifies results (execute_shell, read_file)\n"
                "  shell      – pure shell/bash tasks (execute_shell only)\n"
                "  writer     – long-form writing: novels, reports, articles (write_file, read_file)\n"
                "Multiple spawn_agent calls can run the SAME task in different ways; pick the best result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task":       {"type": "string", "description": "Detailed task description for the subagent"},
                    "agent_type": {"type": "string",
                                   "enum": ["explorer","researcher","coder","analyst","tester","shell","writer"],
                                   "description": "Subagent specialization"},
                    "context":    {"type": "string", "description": "Extra context to pass (file paths, prior results, etc.)"},
                    "parallel_tasks": {
                        "type": "array",
                        "description": "并行子任务列表。多个独立任务同时执行。格式: [{\"task\":\"...\",\"agent_type\":\"...\"}]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task":       {"type": "string"},
                                "agent_type": {"type": "string"}
                            }
                        }
                    },
                    "pipeline_tasks": {
                        "type": "array",
                        "description": "串行管道任务列表。A 的输出自动作为 B 的 context。格式: [{\"task\":\"...\",\"agent_type\":\"...\"}]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task":       {"type": "string"},
                                "agent_type": {"type": "string"}
                            }
                        }
                    },
                    "dag_tasks": {
                        "type": "array",
                        "description": (
                            "DAG 编排任务列表（v1.0）。支持依赖关系、同层并行、失败重试、Critic审查。"
                            "格式: [{\"task\":\"...\",\"agent_type\":\"...\",\"step_id\":\"step_1\","
                            "\"label\":\"设计\",\"depends_on\":[],\"max_retries\":2,\"timeout\":300}]。"
                            "depends_on 指定依赖的 step_id，无依赖的步骤自动并行执行。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "task":        {"type": "string", "description": "子任务描述"},
                                "agent_type":  {"type": "string"},
                                "step_id":     {"type": "string", "description": "步骤唯一ID（如 step_1）"},
                                "label":       {"type": "string", "description": "步骤标签"},
                                "depends_on":  {"type": "array", "items": {"type": "string"}, "description": "依赖的 step_id 列表"},
                                "max_retries": {"type": "integer", "description": "最大重试次数（默认2）"},
                                "timeout":     {"type": "integer", "description": "超时秒数（默认300）"},
                                "context":     {"type": "string", "description": "额外上下文"}
                            }
                        }
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["parallel", "pipeline", "competitive", "dag", "batch"],
                        "description": "编排模式。parallel=同时跑, pipeline=串行链, competitive=竞争取最佳, dag=DAG依赖图编排, batch=批量同类任务（用 batch_items 字段）"
                    },
                    "batch_items": {
                        "type": "array",
                        "description": (
                            "批量同类任务列表 (2026-05 新增). 跟 parallel_tasks 不同, "
                            "batch_items 共享同一个 base task / agent_type / context, "
                            "每个 item 只换 item_id + 该 item 的具体内容. "
                            "默认**串行**执行 (避免子代理同时改 state.json/同一文件), "
                            "每个 item 都是一个全新 spawn (max_iter 独立, history 隔离). "
                            "典型场景: 写 N 章小说 (1 item = 1 章) / 批量修 N 个 bug / 批量爬 N 个 URL / "
                            "批量生成 N 个模块代码. 比 parallel_tasks 更适合大规模重复性任务. "
                            "用法: spawn_agent(task='你是 writer 写仙侠小说, 大纲见 outline.md', "
                            "agent_type='writer', context='共享设定见 state.json', "
                            "batch_items=[{'item_id':'ch01','task':'写第 1 章: 主角入门'}, "
                            "{'item_id':'ch02','task':'写第 2 章: 试炼'}, ...])"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_id": {"type": "string", "description": "本 item 标识 (如 ch01, bug-123, module-auth)"},
                                "task":    {"type": "string", "description": "本 item 具体要做什么 (会拼到 base task 后)"},
                                "context": {"type": "string", "description": "本 item 特有的额外 context (可选)"},
                            }
                        }
                    },
                },
                "required": ["task", "agent_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_profile",
            "description": (
                "Update session context files (USER.md / TOOLS.md / SOUL.md). "
                "Use this when you learn NEW user information (name, preferences, work, habits) "
                "or when new tools/services/paths are created in the session. "
                "DO NOT call read_file on USER.md/TOOLS.md — use this tool to read AND update them.\n"
                "action='read'  → returns current content of the file\n"
                "action='merge' → intelligently merge new_content into existing file (append/update sections)\n"
                "action='replace_section' → replace a specific ## section"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file":    {"type": "string", "enum": ["USER.md", "TOOLS.md", "SOUL.md", "IDENTITY.md", "HEARTBEAT.md", "BOOTSTRAP.md"],
                                "description": "Which context file to update"},
                    "action":  {"type": "string", "enum": ["read", "merge", "replace_section"],
                                "description": "read=view current, merge=add new info, replace_section=overwrite a section"},
                    "section": {"type": "string", "description": "Section heading (for replace_section), e.g. '偏好'"},
                    "new_content": {"type": "string", "description": "Content to merge or replace with"},
                },
                "required": ["file", "action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "Explicitly save important information to session memory. "
                "Use when the user provides key info worth remembering across sessions: "
                "project decisions, architecture choices, resolved bugs, user corrections. "
                "section: which memory section (e.g. 'Key References', 'Completed Work'). "
                "topic: optional, saves to a separate L2 file for that topic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "description": "Memory section heading"},
                    "content": {"type": "string", "description": "What to remember"},
                    "topic":   {"type": "string", "description": "Optional topic file name for L2 memory"},
                },
                "required": ["section", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "self_reflect",
            "description": (
                "自我反思工具 - 查看过往经验, 用于主动学习。\n"
                "调用场景:\n"
                "  - 开始复杂任务前: 调用 scope='errors' 看看类似问题历史上是怎么踩坑的\n"
                "  - 完成任务后: 调用 scope='summary' 生成'这次学到了什么'供下次调用\n"
                "  - 用户问'你最近常做什么类型的任务': scope='skill_usage'\n"
                "  - 识别重复出错模式: scope='patterns'\n"
                "注意: 这是主动学习工具, 不是被动记忆。看完后要真的改变行为, 不要装模作样。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["errors", "summary", "skill_usage", "patterns"],
                        "description": ("errors=最近 N 个已解决错误+修复方法; "
                                        "summary=生成本 session 的 lessons learned 写入 MEMORY.md; "
                                        "skill_usage=skill 使用统计(哪些活跃/冷门); "
                                        "patterns=检测重复出错模式")
                    },
                    "query": {"type": "string", "description": "可选: 过滤关键词（仅对 errors 有效）"},
                    "limit": {"type": "integer", "description": "返回条目数上限（默认 10）"}
                },
                "required": ["scope"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": (
                "创建新的 skill (SKILL.md)。默认写到 skills/drafts/ 作为草稿, 需用户审核后手工移到 skills/ 激活。"
                "这是安全设计: 避免模型自己创建的低质量 skill 污染核心库。"
                "只有当用户明确说'直接激活'或'立即可用'时, 才设置 activate=true 直接写到 skills/。"
                "适用场景: 用户多次解决同一类问题后要求'把这个流程保存为 skill 下次用'。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":        {"type": "string", "description": "Skill 目录名, 只能含小写字母/数字/连字符/下划线, 如 'stock-monitor'"},
                    "description": {"type": "string", "description": "一行描述, 用于触发判断"},
                    "content":     {"type": "string", "description": "完整 SKILL.md markdown 内容"},
                    "activate":    {"type": "boolean", "description": "True=直接激活到 skills/（需用户明确要求）; False/默认=写到 skills/drafts/ 草稿"},
                    "overwrite":   {"type": "boolean", "description": "True=覆盖已存在的同名 skill"}
                },
                "required": ["name", "content"]
            }
        }
    },
]
