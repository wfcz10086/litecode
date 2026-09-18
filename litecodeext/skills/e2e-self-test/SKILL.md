---
name: e2e-self-test
description: >
  LiteCode 自我端到端自测：纯 Playwright 浏览器驱动跑一遍全部主功能（登录 / 9 面板 /
  模型切换 / 发消息 SSE / 定时器 4×2 矩阵 / DAG 编排 / 文件上传 / 中断 / project_map），
  每个子功能各 1 条 case，每条都用按钮+表单交互真触发，输出 per-case 结果 + 截图。
  触发词: 自测, 自我测试, e2e-self-test, 跑一遍, 自检全功能, 测每个功能, 自动回归.
---

# LiteCode 自测 skill

## 触发场景
- 用户问 "每个子功能都跑一下" / "全功能自测"
- 改完前端代码后想验证 UI 没崩
- 检查容器升级后是否回归

## 调用形式
```
spawn_agent(tester, mode="self_test", task="跑 e2e-self-test skill 的 quick 模式")
```
或主聊天里直接说"跑一下 e2e-self-test"，agent 自动 dispatch。

## skill 提供的 actions
| action | 描述 | 估时 |
|---|---|---|
| `quick`    | 仅跑核心 5 类（登录 / 面板 / 发消息 / 切模型 / DAG run 按钮置灰） | ~90s |
| `full`     | 全 10 章节 ~68 case (同 reports/e2e_v2_*) | ~30min |
| `feature <name>` | 单个子功能（dag/timer/upload/memory/project/wechat/...） | ~30s-2min |

## 每个 sub-feature 的判定
脚本对每个 sub-feature 跑 3 件事：
1. **触发**：通过 Actor 在浏览器里点按钮 + 填表，真发请求
2. **观察**：等 toast / DOM 变化 / API 状态码（用 `_verify_via_api` 偷读）
3. **判定**：返回 PASS / FAIL + 现场截图 + result_reason

failure 时自动 `docker logs litecode --tail 80` 抓后端日志附在结果里。

## 核心入口
- `scripts/run.py` — 主执行器
- `scripts/lib/actor.py` — Playwright Actor 封装（来自 `tools/web_actor.py`）
- `scripts/lib/cases.py` — 各 sub-feature case 函数

## 输出
- `/tmp/litecode_workspace/e2e_self_test_<ts>/cases/*.json`
- `/tmp/litecode_workspace/e2e_self_test_<ts>/shots/*.png`
- `/tmp/litecode_workspace/e2e_self_test_<ts>/summary.json`

## 范例（agent 调用）
```python
from skills.e2e_self_test.scripts.run import run_quick
result = run_quick(base="http://127.0.0.1:18790", pwd="CHANGE_ME_PASSWORD")
print(f"pass={result['pass']}/{result['total']}")
# 失败的看 result['fails']，每条含 case_id / shot / reason / docker_log_tail
```
