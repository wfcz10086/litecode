# BOOTSTRAP.md — Session 启动检查清单

> 每次新 session 开始时，按顺序执行以下步骤。

## 启动顺序

1. **读取身份** — `IDENTITY.md`：确认自己是谁
2. **读取灵魂** — `SOUL.md`：行为原则与边界
3. **读取用户** — `USER.md`：用户信息、偏好、语言习惯
4. **读取环境** — `TOOLS.md`：当前 session 路径、服务、环境变量
5. **读取记忆** — `MEMORY.md`（如存在）：上次 session 的关键信息
6. **读取心跳** — `HEARTBEAT.md`：是否有定时任务需要恢复

## 完成后确认

- [ ] 知道用户名字和偏好
- [ ] 知道当前 workspace 路径
- [ ] 知道已有哪些服务在跑（端口/URL）
- [ ] 记住上次 session 的未完成任务（如有）

## 重要规则

- 上下文文件 **只用 `update_profile` 工具读写**，不要 `read_file` / `write_file`
- 遇到新的用户信息 → 立即更新 `USER.md`
- 遇到新服务/路径/配置 → 立即更新 `TOOLS.md`
- 重要决策/架构/解决方案 → 用 `save_memory` 写入 `MEMORY.md`

## 大型项目实施规则（≥3 个源文件 / 跨多模块 / 用户说"写个 xx 工具/服务"）

> ⛔️ **禁止一次性批量产出源代码**。无论模型多么有冲动一次写完，必须按下面流程走。

### 强制流程 (违反 = framework 强制中断)

1. **第 1 步永远是 `write_file PLAN.md`**, 列 5-12 个 step, 每步只做一件事 (一个文件 或 一次 build).
   - 每个 step 写清: 输入 / 输出 / 验证命令 (go build / pytest 等)
   - 第一个 step 通常是 `init module + 最小可编译 main.go (5-10 行)` + 立即 `go build`
2. **每个 step 只允许写 1 个源文件** (≤ 250 行).
3. **写完单文件立即 `execute_shell` 跑 build/lint 验证**:
   - Go: `cd <root> && go build ./... && go vet ./...`
   - Python: `python3 -c "import <mod>"` 或 `pytest -x <mod>`
   - Node: `npm run build` / `tsc --noEmit`
4. **build 通过才能进入下一个 step**, 失败必须立刻 fix.
5. **每完成 3 个 step 重读 PLAN.md** 检查偏离, 必要时 `patch_file PLAN.md` 标记进度.
6. **解耦原则**: 每个文件单一职责 (1 个 struct/class 或 1 组紧密相关函数), 函数 < 50 行, 文件 < 250 行. 超了拆.

### Framework 自动保护 (你的强约束)

- **同一 session 60 秒内连续 `write_file` 创建 ≥ 3 个新源文件** → 系统会在第 3 次返回 SYSTEM 警告, 要求立即停手跑 build.
- **subagent 同一 path `write_file` ≥ 4 次** → 自动循环检测 break.
- 触发这些保护说明你违反了分步规则, 不是 framework bug.

### 哪些不算"大型项目"

- 单文件脚本 (< 200 行) — 可以一次写
- 已有项目改 1-2 个文件 — 不需要 PLAN
- 修 bug / 加单功能 — 不需要 PLAN, 但仍要 build/test 验证
