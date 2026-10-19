---
name: windows-container-dev
description: 在完整 Windows 虚拟机中配置容器开发环境，用 VS Code Dev Containers 实际开发、命中断点并验收。适用于新 Windows 安装后的端到端开发调试测试。
---

# Windows 容器开发与调试验收

## 优先完成开发调试

先用一次简短状态检查确认系统身份、现有 WSL/Docker、VS Code/扩展、镜像和项目；已有内容直接复用。当前关卡所需依赖齐备就继续，不重复安装，也不展开与阻塞无关的工具边界测试。

本例是 Python HTTP 服务与断点调试，优先复用现有缓存。最小项目默认使用 `python:3.12-slim-bookworm`、容器用户 root，并通过 `postCreateCommand` 安装 debugpy；本轮已实际验证容器内 Python、debugpy、VS Code Server 与调试服务运行。它与完整 devcontainers 镜像的用户和预装依赖不同，不能只替换镜像名就宣称通过；IDE 断点仍单独验收。图形欢迎页、弹窗与文件夹信任按当前截图处理，不关闭全局安全检查。

下载镜像期间可准备独立的 VS Code 安装/扩展，但同一文件、安装器或桌面仍串行操作。每关用真实结果决定下一步，先完成容器与 IDE 断点，再做额外的环境兼容性测试。基础镜像已能使用时，不等待另一份更大镜像才继续。

## Web 聊天入口

用户可以在 LiteCode Web 新建对话，按 [Web 任务提示词](references/web-prompt.md) 填入 Windows 的 SSH 别名、桌面节点别名和预期主机名后发送。收到这类任务时加载本技能并执行；每关保留工具原始结果。Web 的消息已发送、SSH 命令已提交、应用窗口已打开，都不能代替最终验收。

用户要求更复杂的业务、写代码运行、Word 修改及复制审阅时，使用 [业务工作流验收](references/business-workflow.md)：依次完成订单报价、运行与负向校验、`.docx` 图形编辑、真实剪贴板粘贴及差异审阅。每次只推进当前短关卡，复用已有环境。

先确定三个身份：虚拟机宿主机的 SSH 别名、Windows 的 SSH 别名、Windows 回连节点别名。分别核对主机名；Linux 宿主机执行的命令和 Windows 桌面操作应在记录中标出各自目标。

用户已授权创建测试机时，继续创建和安装。先检查宿主机可用内存/磁盘、KVM、AMD 的嵌套虚拟化状态及端口占用。使用独立目录、系统盘、容器名和端口；缓存的安装 ISO 可以复用。现有机器的系统盘不能作为“全新安装”验收的证据。资源不足或必须停止现有业务才能继续时，报告具体缺口。

AMD KVM 测试机需要嵌套 WSL 时，可参考 [本轮实际验证的配置](references/amd-vm-profile.md)；按目标机器重新验收，不照搬到 Intel 或未授权机器。

已有 Windows 虚拟机变更 CPU、安装源或重建容器前，先读 [虚拟机变更检查](references/vm-safety.md)，检查镜像实际的安装完成标记和持久化挂载，并准备停机快照。dockurr v6.06 缺少 `windows.boot` 时会把已有数据盘当作未完成安装；不能直接 `up --force-recreate`。运行 `assets/check_vm_recreate.py --storage <系统盘目录> --snapshot <停机快照目录>`，检查不通过时先修复前提。不要凭空创建标记，也不要复制正在运行的系统盘充当可靠快照。仓库完整操作记录为 `docs/WINDOWS_VM_OPERATIONS.md`。

优先检查本地缓存：容器运行镜像、Windows 安装 ISO 和现有 Windows 系统盘分别确认用途。搜索安装盘应覆盖缓存目录及实际挂载盘；不能只检查一个目录就重新下载。完整 ISO 校验大小、哈希与安装介质结构后复用，下载半成品不能当作安装完成。续传旧下载前核对已有数据与同一源文件对应区段的哈希；保留校验后的完整 ISO 和可信安装包，供后续新机复用。

使用 dockurr/windows 的 OEM 流程时，可复用 `assets/prepare-windows.ps1`：校验缓存的 Git/VS Code 安装器签名，在安装用户的交互会话中执行，再向宿主共享目录写入实际系统信息。控制端看到就绪后才生成并投递新接入命令，避免系统安装期间接入码已过期；该脚本等待 `join.ps1` 后执行。此入口保留 Python/OpenSSH 给一键接入脚本安装，以便验证新系统初始化。

## 两种操作通道

