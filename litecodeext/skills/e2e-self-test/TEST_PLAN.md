# LiteCode 全功能自测方案

通过读 `litecodeext/web_ui.py`（79 路由）+ `lib/tools/defs.py`（25 工具）+ `lib/agent/subagent.py`（8 子代理）+ HTML 面板（9 panel）+ skills/（86 个），生成的测试矩阵。

## A. 单功能测试（feature-isolated）

每条 = 1 个 sub-feature，Actor 真触发 + 偷读断言。

| ID | Feature | Trigger | Assert |
|---|---|---|---|
| **A01** | health | GET /health (API:18789) | status=ok |
| **A02** | auth/login | fill #lpwd → click .lb_ | cookie set |
| **A03** | auth/logout | POST /api/logout | cookie cleared |
| **A04** | auth/status | GET /api/auth/status | logged_in=true |
| **A05** | sessions/create | click #ncb | window.cur set |
| **A06** | sessions/list | GET /api/sessions | array len ≥ 1 |
| **A07** | sessions/delete | dblclick rename + del button | session removed |
| **A08** | sessions/export | click #exp-btn | download triggered |
| **A09** | chat/send | fill #mi → click #sdb | .msg.ai .mb populated |
| **A10** | chat/interrupt | click #ib while streaming | streaming false in 1.5s |
| **A11** | chat/multi-turn | send 2 messages | 2 .msg.ai bubbles |
| **A12** | upload/single | set_input_files #fi (1×1 PNG) | #pfp .pfc count=1 |
| **A13** | upload/multi | set_input_files (3 files) | 3 chips |
| **A14** | upload/oversize | upload 30MB | rejected (toast/no chip) |
| **A15** | memory/save | save_memory tool via chat | /api/memory/{sid} contains keyword |
| **A16** | memory/compress | click compressMem | toast 完成 |
| **A17** | memory/clear | click clrMem | /api/memory empty |
| **A18** | model/list | GET /api/models | array contains active |
| **A19** | model/switch | Settings panel button mdlSwitch | sbf-model-id text changes |
| **A20** | model/add | POST /api/models/add (test cleanup) | count++ |
| **A21** | model/delete | DELETE /api/models/{id} | count-- |
| **A22** | timer/create cron+agent | tmrShowAdd → fill → tmrAdd | /api/timers contains name |
| **A23** | timer/create once+shell | (same with type=once) | created |
| **A24** | timer/create cron+wechat | (same with action=wechat_msg) | created |
| **A25** | timer/create cron+web_notify | (same with action=web_notify) | created |
| **A26** | timer/run-now | tmrRun(tid) | run_count incremented |
| **A27** | timer/toggle | tmrToggle(tid, false) | enabled=false |
| **A28** | timer/copy | tmrCopy(tid) | tmrShowAdd form pre-filled |
| **A29** | timer/delete | tmrDel(tid) | timer removed from list |
| **A30** | timer/history | GET /api/timers/{id}/history | array |
| **A31** | timer/notifications | GET /api/timers/notifications | array |
| **A32** | dag/create | newDAG + dc-name fill + dc-ok | /api/dags/{name} stored |
| **A33** | dag/add-node | addDAGNodeQuick(coder) | canvas .dag-node count++ |
| **A34** | dag/connect | Shift+click pair | edge stored on save |
| **A35** | dag/save | saveDAG | toast 已保存 |
| **A36** | dag/run | runDAG | jobs/{job_id} status=done |
| **A37** | dag/run-button-disabled | runDAG → check #dag-run-btn | disabled=true |
| **A38** | dag/status-bar | runDAG → check #dag-status-bar | display=block |
| **A39** | dag/floating-indicator | runDAG → check #dag-floating-indicator | display=flex |
| **A40** | dag/AI-gen | aiGenDAG → fill desc → ok | /api/dags new dag created |
| **A41** | dag/jobs/list | GET /api/dags/jobs | array |
| **A42** | dag/job/detail | GET /api/dags/jobs/{job_id} | agents + final_output |
| **A43** | project_map/status | GET /api/map_status | enabled=true |
| **A44** | project_map/force | POST /api/map_force_update {} | ok=true |
| **A45** | project_map/auto | write_file via chat → verify watcher | file in map (or workspace) |
| **A46** | projects/create | UI prj-name + prj-type → createProject | /api/projects contains name |
| **A47** | projects/context-put/get/del | full CRUD on /api/projects/{pid}/context | round-trip |
| **A48** | projects/decisions | POST decisions × 2 → DELETE 1 | count -1 |
| **A49** | wechat/status | GET /api/wechat/status | available=bool |
| **A50** | wechat/qr | GET /api/wechat/qr | image or 404 |
| **A51** | workspace/files | GET /api/workspace/files | tree |
| **A52** | workspace/preview | GET /api/workspace/preview?path=... | content |
| **A53** | workspace/download | click download | Content-Disposition |
| **A54** | bgprocs/list | GET /api/bgprocs | array |
| **A55** | bgprocs/kill | DELETE /api/bgprocs/{pid} | 200 |
| **A56** | stats | GET /api/stats | total_tokens etc |
| **A57** | tombstones | DELETE /api/tombstones/{sid} → list | sid not in tombstones |
| **A58** | config | GET /api/config + PATCH partial | persistence |
| **A59** | inject/path-traversal | GET /api/file?path=../../etc/passwd | 4xx |
| **A60** | inject/cron 6-seg | tmrAdd with 6-seg cron | rejected |
| **A61** | inject/once-past | once with past time | rejected |
| **A62** | inject/no-auth | no cookie /api/sessions | 401 |
| **A63** | inject/oversize-upload | 30MB upload | 413 |

