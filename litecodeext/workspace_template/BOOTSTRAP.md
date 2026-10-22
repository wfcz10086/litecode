# BOOTSTRAP.md — Session 启动检查清单

IDENTITY / SOUL / USER / TOOLS / HEARTBEAT 已注入本提示，不用再去读；上次 session 的记忆由系统按需召回。
开工前心里确认：用户名字和偏好、当前 workspace 路径、已有哪些服务在跑、上次未完成的任务（如有）。

## 重要规则

- 上下文文件 **只用 `update_profile` 工具读写**，不要 `read_file` / `write_file`
- 新的用户信息 → 更新 `USER.md`；新服务/路径/配置 → 更新 `TOOLS.md`；重要决策/架构/解决方案 → `save_memory`

## 大型项目实施规则（≥3 个源文件 / 跨多模块 / 用户说"写个 xx 工具/服务"）

⛔️ **禁止一次性批量产出源代码**，必须按下面流程走 (违反 = framework 强制中断)：

1. **第 1 步永远是 `write_file PLAN.md`**，列 5-12 个 step，每步只做一件事 (一个文件 或 一次 build)，写清 输入 / 输出 / 验证命令；
   第一个 step 通常是 init module + 最小可编译入口 (5-10 行) + 立即 build。
2. **每个 step 只写 1 个源文件** (≤ 250 行)，写完立即 `execute_shell` 跑 build/lint：
   Go `go build ./... && go vet ./...` / Python `python3 -c "import <mod>"` 或 `pytest -x <mod>` / Node `npm run build` 或 `tsc --noEmit`。
3. **build 通过才进下一个 step**，失败立刻 fix；每完成 3 个 step 重读 PLAN.md 检查偏离，`patch_file PLAN.md` 标进度。
4. **解耦**：每个文件单一职责，函数 < 50 行，文件 < 250 行，超了拆。

Framework 自动保护：同一 session 60 秒内连续 `write_file` 创建 ≥ 3 个新源文件 → 第 3 次返回 SYSTEM 警告要求停手跑 build；
subagent 同一 path `write_file` ≥ 4 次 → 自动循环检测 break。触发说明你违反了分步规则，不是 framework bug。

不算大型项目：单文件脚本 (< 200 行) 可一次写；已有项目改 1-2 个文件、修 bug / 加单功能不需要 PLAN，但仍要 build/test 验证。
