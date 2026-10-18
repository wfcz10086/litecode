"""lib/tools/defs/agent_defs.py — skill / agent / memory / self-reflect (8 个)."""

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
                "  operator   – 远程操作员: 操作一台已接入的 Windows 电脑 (windows_desktop / windows_system, "
                "截图→动作→截图确认, 改过文件用 inspect_file 回读); 不能改服务器文件、不能 execute_shell\n"
                "远程操作员 (operator) 用法: 必须用 alias 字段指定要操作的电脑 (单个任务用顶层 alias, "
                "parallel_tasks/pipeline_tasks/dag_tasks 每项写自己的 alias)。一台电脑同一时刻只能一个操作员 "
                "(只有一套鼠标键盘): 同时运行的两个 operator 指定同一 alias 会被拒绝派发, 同一台电脑上的多件事请合并成"
                "一个任务或用 pipeline_tasks/depends_on 串行; 某台电脑正被操作员占用时, 你自己对它的 windows_desktop/"
                "windows_system 调用会立即报错, 等子代理结束再操作。多台电脑并行示例: spawn_agent(parallel_tasks=["
                "{\"agent_type\":\"operator\",\"alias\":\"vm-a\",\"task\":\"在桌面新建 报告.txt 写入'A 完成'并保存\"}, "
                "{\"agent_type\":\"operator\",\"alias\":\"vm-b\",\"task\":\"用 WPS 打开 D:/sales.xlsx, 在 D5 填 =SUM(D2:D4) 保存\"}])。"
                "operator 属写类: 改过文件且之后回读过 (或只做界面操作且最后有截图/OCR 确认) 才标 [已验证]。\n"
                "Multiple spawn_agent calls can run the SAME task in different ways; pick the best result.\n"
                "并行改代码自动隔离: 本次派发有 ≥2 个写类子代理 (coder/analyst/tester/shell/writer) 并行或按 DAG 并发, "
                "且 workdir 是 git 仓库时, 每个写类子代理在独立 git 工作树 (分支 lc-agent/<id>) 里干活, 互不覆盖; "
                "结束后自动提交并 merge --no-ff 回主工作区, 冲突时先自动解冲突 (工作树内反向合并+校验, 默认开), "
                "解不了才保留分支与工作树并列出冲突文件与失败原因, "
                "交你用 agent_worktree 处理。主工作区有未提交改动时不自动合并。\n"
                "交付证据: 框架从子代理自己的工具调用里机器采集证据 (测试/构建/运行命令的退出码与通过失败数、"
                "写入文件及是否回读、搜索/读取取证、工作树提交与合并状态), 结果开头标 [已验证] 或 [未验证: 原因], "
                "末尾附「证据」小节; 多个子代理时列出每个子代理的判定。"
                "[未验证] 的子代理结论 (尤其是数字、'测试通过'、'已修复'之类) 只能当线索: "
                "汇报给用户前必须自己核实 (跑测试 / read_file 回读 / 重新计算), 或明确告诉用户该结论未经验证。\n"
                "后台子代理 (background=true): 只支持单个 task 或 parallel_tasks, 立即返回每个子代理的 id (sa-xxxxxx)、"
                "角色和任务摘要, 子代理在后台跑 (工作树隔离/证据/操作员独占照旧; 写类子代理在 git 仓库里一律用独立工作树, "
                "因为你自己也在同时改文件), 你不用干等。典型用法: spawn_agent(background=true, ...) 派出去 → 你继续做别的 → "
                "发现要补充/纠正时 agent_control(action=\"send\", id, message) 追加指令 (子代理下一轮迭代前收到) → "
                "需要结果时 agent_control(action=\"wait\", id) 收结果 (格式与阻塞模式相同, 带 [已验证]/[未验证] 标记)。"
                "每个会话同时运行的后台子代理默认最多 4 个 (超过拒绝派发); 回合结束时还没跑完的不会被杀, 下一轮再用 agent_control 取。"
                "不需要中途干预、马上就要结果的任务, 照旧用阻塞方式 (不带 background)。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task":       {"type": "string", "description": "Detailed task description for the subagent"},
                    "agent_type": {"type": "string",
                                   "enum": ["explorer","researcher","coder","analyst","tester","shell","writer","operator"],
                                   "description": "Subagent specialization"},
                    "alias":      {"type": "string", "description": "agent_type=operator 时必填: 要操作的 Windows 电脑别名 (windows_nodes list 可查); 多任务时作为未写 alias 的 operator 项的默认值"},
                    "context":    {"type": "string", "description": "Extra context to pass (file paths, prior results, etc.)"},
                    "workdir":    {"type": "string", "description": "要改动的项目目录 (绝对路径, 默认 WORKSPACE); 是 git 仓库时才可能用工作树隔离"},
                    "background": {"type": "boolean", "description": "true=后台运行, 立即返回子代理 id, 之后用 agent_control 查看/补充指令/等待/停止; 默认 false (阻塞到子代理结束)"},
                    "isolation":  {"type": "string", "enum": ["auto", "worktree", "none"],
                                   "description": "auto(默认)=≥2 个写类子代理并发且 workdir 是 git 仓库时自动用独立工作树; worktree=写类子代理一律隔离; none=不隔离 (旧行为, 共用目录)"},
                    "parallel_tasks": {
                        "type": "array",
                        "description": "并行子任务列表。多个独立任务同时执行。格式: [{\"task\":\"...\",\"agent_type\":\"...\"}]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task":       {"type": "string"},
                                "agent_type": {"type": "string"},
                                "alias":      {"type": "string", "description": "operator 必填: 该任务操作的 Windows 电脑别名"}
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
                                "agent_type": {"type": "string"},
                                "alias":      {"type": "string", "description": "operator 必填: 该任务操作的 Windows 电脑别名"}
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
                                "context":     {"type": "string", "description": "额外上下文"},
                                "alias":       {"type": "string", "description": "operator 必填: 该步骤操作的 Windows 电脑别名"}
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
            "name": "agent_control",
            "description": (
                "查看和控制本会话派出的子代理 (spawn_agent background=true 的后台子代理; 阻塞模式的也有记录可查)。"
                "action: list=列出本会话子代理及进度; status=某个子代理的状态/迭代数/最近工具调用; "
                "send=给运行中的子代理补充指令 (message ≤2000 字, 子代理在下一轮迭代开始前收到并照做, 正在跑的工具不会被打断; "
                "已结束的会报错); wait=等指定子代理 (不给 id 则等本会话全部) 结束并返回结果, timeout 默认 300 秒、最多 1800 秒, "
                "超时不报错而是返回当前进度, 可稍后再 wait 或先做别的; result=取已结束子代理的结果 (没结束返回进度); "
                "stop=请求停止, 子代理在下一轮迭代开始时结束 (状态 stopped, 结果标未验证; 在独立工作树里的改动只提交不合并, 用 agent_worktree 处理)。"
                "结果格式与阻塞模式相同: 开头 [已验证] 或 [未验证: 原因], 末尾附证据小节; [未验证] 的结论只能当线索, "
                "汇报前须自己核实或明确告诉用户未经验证。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action":  {"type": "string", "enum": ["list", "status", "send", "wait", "result", "stop"],
                                "description": "操作"},
                    "id":      {"type": "string", "description": "子代理 id (sa-xxxxxx, 来自 spawn_agent 后台派发结果或 list); list 不需要, wait 不给则等全部"},
                    "message": {"type": "string", "description": "send 用: 补充指令 (≤2000 字)"},
                    "timeout": {"type": "integer", "description": "wait 用: 最多等待秒数 (默认 300, 上限 1800)"},
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "agent_worktree",
            "description": (
                "处理子代理留下的隔离工作树 (spawn_agent 并行改代码时创建, 冲突/未合并时保留)。"
                "action=list 列出待处理工作树 (顺带清理 24 小时前已合并/已丢弃的残留); "
                "diff 看某个工作树相对基点的改动; merge 再次尝试 merge --no-ff 回主工作区 "
                "(冲突则 abort 并列出冲突文件, 可手动解决后再 merge 或 discard); discard 删除工作树与分支; "
                "resolve 对保留的冲突分支自动解冲突: 在工作树里反向合并主分支, 末尾追加类冲突两边保留, "
                "其余派解冲突子代理, 校验 (无冲突标记/py 可编译/agent.worktree_verify_cmd) 通过才合并, 否则回退保留并说明原因。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "merge", "discard", "diff", "resolve"],
                               "description": "操作"},
                    "id":     {"type": "string", "description": "工作树 id (spawn_agent 结果或 list 里的 [工作树 <id>]); list 不需要"},
                },
                "required": ["action"]
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
                "topic: optional, saves to a separate L2 file for that topic. "
                "key: optional short name for a single-valued fact (e.g. 'deploy port'); "
                "saving the same key again replaces the old value instead of adding a duplicate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "description": "Memory section heading"},
                    "content": {"type": "string", "description": "What to remember"},
                    "topic":   {"type": "string", "description": "Optional topic file name for L2 memory"},
                    "key":     {"type": "string", "description": "Optional fact name; same key overwrites the old value"},
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
