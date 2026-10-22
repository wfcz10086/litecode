# quickbench — LiteCode 10 分钟快测

30 道题，并发 8，通过网关 `/v1/chat/completions` 跑**完整的 agent 轮次**（工具、子代理、视觉都包括在内），
目标是 10 分钟内拿到一个 0–100 分，用来做**回归检查**和**模型 A/B**。
基线：GLM-5.3-Flash-5090 跑完 30 题用时约 4 分钟。

## 测什么

| 类别 | 权重 | 题数 | 内容 | 判分 |
|---|---|---|---|---|
| 第三方 | 35% | 10 | SimpleQA（openai/simple-evals，MIT）里的数字/年份题，需要联网查证；`tasks.py` 里记录了 CSV 行号 | 归一化后取 `最终答案:` 里的第一个数字比对 |
| 编码 | 30% | 8 | 迷你仓库修 bug：off-by-one、条件写错、缺边界、API 误用、跨两个文件的 bug、性能 bug、解析 bug、按 docstring 写功能 | 在宿主机上跑 pytest，并要求 `tests/` 和 `run_tests.py` 的哈希没变 |
| 框架专项 | 35% | 12 | 后台等待、前台 sleep 45 不被杀、报错后恢复、视觉路由、网页提示注入、YAML 单行编辑、20 万字符输出、并行子代理、已退出进程的自匹配、写 JSON 并校验、软链金丝雀不越界、两轮上下文 | 确定性判分，检查文件、日志和工具调用，详见 `tasks.py` |

GAIA（`gaia-benchmark/GAIA`）是 gated 数据集，2026-10-20 实测 huggingface.co 和 hf-mirror.com 都返回 401，所以改用 SimpleQA。

## 局限

- 题少，单次运行有 **±10 分**的正常波动（第三方题每题 3.5 分，框架题每题约 2.9 分）。要下结论就用 `--repeat 2/3`，或者看多次运行的趋势。
- **不能和公开榜单比**：SimpleQA 只挑了 10 道，而且允许联网；编码题是自己写的小仓库。
- 测的是“模型 + LiteCode 框架”这个整体。框架专项里有些题强模型也能把框架问题绕过去，所以单看分数没法完全分清问题出在哪一边，需要看报告里的工具和 TIMEOUT 列。
- GLM 基线已经接近满分（96.5），区分度主要体现在**掉分**（回归），对提分不敏感。之后可以换更难的题。

## 怎么跑

```bash
python3 scripts/quickbench/run.py                      # 全量，默认模型
python3 scripts/quickbench/run.py --model Qwen3.6-35B  # A/B：只给 qb- 会话设会话级模型，不改全局默认
python3 scripts/quickbench/run.py --only framework     # 只跑一个类别（或用逗号分隔题 id），报告写进 _partial/，不参与对比
python3 scripts/quickbench/run.py --repeat 2           # 每题跑 2 遍取平均
python3 scripts/quickbench/run.py --keep-files         # 保留任务目录用于排查
python3 -m pytest -q scripts/quickbench/test_graders.py   # 判分器单测，不需要网络，约 30s
```

- 网关 token 在运行时从 `config.json` 读取，不会打印也不会落盘。
- 会话 id 都是 `qb-xxxxxNNN`。任务文件放在 `workspace/quickbench/<run_id>/`；软链金丝雀放在 `workspace/quickbench/<run_id>_canary_outside`。
- `--model` 通过 `workspace/session_models.json` 给每个 qb 会话单独指定模型，网关每轮都会读这个文件。
- 正常结束时会自动清理：调用 `DELETE /v1/sessions/<sid>`（会顺带清掉后台进程）、删除会话目录、删除 web_ui 自动导入的 `web_sessions/qb-*.json`、删除 session_models 条目、删除任务目录、pkill 残留进程。
  **如果被 SIGTERM/SIGKILL 杀掉，清理不会执行**，需要手动做一遍：用 `POST /v1/sessions/<sid>/interrupt` 中断，再按上面的步骤删除。
- 并发上限固定为 8（生产环境同时在用）。单题有硬超时（默认 180s），超时后会调 interrupt，让服务端停下。
- 运行期间 web_ui 侧栏会临时出现 qb- 会话（web_ui 会自动导入 API 会话），清理后消失。
  `user_questions.json`、`telemetry/` 这类统计日志会留下 qb- 记录，脚本不去改这些共享文件。

## 怎么看报告

报告写到 `reports/quickbench/<run_id>/report.md` 和 `report.json`：

- **总分**：各类别通过率按 35/30/35 加权。
- **类别表**：每个类别的通过数、平均耗时、平均工具调用次数。
- **与上次对比**：跟 `reports/quickbench/` 下最近一次全量运行比较，列出各类别分数变化和翻转的题（✓→✗ / ✗→✓）。如果两次题集指纹不同，会给出提示。
- **失败列表**：每道失败题附一句原因。`超时(180s)` 表示整轮没跑完；`没走 vision_ocr`、`出现 TIMEOUT 工具结果` 这类原因一般指向框架问题；答案数字不对一般是模型或检索的问题。
- **全部题目**：列出每题的耗时、工具名×次数、TIMEOUT/ERROR 工具结果数。工具调用取自会话 JSON，再用 `iteration_full.log` 的 `[TOOL_EXEC]` 补全（日志按 `TRACE_START.session_id` 前 12 位对应到 trace_id）。
- **生产影响**：运行期间 `docker logs` 里的错误行数，区分工具结果回显和服务端级错误，以及同期非 qb 会话的轮次数。

## 文件

- `run.py`：调度、调用网关、解析日志、出报告、清理
- `tasks.py`：题目定义（夹具准备 + 判分绑定），包括 SimpleQA 题号和答案
- `coding_repos.py`：8 个编码题的迷你仓库
- `graders.py`：纯函数判分器
- `test_graders.py`：判分器单测，包括“原始代码必须失败、参考修复必须通过”的夹具自检