## B. 工具调用测试（per-tool via chat send_chat）

每条 = 让 chat 触发一种工具，背后 grep SSE 帧确认 task_exec.detail 含工具名。

| ID | Tool | Prompt | Expected detail substring |
|---|---|---|---|
| **B01** | execute_shell | 跑 `pip show fastapi` | execute_shell |
| **B02** | list_bg | 后台 sleep + list_bg | list_bg |
| **B03** | kill_bg | kill 上一条 | kill_bg |
| **B04** | web_fetch | 抓 https://www.python.org/ 标题 | web_fetch |
| **B05** | vision_ocr | 上传中文 PNG + 让识别 | vision_ocr |
| **B06** | web_search | 搜 FastAPI 文档 | web_search |
| **B07** | browser_search | 用 Playwright 搜 example.com | browser_search |
| **B08** | browser_read | 读 https://httpbin.org/headers | browser_read |
| **B09** | github_search_issues | 搜 fastapi import error | github_search_issues |
| **B10** | stackoverflow_search | 搜 python typeerror | stackoverflow_search |
| **B11** | search_code_error | 诊断一段 traceback | search_code_error |
| **B12** | read_file | 读 README.md | read_file |
| **B13** | write_file | 写 e2e_demo/test.txt | write_file |
| **B14** | patch_file | 改 e2e_demo/buggy.py off-by-one | patch_file |
| **B15** | apply_blocks | 多文件原子事务 | apply_blocks |
| **B16** | get_tree | 列 e2e_demo 目录树 | get_tree |
| **B17** | find_files | 找 *.md | find_files |
| **B18** | search_code | search TODO | search_code |
| **B19** | find_symbol | 找 class TimerManager | find_symbol |
| **B20** | load_skill | load anti-ai-tell-audit | load_skill |
| **B21** | list_skills | 列 5 个 skills | list_skills |
| **B22** | spawn_agent | spawn coder | spawn_agent |
| **B23** | update_profile | 更新偏好 | update_profile |
| **B24** | save_memory | 记简洁回答偏好 | save_memory |
| **B25** | self_reflect | 让 critic 自审上次回答 | self_reflect |

## C. 子代理测试（per-agent_type）

