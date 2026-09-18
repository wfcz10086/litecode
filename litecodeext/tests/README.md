# LiteCode 测试套件

所有测试放在这个目录下。测试文件通过 `Path(__file__).parent.parent` 指向源码根 (`litecodeext/`)。

## 文件

| 文件 | 用途 | 运行方式 |
|------|------|---------|
| `test_full.py` | 25 场景在线测试 + 38 离线单元测试 | `python3 tests/test_full.py [--offline \| --scenario N \| --tag T]` |
| `test_v123_fixes.py` | 模型切换 / 子代理工具注入等 API 级回归 | `python3 tests/test_v123_fixes.py http://host:18789 token` |
| `swe_bench.py` | SWE-bench Verified 精简评测 runner | `python3 tests/swe_bench.py --smoke \| --limit N` |

## 离线单测 (不依赖 server, ~2 分钟)

```bash
cd litecodeext
python3 tests/test_full.py --offline
```

覆盖 38 项:
- blackboard / orchestrator / memory_index / itrace / multi_agent (核心模块)
- thinking_adapter (vLLM/OpenAI/Ollama 5 后端兼容)
- flow_control (LOOP_BREAK / BATCH / SLIDING_WINDOW)
- tool_dispatch (bg_procs 分桶 / keep_alive / LRU / skill stats / drafts)
- config._LIVE / subagent 工具注入 / memory LLM 超时降级
- 默认模型 / models 数组 / 自动委派 / entrypoint 占位符
- reasoning 多后端 / memory 异步化 / Web UI 推理折叠
- compress thinking 策略 / extract_content 多场景

## 在线场景 (需要 server 起着, ~60 min)

```bash
cd litecodeext
python3 tests/test_full.py               # 全部 25 个
python3 tests/test_full.py --scenario 14 # 单场景
python3 tests/test_full.py --tag search  # 按 tag
python3 tests/test_full.py --skip 1,2    # 跳过
```

场景分组:
- A 基线 (2) · B 搜索 (4) · C 代码 (5) · D 子代理 (4)
- E 长文 (3) · F 记忆 (2) · G 技能/基建 (3) · H 错误 (1) · I 日期 (1)

报告: `/tmp/openclaw_workspace/logs/test_full_report.md`
Trace: `/tmp/openclaw_workspace/logs/iteration_full.log`
