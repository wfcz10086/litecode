---
name: litecode-debug-protocol
description: >
  LiteCode 专用调试协议。当 LiteCode agent 本身出现问题时使用，
  包括：vLLM 400 错误、工具调用 JSON 格式错误、session history 损坏、
  迭代中途停止、agent 无响应、空回复、工具反复失败、内存压缩异常。
  先看日志与 telemetry 再下结论。
  关键词：vLLM 400、arguments、JSON format、session、history、迭代停止。
---

# LiteCode 调试协议

## 第一步: 取证 (不看日志不下结论)

| 看什么 | 在哪 |
| --- | --- |
| 主日志 (每轮工具调用/报错) | `tail_log(path="/tmp/litecode_workspace/logs/iteration_full.log", lines=200)` |
| 工具调用 / 失败 / 空回复 / SSE / 子代理 / DAG / 技能命中 | `/tmp/litecode_workspace/telemetry/` 下 `tools.jsonl` `failures.jsonl` `empty_response.jsonl` `sse.jsonl` `subagent.jsonl` `dag.jsonl` `skills.jsonl` (JSONL, 用 `tail -n 50 文件 \| jq -c .` 看) |
| 容器 stdout (启动失败、未捕获异常) | 宿主机 `docker logs litecode --tail 200` |
| 会话历史 | `sessions_dir` (config.json → paths, 默认 `/tmp/litecode_workspace/sessions`) |

上面是容器内默认路径; 实际以 config.json 的 `paths` 段为准。拿到真实报错原文和时间点后再对照下面的速查。

**修 LiteCode 自身代码时:** 改完先在测试实例验证 (不要直接重启生产 web_ui), 跑 `python3 -m pytest -q tests` 全量; 前端改动必须 Playwright 真人模拟验收 (见 webapp-testing)。

## 常见问题速查

### vLLM 400: arguments must be in JSON format

**根因**：session history 里有 dangling tool_calls，其 arguments 是截断的非法 JSON。

**位置**：`core/history_utils.py` → `_sanitize_history()` (子代理另有一份在 `lib/agent/subagent.py`)

**验证**：
```bash
# 检查session文件
python3 -c "
import json
data = json.load(open('/tmp/litecode_workspace/sessions/SESSION_ID.json'))
for m in data['msgs']:
    for tc in m.get('tool_calls') or []:
        fn = tc.get('function', {})
        args = fn.get('arguments', '')
        if isinstance(args, str):
            try: json.loads(args)
            except: print('BAD:', m['role'], fn.get('name'), repr(args[:50]))
        elif not isinstance(args, dict):
            print('WRONG TYPE:', type(args))
"
```

**修复**：`_sanitize_history()` 已在框架层自动处理。如果仍然触发，说明有新的 arguments 类型没有覆盖。

---

### Agent 中途停止，没有总结

**根因**：for 循环耗尽迭代次数（max_iterations）但任务未完成。

**配置位置**：`config.json` → `agent.max_iterations`（以实际文件为准, 不要凭记忆报数值）

**检查**：
```bash
grep "max_iterations\|task_timeout" /path/to/config.json
```

**临时处理**：用户说"继续"时，agent 会重新加载 session history 继续执行。

**相关机制**：用户在回合进行中可以"插话" (消息在下一步接进上下文, 不停止任务; "立即插话" 会放弃当前正在执行的工具) 或点停止。排查"agent 忽略了我中途说的话"时, 先确认用户用的是插话还是新开一轮。

---

### ITER_ENFORCE 频繁触发

**根因**：模型在第一轮调工具时没有输出前置文字（text_chars < 10）。标记定义见 `core/itrace.py`。

**影响**：每次 enforce 消耗一次迭代预算（已有补偿逻辑：enforce 时 max_iter+1）。

**优化**：在 AGENT_PROMPT.md 中强调"工具调用前必须先说一句话"。

---

### Memory 压缩导致上下文丢失

**症状**：新 session 开始后，agent 不记得上一个 session 的关键错误和修复方案。

**配置**：`lib/session.py`
```python
MAX_HISTORY = 500   # 未压缩时上限
RECENT_KEEP = 300   # 压缩后保留条数
```

**保护**：`Errors & Corrections` 和 `Key References` section 在压缩时不截断。

---

### (exit -15) 被当成错误重试

**根因**：`core/executor_v4.py` 的 `classify_error` 对负 exit code 直接返回 `ok`，但 stderr 合并时可能带有前次进程的输出。

**正确理解**：
- `exit -15` = SIGTERM 正常终止，**不是错误**
- `exit -9` = SIGKILL 强制终止，**不是错误**
- `[STDERR_WARNINGS - exit 0]` = 命令成功有警告，**不是错误，不重试**

---

## Session 诊断工具

```bash
# 查看最新 session 的消息结构
python3 << 'EOF'
import json, glob, os
sessions = sorted(glob.glob('/tmp/litecode_workspace/sessions/*.json'), key=os.path.getmtime, reverse=True)
if sessions:
    data = json.load(open(sessions[0]))
    msgs = data['msgs']
    print(f"Session: {data['sid']}, msgs: {len(msgs)}")
    for i, m in enumerate(msgs[-10:]):
        role = m['role']
        tc = len(m.get('tool_calls') or [])
        content_len = len(m.get('content') or '')
        print(f"  [{len(msgs)-10+i}] {role}: content={content_len}c, tool_calls={tc}")
EOF
```
