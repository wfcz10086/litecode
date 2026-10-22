---
name: e2e-self-test
description: >
  LiteCode 自我端到端自测：纯 Playwright 浏览器驱动覆盖全部主功能（登录 / 9 面板 /
  模型切换 / 发消息 SSE / 定时器 4×2 矩阵 / DAG 编排 / 文件上传 / 中断 / project_map），
  每个子功能各 1 条 case，每条都用按钮+表单交互真触发，输出 per-case 结果 + 截图。
  触发词: LiteCode 自测, 自我测试, e2e-self-test, 自检全功能, 测每个功能, 全功能回归.
---

# LiteCode 自测 skill

只用于测 **LiteCode 自己的 Web UI**。测用户自己的网页应用用 webapp-testing。

## 触发场景
- 用户问 "每个子功能都跑一下" / "全功能自测"
- 改完 LiteCode 前端代码后想验证 UI 没崩
- 检查容器升级后是否回归

## 调用形式

脚本在本 skill 目录 `scripts/run.py` (容器内 `/opt/litecode/skills/e2e-self-test/scripts/run.py`), 用 `execute_shell` 直接跑:
```
execute_shell(command="cd /opt/litecode/skills/e2e-self-test && python3 scripts/run.py quick --base http://127.0.0.1:18790 --pwd '<Web 登录密码>'",
              timeout=600)
```
- 模式: `quick` (核心 5 类, ~90s) / `normal` / `full` (~30min, 必须 `background=true` 跑, 再 `tail_log` 看进度) / `feature --feature <名字>` (单个子功能, 名字写错时脚本会列出可用名)。
- `--pwd` 用用户实际的 Web 登录密码, 不知道就问, 不要猜默认值。
- 退出码 0 = 全部 PASS, 1 = 有 FAIL。

也可以派给测试子代理: `spawn_agent(agent_type="tester", task="跑 LiteCode e2e-self-test quick 模式: <上面的命令>, 汇报 PASS/FAIL 和失败用例的截图路径")`。

## 安全规矩 (生产实例上跑时)
- 自测会真的建会话、发消息、建定时器/DAG。**只清理自己这次创建的对象**, 绝不按"当前会话"或列表批量删用户数据。
- 改了 web_ui 后端代码、还没重启生产的情况下, 应该对测试实例 (另起端口的第二实例) 跑, 不要为了自测去重启生产服务。
- 全功能 full 模式耗时长、会消耗模型调用, 用户没要求就跑 quick。

## 每个 sub-feature 的判定
脚本对每个 sub-feature 跑 3 件事：
1. **触发**：通过 Actor 在浏览器里点按钮 + 填表，真发请求
2. **观察**：等 toast / DOM 变化 / API 状态码（用 `_verify_via_api` 偷读）
3. **判定**：返回 PASS / FAIL + 现场截图 + result_reason

failure 时自动抓后端日志 (`docker logs litecode --tail 80`) 附在结果里; 容器外跑不了 docker 时看 `/tmp/litecode_workspace/logs/iteration_full.log`。

## 核心入口
- `scripts/run.py` — 主执行器 (`run_quick` / `run_full` / `run_feature`)
- `scripts/lib/actor.py` — Playwright Actor 封装
- `scripts/lib/cases.py` — 各 sub-feature case 函数
- `TEST_PLAN.md` — 测试矩阵说明

## 输出
- `/tmp/litecode_workspace/e2e_self_test_<ts>/cases/*.json`
- `/tmp/litecode_workspace/e2e_self_test_<ts>/shots/*.png`
- `/tmp/litecode_workspace/e2e_self_test_<ts>/summary.json` (pass / fail / results)

## 汇报
贴 `[self-test] DONE pass=X/Y fail=Z` 原文; 每个 FAIL 给 case_id、reason、截图路径。可以把失败截图 `vision_ocr(paths=[...])` 一次看完 (≤8 张) 再判断是 UI 问题还是用例过时。