| ID | agent_type | Task | Expected output |
|---|---|---|---|
| **C01** | explorer | 扫 litecodeext/core 找用 sqlite 的文件 | filenames |
| **C02** | researcher | 搜 FastAPI SSE backpressure 综述 | 数据 + URL |
| **C03** | coder | 写 FastAPI /healthz endpoint 并跑测 | code + test pass |
| **C04** | critic | 审上一段代码 + 给改进 | 3 条建议 |
| **C05** | writer | 写 800 字三端架构文 | ≥800 字 markdown |
| **C06** | tester | 跑 pytest tests/test_core.py | passed |
| **C07** | planner | 拆"做一个 BTC 报告"为 3-5 步 | step list |
| **C08** | shell | 跑 ls -la / 一行 grep | output |

## D. 串联链路测试（chain integration）

每条 = 多个 feature 串成一条业务链，验真实端到端可用。

| ID | Chain | 步骤 | 验证 |
|---|---|---|---|
| **D01** | search→save→recall | researcher 搜 BTC 价格 → save_memory → 新会话问 BTC 价格 | 答含搜出的数据 |
| **D02** | write→map→query | write_file 新 .py → 等 map watcher → /api/map_status 含新文件 | yes |
| **D03** | spawn→pipe→write | researcher 搜 → writer 合并写 file → coder 转 docx | docx 落盘 |
| **D04** | timer→fire→notify | 建 cron+web_notify 1min → 等 75s → /api/timers/notifications ≥1 | yes |
| **D05** | timer→fire→agent_chat | 建 cron+agent → 等 130s → 该 sid history 多 1 条 assistant | yes |
| **D06** | upload→vision→summary | 上传中文 PNG → vision_ocr 识别 → writer 摘要 | 摘要含图片关键字 |
| **D07** | DAG→run→view-output | plan_full DAG run → /api/dags/jobs/{id}.agents[].output_preview ≥80 字 | yes |
| **D08** | chat-create-DAG | 让 agent: "创建一个 DAG ..." → 验 /api/dags 多一条 | yes |
| **D09** | chat-create-timer | 让 agent: "每 1 分钟提醒 ..." → 验 /api/timers 多一条 | yes |
| **D10** | error→search→fix | 抛 ImportError → search_code_error → patch_file | repo 自愈 |

## 模式

- **quick** (~90s): A01, A05, A09, A12, A19, A37, A38, A39, A43
- **normal** (~10min): + A22..A29 + B01..B05 + C01,C03 + D02
- **full** (~45min): 全部 A + B + C + D

## 执行入口

```bash
# Pure Python (no agent involvement) — 自查
python3 litecodeext/skills/e2e-self-test/scripts/run.py quick

# Via agent chat
"跑 e2e-self-test full" → spawn tester subagent → calls run.py full

# Single feature
python3 litecodeext/skills/e2e-self-test/scripts/run.py feature --feature dag_run_button
```

## 自检循环（self-fix loop）

每条 FAIL 后：
1. `docker logs --tail 80 litecode` 抓后端栈
2. 决定根因属哪类（UI selector / DOM event / API contract / agent loop / 时序）
3. 改源码（fail-fast，禁 try/except 吞错）
4. `docker cp` 同步到容器（dev）or `docker compose up -d --build`（prod）
5. 仅复测该 feature 的 case，PASS 才更新 summary
6. 多次复测仍 FAIL → 升级到 chain 测试看上下游是否被影响
7. 写入 `REFLECT.md`：根因属哪类，以后如何防

## 待补的"看到执行情况"功能（基于 v1.14 起步）

- ✅ DAG run 按钮跑时置灰（v1.14 已加）
- ✅ DAG 状态栏显示进度（v1.14 已加）
- ✅ 全局浮动指示器（任意面板下都看得到）（v1.14 已加）
- ⏳ DAG 节点点击 → 弹层显示 output_preview（待加）
- ⏳ DAG 完成 → 状态栏出 "查看输出" 按钮 → 打开 final_output 详情（待加）
- ⏳ Timer history 弹层显示每次 run 的 stdout/stderr（待加）
- ⏳ Agent loop 实时输出流到 chat 的 reasoning 折叠块（已有部分）
- ⏳ 工具调用展开后默认显示前 200 字 result，不仅折叠（待加）
