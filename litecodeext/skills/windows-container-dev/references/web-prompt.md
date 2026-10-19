# 从 LiteCode Web 发起 Windows 开发任务

先完成 Windows 一键接入，在 Web 的 Windows 电脑列表确认节点在线，并保存对应的 SSH 别名。两种别名可以不同，但必须指向同一台电脑。

在 Web 新建对话，将下面三个占位符替换后发送。新机需要安装和重启时，明确授权的机器范围；现有机器仅检查缺失依赖，不自行重启。

```text
请加载 windows-container-dev，在以下 Windows 上实际完成容器开发和 VS Code 调试，不要只给计划。

Windows SSH 别名：<SSH别名>
桌面回连节点别名：<节点别名>
预期 Windows 主机名：<主机名>

这是一台独立新建的测试机，允许安装依赖、启用 WSL 2、重启这台测试机，以及创建独立项目和容器。保留完成后的机器和项目。不操作其他电脑，不修改自动登录、锁屏、UAC、防火墙或节点能力设置。

由当前会话单独执行，逐关播报；系统命令使用已有 SSH，GUI 使用桌面工具。Windows 脚本使用 ssh_exec(shell="powershell")，检查真实退出码。安装超时先检查进程和日志，不盲目重复执行。优先校验并复用已有安装包。

WSL 内命令按技能说明分流输出并读取实际原生退出码，避免多层引号和 PowerShell 警告误判；实验中的 shell=wsl 入口需先完成目标机验证。长下载保存 PID、日志和退出码，使用短查询播报字节增量/进度；不要用一条十几分钟的 SSH 命令等待。镜像过大或网络过慢时可评估更小的兼容基础镜像，仍必须完成真实 VS Code Dev Container 和断点验收。

1. 核对 Windows 身份、SSH 自动启动、节点心跳与桌面；需要重启时记录前后启动时间，并检查 SSH 和节点恢复。
2. 在 Windows 内配置 WSL 2 与容器引擎，确认 Windows 内调用的 docker version/info、容器 ID 和容器内 uname。
3. 使用技能附带的最小项目，通过 VS Code 实际进入 Dev Container。
4. 在 app.py 的 result = add(a, b) 行设置断点并启动调试，从 Windows 浏览器异步请求 /add?a=2&b=3，实际确认 IDE 暂停以及变量 a=2、b=3；用 F5 继续同一次请求，保留浏览器完整 HTTP JSON 并核验 result=5。Shift+F5 是停止，不能代替继续。
5. 在 VS Code 编辑器修改 /health 的 greeting 为本轮唯一标记，保存后用 inspect_file 回读源文件，再运行确认 HTTP 返回该标记。运行容器内 python -m unittest checks.py，检查退出码。
6. 对界面上实际显示的一条完整文字，用 OCR expected_texts 严格核对。OCR 调用完成不等于识别通过。

每关结束立即写入 uploads/windows-dev-<当前会话>-<关卡>-evidence.json，保存实际工具/命令、目标、时间、原始工具响应，以及真实现存截图/下载副本的完整路径与实际 SHA-256；凭据脱敏，其余证据不改写。上下文裁剪、模型摘要不能代替原始证据；字段、版本、行号和测试数量取自对应同一次结果，缺失标未执行或待补验。JSON 可解析或统计 status 数量不算验收，不得捏造“监督者已核实”。

测试命令执行后保存真实退出码，例如 rc=$?; echo "UT_EXIT=$rc"; exit "$rc"，保留完整 Ran/OK 或失败输出、UT_EXIT 和上层原生退出码，不能用 echo 的退出码覆盖测试结果。

逐关标出通过、失败、未执行或待补验；有必需关卡未通过时，不报告整体成功。同一错误重复两次后先定位原因，再继续执行。收尾保留运行中的 VS Code、开发容器、机器和项目；临时清理只按实际执行记录汇报，未做写未执行，不凭空声称进程为0、无运行容器或镜像已清理。
```

Windows 内可选 WSL 2 + Docker Engine + VS Code Remote WSL / Dev Containers；宿主 Linux 的容器不算 Windows 内部验收。锁屏时先保留后台任务，桌面恢复后重新截图定位。

验收结果应留在当前 Web 对话中。记录会话 ID、实际提示词和截图路径，便于复测；日志中不保存接入码、密码、私钥或登录 Cookie。