- 文件、包安装、服务、容器和日志用 `ssh_exec` 或 `windows_system`。Windows 原始脚本使用 `ssh_exec(shell="powershell", cmd="原始脚本")`；工具负责编码。PowerShell 5.1 多句用换行/分号；不能使用 `&&`。每次检查 `rc`、退出码和实际回读，调用返回不代表命令成功。
- 实验中的 Windows Bash 入口为 `ssh_exec(shell="wsl", wsl_distro="Ubuntu", cmd="原始 Bash")`，需要时显式传 `wsl_user="root"`。本轮 Windows 原生验证出现超时，暂不用于开发主线。工具通过 stdin 投递脚本，分流 stdout/stderr，保留 WSL 实际退出码；Bash 是否遇错即停由调用者明确指定。后续修复验证时可用 `execute_shell` 调用 `python3 /opt/litecode/skills/windows-container-dev/assets/run-wsl.py --alias <Windows别名> --distro Ubuntu --file <控制端Bash文件> --timeout 40 --session <当前会话>`，独立进程读取新代码，无需重启其它人的任务。
- GUI 用 `windows_desktop`。快捷键是 `action="hotkey", keys="enter"` 等白名单值，没有 `press` 动作。一般设置 `compress=true`；OCR 识别用原图坐标。英文代码/数字先查看 `available_languages`，仅在已安装时选 `ocr_language="en-US", timeout=90`。识别验收必须传 `expected_texts=["完整预期行"]`；`ocr_mismatch` 是失败，不能以文件回读正确替代 OCR 通过。文件内容另用真实文件回读核验。

SSH 与桌面通道可以串行混用。SSH 启动的 GUI 可能处于后台会话，应通过桌面通道 `launch_app` 启动交互应用，再依据窗口列表确认前台窗口。前台窗口和编辑区焦点分别确认；菜单仍打开时先关闭，再点击编辑区。坐标点击需要当前图片宽高。缩放后检查实际缩放比例，不能仅凭快捷键已发送就认定放大成功。

每次 Windows GUI 调用显式传目标节点 `alias`。`desktop_exec` / `desktop_screenshot` / `desktop_click` / `desktop_key` / `desktop_type` 只操作 LiteCode 控制容器的 Linux `:99` 桌面，没有 Windows 自动路由；不能把 Windows 截图上的坐标发给这些工具。Windows 截图、点击、按键、输入全部使用 `windows_desktop`。

复杂 GUI 阶段可交给 LiteCode 的单个 `operator`：`spawn_agent(agent_type="operator", alias="目标节点", background=true, task="当前关卡及已有状态")`。它的工具集绑定 Windows；主会话负责前提检查和证据审查，操作员运行期间不争用同机桌面，也不再派第二个同机操作员。补充上下文使用 `agent_control(action="send", id=..., message=...)`；中断父会话会同时请求停止后台操作员，不能用来只暂停父任务。

快捷键按实际白名单调用：回车是 `enter`；文件可用 `ctrl+o` 或当前截图中的文件树打开；`ctrl+shift+p`、`ctrl+g`、`f9`、`f5` 已支持。当前 `ctrl+p` / `ctrl+h` 未支持，遇到拒绝后使用已有菜单/快捷键，不反复尝试。远程容器内打开源文件时使用 `/workspaces/...` 路径并核对编辑器文件名。

文档按段导航/选择可使用 `ctrl+up`、`ctrl+down`、`ctrl+shift+up`、`ctrl+shift+down`。复制末段时先在文档获得编辑焦点，`ctrl+end` 到末尾，再 `ctrl+shift+up` 选择上一段；用新截图及 `ctrl+c` 后的 `clipboard` 实际内容确认选区，不能按视觉换行猜整段是否选全。调用前确认节点组件和控制端工具定义均已加载支持版本。

