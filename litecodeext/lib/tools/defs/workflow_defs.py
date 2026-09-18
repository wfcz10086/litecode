"""lib/tools/defs/workflow_defs.py — timer + dag 编排 (11 个)."""

WORKFLOW_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "create_timer",
            "description": (
                "创建一个 LiteCode 自有定时任务（不碰系统 cron / systemd / at）。"
                "用户在聊天里说'每 X 分钟提醒我...'/'每天 X 点跑...'/'X 分钟后...' 时调用本工具。"
                "type=cron 时 schedule 是 5 段 crontab；type=once 时 schedule 是 ISO 时间字符串。"
                "action_type 决定到点做什么：shell（跑命令）/ agent（让本会话或指定 sid 跑一段 prompt）/ "
                "dag（直接触发 workspace/dags/<name>.json 里的 DAG，无需 token）/ "
                "wechat_msg（发微信）/ web_notify（在 Web UI 推通知）。"
                "用户说'每天 9 点跑 DAG xxx'就走 dag 动作: action_target 填 DAG 名, action_content 留空。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":           {"type": "string", "description": "定时器名（描述性即可，无需唯一）"},
                    "type":           {"type": "string", "enum": ["cron", "once"],
                                       "description": "cron=周期触发，once=单次到点触发"},
                    "schedule":       {"type": "string", "description":
                                       "cron 时填 crontab 5 段如 '*/5 * * * *'；once 时填 ISO 如 '2026-05-10T15:30:00'"},
                    "action_type":    {"type": "string",
                                       "enum": ["shell", "agent", "dag", "wechat_msg", "web_notify"]},
                    "action_target":  {"type": "string",
                                       "description": "agent: 目标 sid（空=同会话）；dag: DAG 名；"
                                                      "wechat_msg: 'bot_id:user_id'；其余留空"},
                    "action_content": {"type": "string",
                                       "description": "shell=命令；agent=prompt；dag=留空(用 target)；"
                                                      "wechat_msg=要发的文本；web_notify=通知文本"}
                },
                "required": ["name", "type", "schedule", "action_type", "action_content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_dag",
            "description": (
                "创建一个 DAG 编排（多子代理流水线）。用户在聊天里说"
                "'建一个 DAG /搞个流水线/做个工作流' 时调用本工具。"
                "每个节点 = 一个子代理（agent_type ∈ explorer / coder / researcher / "
                "tester / writer / critic / planner / shell）+ 一段 task prompt。"
                "edges 形如 [{'from': 'node1_id', 'to': 'node2_id'}] 描述拓扑。"
                "节点可选 'when' 字段做多条件判断, AND 关系, 任一不满足该节点 SKIPPED, "
                "语法: 'success:<sid>' / 'failure:<sid>' / 'contains:<sid>:<keyword>' / "
                "'!contains:<sid>:<keyword>'。例: writer 节点 when=['success:fetch','contains:fetch:BTC'] "
                "表示 fetch 成功且输出含 BTC 才跑 writer。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string", "description": "DAG 名（用作 /api/dags/{name} 的 key，建议小写下划线）"},
                    "nodes": {"type": "array", "description": "节点列表",
                              "items": {"type": "object",
                                        "properties": {
                                            "id":         {"type": "string"},
                                            "agent_type": {"type": "string"},
                                            "label":      {"type": "string"},
                                            "task":       {"type": "string"},
                                            "when":       {"type": "array",
                                                           "items": {"type": "string"},
                                                           "description": "可选条件列表 AND, 见上"}
                                        },
                                        "required": ["id", "agent_type", "task"]}},
                    "edges": {"type": "array", "description": "边列表 [{from, to}]",
                              "items": {"type": "object",
                                        "properties": {
                                            "from": {"type": "string"},
                                            "to":   {"type": "string"}
                                        },
                                        "required": ["from", "to"]}}
                },
                "required": ["name", "nodes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_dag",
            "description": (
                "全量更新已有 DAG 的节点和边。用户说'修改 DAG xxx 的所有步骤'/'重新定义 DAG xxx'时调用。"
                "与 create_dag 参数相同，但要求目标 DAG 已存在（否则建议用 create_dag）。"
                "update_dag 会完全替换旧版本，若只想改单个节点请用 patch_dag_node。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string", "description": "要更新的 DAG 名（必须已存在）"},
                    "nodes": {"type": "array", "description": "新的节点列表（全量替换）",
                              "items": {"type": "object",
                                        "properties": {
                                            "id":         {"type": "string"},
                                            "agent_type": {"type": "string"},
                                            "label":      {"type": "string"},
                                            "task":       {"type": "string"}
                                        },
                                        "required": ["id", "agent_type", "task"]}},
                    "edges": {"type": "array", "description": "新的边列表（全量替换）[{from, to}]",
                              "items": {"type": "object",
                                        "properties": {
                                            "from": {"type": "string"},
                                            "to":   {"type": "string"}
                                        },
                                        "required": ["from", "to"]}}
                },
                "required": ["name", "nodes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_dag",
            "description": (
                "删除指定名称的 DAG。用户说'删掉 DAG xxx'/'删除工作流 xxx'时调用。"
                "删除后 /api/dags 列表里不再出现该 DAG，操作不可逆，请在调用前向用户确认。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "要删除的 DAG 名"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "patch_dag_node",
            "description": (
                "修改 DAG 中单个节点的 label/task/agent_type，其余字段（id/depends_on 等）保持不变。"
                "用户说'把 DAG xxx 的节点 yyy 的任务改成...'/'修改某个步骤的 agent 类型'时调用。"
                "只允许修改 label、task、agent_type 三个字段；尝试修改 id/depends_on 等会被拒绝。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":       {"type": "string", "description": "DAG 名"},
                    "node_id":    {"type": "string", "description": "要修改的节点 id"},
                    "label":      {"type": "string", "description": "新的节点显示名（可选）"},
                    "task":       {"type": "string", "description": "新的任务描述（可选）"},
                    "agent_type": {"type": "string", "description": "新的 agent 类型（可选）",
                                   "enum": ["coder", "explorer", "researcher", "analyst",
                                            "tester", "shell", "writer", "critic"]}
                },
                "required": ["name", "node_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_dag",
            "description": (
                "立即启动指定 DAG 后台执行，返回 job_id。"
                "用户说'运行 DAG xxx'/'执行工作流 xxx'/'跑一遍 xxx'时调用。"
                "DAG 在后台异步执行，可通过 GET /api/dags/jobs/{job_id} 查询进度。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":   {"type": "string", "description": "要运行的 DAG 名"},
                    "inputs": {"type": "object", "description": "可选的输入参数（当前版本保留字段，暂不生效）"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_timers",
            "description": (
                "列出所有定时任务的摘要（id/name/type/schedule/enabled/last_run）。"
                "用户说'查看所有定时器'/'有哪些定时任务'/'列一下定时器'时调用。"
                "enabled_only=true 时只返回已启用的定时器。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled_only": {"type": "boolean", "description": "True=只返回已启用的定时器，默认 false"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_timer",
            "description": (
                "修改已有定时任务的字段（name/schedule/action_type/action_content/enabled 任意组合）。"
                "用户说'把定时器 xxx 的周期改成每小时'/'禁用/启用定时器 xxx'/'修改定时任务'时调用。"
                "只更新提供的字段，未提供的字段保持不变。id 是必填项用于定位定时器。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id":             {"type": "string", "description": "定时器 id（唯一标识）"},
                    "name":           {"type": "string", "description": "新名称（可选）"},
                    "schedule":       {"type": "string", "description": "新的 cron 表达式或 ISO 时间（可选）"},
                    "action_type":    {"type": "string",
                                       "enum": ["shell", "agent", "wechat_msg", "web_notify"],
                                       "description": "新的动作类型（可选）"},
                    "action_content": {"type": "string", "description": "新的动作内容（可选）"},
                    "enabled":        {"type": "boolean", "description": "True=启用 / False=禁用（可选）"}
                },
                "required": ["id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_timer",
            "description": (
                "删除指定定时任务。用户说'删掉定时器 xxx'/'取消定时任务 xxx'时调用。"
                "删除后任务不再执行且无法恢复，请先用 list_timers 确认 id。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "要删除的定时器 id"}
                },
                "required": ["id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_timer_now",
            "description": (
                "立即触发指定定时任务执行一次（不影响原有调度周期）。"
                "用户说'马上跑一次定时器 xxx'/'立即执行定时任务 xxx'时调用。"
                "执行结果会记录到 history，可通过 get_timer_history 查看。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "要立即执行的定时器 id"}
                },
                "required": ["id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_timer_history",
            "description": (
                "查询定时任务的执行历史记录（时间戳/状态/耗时/输出摘要）。"
                "用户说'查看定时器 xxx 的执行记录'/'定时任务 xxx 最近跑了几次'时调用。"
                "limit 控制返回最近 N 条记录，默认 20 条。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id":    {"type": "string", "description": "定时器 id"},
                    "limit": {"type": "integer", "description": "返回最近 N 条记录，默认 20", "default": 20}
                },
                "required": ["id"]
            }
        }
    },
]