新系统的 WSL 未安装是预期的检查结果。把依赖探测与安装分开执行，避免开头的预期错误中止后续复制/安装；不存在的日志先用 `Test-Path` 判断。PowerShell 的 `-ErrorAction SilentlyContinue` 不会覆盖所有原生程序重定向错误，需要时用 `try/catch` 明确区分缺失依赖与实际失败。WSL 输出出现 NUL/UTF-16 字符时，可在当前调用中设置 `$env:WSL_UTF8='1'` 并确认本版支持；不要把该变量永久写入系统，也不要只删除 NUL 后宣称中文内容完整。Microsoft 自带的 [WSL 日志脚本](https://github.com/microsoft/WSL/blob/master/diagnostics/collect-wsl-logs.ps1) 使用同一进程环境变量。

Windows 位于 KVM 虚拟机内时，嵌套开关或 `svm=on` 本身不保证 WSL 2 能启动。核对实际 QEMU 选项及来宾行为；`hv-passthrough` 会覆盖其它 `hv-*` 设置，不能只看到 `-hv-avic` 就认定已关闭。显式配置必须包含所需依赖，嵌套 Hyper-V 的合成定时器需要 `hv-stimer-direct`。这是兼容性排查线索，不能替代真实启动验收；所有配置试验沿用停机和快照检查。

`wsl: Nested virtualization is not supported on this machine` 也可能是 WSL 内再次运行虚拟机的警告，不能直接等同于 WSL 2 无法启动。[微软配置说明](https://learn.microsoft.com/en-us/windows/wsl/wsl-config)中的 `.wslconfig` / `[wsl2] nestedVirtualization` 控制这一能力，普通 Docker 容器并不需要在 WSL 中再启动 KVM 虚拟机。先检查发行版列表与实际原生进程退出码，再运行 Linux `uname` 验收。PowerShell 5.1 在 `$ErrorActionPreference='Stop'` 下用 `2>&1` 捕获原生 stderr 可能把警告变成终止错误；需要时用 `Start-Process` 分别重定向 stdout/stderr 到临时文件，等待进程并读取 `ExitCode`，按实际字节编码解码输出。不要因此反复安装发行版、重装系统或忽略真正的非零退出码。

通过 Windows 生成给 Linux 使用的 shell/Python 文件时，用 `new Text.UTF8Encoding($false)` 与 `WriteAllText` 写无 BOM 的 UTF-8，并采用 LF；PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会加 BOM，可能使 bash 把第一行的 shebang 当成不存在的命令。安装脚本出错后先查真实进程和日志，避免后台安装仍在进行时再次启动。安装依赖前核对 Windows/WSL UTC；发现时钟偏差时先修正时间，不禁用 TLS、签名或仓库有效期检查来绕过错误。

下载镜像、安装扩展等耗时工作应保存进程标识、日志与最终退出码，以短查询汇报实际进度，避免单次 SSH 阻塞十几分钟。超时不等于远端停止；先查已有进程，禁止重复安装或拉取。镜像下载很慢时，确认进度和现有缓存；本例仅需 Python/调试器，可在保持容器与断点验收标准的前提下评估更小的兼容镜像。不能根据单个镜像层的进度推断整个下载完成时间。

锁屏/未登录时保持 SSH、文件及系统操作；GUI 返回不可用后等待用户解锁，重新截图再继续。旧坐标、旧窗口句柄、旧 OCR 不能直接续用。Windows 回连通过出站 HTTPS 工作；没有公网 IP 时先验证回连，SSH 直连失败不能当作整个节点不可用。

## 完整验收关卡

1. **系统**：取得新机的 Windows 版本、主机名、启动时间与首次桌面截图，再记录“安装完成”。下载镜像、容器启动和 Web 安装页面只证明安装进行中。新建机器使用独立凭据，接入命令及私钥写入受限文件。
2. **接入**：实际运行一键安装，验证 SSH 服务的自动启动、代理心跳、版本更新、首次截图、OCR 及文件回读。验证私网/NAT 下的出站接入。新机安装所需的重启属于安装流程；现有机器的重启沿用用户对该机器的授权。
3. **容器**：在 Windows 内核对 WSL 2 与嵌套虚拟化，安装所选容器引擎。在 Windows 内执行 `docker version`、`docker info` 以及容器内 `uname`。宿主 Linux 上单独跑一个容器不能替代“Windows 内运行容器”的验收。
4. **VS Code**：安装 VS Code、Python/Debugger 和 Dev Containers 扩展。可通过 `launch_app(name="vscode")` 启动默认安装目录中的程序，用 `ctrl+shift+p` 打开命令面板。Docker Engine 安装在 WSL 内且 Windows 无 Docker CLI 时，先通过 WSL 扩展打开对应发行版中的项目路径、确认终端能够调用 Docker，再执行 Reopen in Container，避免在纯 Windows 上反复触发 Docker 缺失提示。[VS Code 官方说明支持通过 WSL 扩展使用发行版内的 Docker](https://code.visualstudio.com/remote/advancedcontainers/docker-options)。使用本技能的 `assets/project/` 作为最小项目；依据 VS Code 的容器状态及容器终端确认所处环境。
5. **调试**：打开 `app.py`，在 `result = add(a, b)` 行设置断点（示例第 24 行；`ctrl+g` 定位行，`f9` 切换断点），`f5` 启动调试。从 Windows 浏览器异步访问 `/add?a=2&b=3`，随后切回 VS Code，确认真正暂停、当前行及局部变量 a=2/b=3。不要用同一会话的同步 SSH HTTP 请求堵住后续 GUI 操作。暂停期间请求未返回属于预期；使用 `f5` 继续同一次请求，取得浏览器的完整 HTTP JSON 并核验 `result=5`。`shift+f5` 是停止调试，不能代替继续验收。
6. **修改与回读**：通过 VS Code 将 `/health` 返回的 greeting 改为本轮唯一标记，保存，使用 `inspect_file` 回读对应 Windows 源文件，确认它与 WSL `/mnt/c/...` 和容器工作区是同一挂载文件；再重启本轮调试服务并用 Windows 内 HTTP 请求确认新标记，避免命中另一份项目或旧服务。运行 `python -m unittest checks.py`。OCR 可核对终端实际显示的完整启动行；先放大并重新截图，再传 `expected_texts`。保留断点截图、退出码、HTTP JSON 和文件回读；这四类证据分别验证 GUI、测试、运行结果和落盘内容。

如果已进入 WSL 项目，但 Dev Containers 日志仍报告 Windows PATH 找不到 `docker`，先检查本版扩展 `package.json` 的 `dev.containers.executeInWSL` / `dev.containers.executeInWSLDistro`。本轮 0.469.0 实际 schema 为 `scope=application`：在 Windows 用户设置里启用并指定已安装的 Ubuntu，项目或 WSL 远端设置不能代替；保留其它设置并备份。随后检查新的真实日志与容器状态，不以设置文件已写入作为连接成功。维护者对[此开关的说明](https://github.com/microsoft/vscode-remote-release/issues/9194)确认它控制 Docker 命令在哪执行。

每关完成后立即在 `uploads/windows-dev-<session>-<gate>-evidence.json` 保存结构化证据：会话、目标、时间、实际工具/命令、原始工具响应、关卡结论，以及截图和下载副本的完整路径与实际 SHA-256。写入前确认文件真实存在，不补写猜测的文件名或哈希。凭据脱敏，其余证据不改写；长输出可单独保存并由 JSON 引用。上下文中的 `[trimmed]`、模型摘要和口头回忆不能作为原始证据；缺失部分标为“待补验”，不能从摘要重建。

原始响应使用独立的 `assets/capture-evidence.py` 采集，不手抄或加入衍生字段：在控制端用 `execute_shell` 执行 `python3 /opt/litecode/skills/windows-container-dev/assets/capture-evidence.py --session <当前会话> --tool-call-id <真实调用ID> --output windows-dev-<会话>-<关卡>-raw.json`，多个调用重复传 `--tool-call-id`。脚本只读会话快照/WAL，保留工具调用及响应原文，输出到 uploads 的新文件，不覆盖已有证据；不会回放或清理 WAL。工具范围为 `windows_desktop`、`windows_system`、`ssh_exec`；缺失、裁剪或可能含凭据时拒绝提取，先补取完整且不含秘密的结果。另写结论文件引用该原始证据，不修改原文。

HTTP 字段、文件行号/大小/哈希、运行版本、测试数量和 OCR 语言/断言必须取自对应的同一次工具结果；不要混用其它轮次或模型记忆中的值。JSON 能解析、统计出若干 `passed` 只说明格式正确，不能证明验收通过。没有明确的监督者审查记录，不得写“监督者已核实”。

测试脚本要保留被测命令的真实退出码，例如 `docker exec <容器> python -m unittest checks.py -v` 后执行 `rc=$?; echo "UT_EXIT=$rc"; exit "$rc"`。同时保存完整测试输出、`UT_EXIT` 和上层原生进程退出码；不能把最后一条 `echo` 的成功当作测试成功。

逐关记录通过、失败、未执行或待补验；任一必需关卡失败、未执行或证据不足，整体验收就不通过。如果某关失败，保留已完成结果，报告失败关卡和原始错误。相同错误重复两次后先定位工具/环境问题；不通过重复点击或改口宣称成功。收尾保留新虚拟机、项目及运行中的 VS Code/开发容器，不为生成“无残留”结论停止开发环境。

临时清理必须依据本轮创建记录，只删除确证的临时文件，并按实际执行的工具响应记录清理结果；未做的清理写“未执行”。没有相应进程/容器查询结果，不得宣称“VS Code 进程为 0”“无运行容器”或镜像已清除。微软 WSL 安装目录及其 PATH、VS Code/容器引擎的正式文件均属于要保留的开发环境；不能因为临时桥接文件曾放在某目录，就删除该目录或移除其 PATH 项。任务暂停后重新核对现场状态继续，避免重复创建已存在的容器或断点。

参考：[VS Code Dev Containers](https://code.visualstudio.com/docs/devcontainers/containers/)、[Docker Windows 安装与虚拟化要求](https://docs.docker.com/desktop/setup/install/windows-install/)、[WSL 故障排查](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting)。
