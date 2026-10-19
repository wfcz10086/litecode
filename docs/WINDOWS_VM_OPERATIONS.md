# Windows 虚拟机操作记录

## 2026-10-19：重建测试机容器触发重新安装

用户要求将教训记录到本地，避免再次发生。本记录不包含凭据。

新机 `win11-devtest` 曾完成 Windows 11 Pro 安装，原生主机名 `WIN-DFV77UAJJ2R`，SSH 为 Running / Automatic，LiteCode 出站节点在线，桌面截图成功。Web 会话 `web-xxxx` 实际完成缓存包校验、WSL 功能启用、WSL 3.0.3 MSI 安装和重启指令。启用虚拟机平台后，来宾未恢复启动；容器和 VS Code 断点尚未验收。

排障时直接重建了容器以变更 CPU 中断选项，没有先检查镜像的完成标记、清理分支，也没有先保存停机快照。运行镜像将原盘当作未完成安装清理，重新创建了 `storage/data.img`，来宾进入 Windows Setup。这是本次操作错误；当时先暂停新机取证，随后恢复同一 QEMU 的安装过程，没有再次重建容器。原有 `win11` / `gpu-vm` 未重启或重建。

该重建命令还使用了 `--timeout 15`，缩短了原配置的两分钟正常关机宽限。后续不得用缩短宽限替代健康停机和快照；嵌套虚拟化实验之前应先完成首次健康停机，核对标记并备份，再进行会影响启动的修改。

实际检查的 dockurr/windows v6.06 运行脚本：

- `/run/install.sh` 的 `needsInstall()` 在普通数据盘路径下需要 `hasData && hasBootMarker` 才直接启动已有系统。
- `hasCompletedInstall()` 对普通数据盘同样要求完成标记；盘存在本身不足以证明镜像认定安装完成。
- 未完成安装分支可能调用 `discardPrevious()`，清理 `data.*`、`windows.*`、固件与 NVRAM 文件。不能把 `up --force-recreate` 当作无条件保盘操作。
- `/run/power.sh` 的 `markWindowsBooted()` 依赖 `ready()` 才创建标记。缺失标记的具体原因还需查明，不能把缺失标记伪装成系统未安装，也不能无证据手造完成状态。

此前宿主 KVM 出现 `Unhandled WRMSR(0xc0010115)`。曾将宿主运行时 `ignore_msrs` 从 N 临时设为 Y，安排 180 秒自动恢复；该试验没有恢复启动，已实际确认恢复 N，未固化到宿主配置。该日志是线索，不能单凭它认定根因。

恢复安装后的新系统主机名是 `WIN-JM2PTI2AMKB`，节点别名 `amd-devtest-20261019-r2`。已取得原生 Windows 身份、SSH Running / Automatic、在线心跳和新的桌面检查；首次安装的证据另存为 `win11-devtest-first-install-*`，不能与替换系统混用。可选缓存预装脚本曾因 `New-NetFirewallRule` 缺少必填 `DisplayName` 等待交互输入，已补全参数；当前这次接入含人工修复，不能当作缓存预装全自动通过的证明。

恢复系统的首次健康停机使用原配置的 120 秒宽限。实际日志是 `Received SIGTERM ... ACPI shutdown`、约 18 秒后 `Shutdown completed!`，运行程序自动生成 `windows.boot`，容器退出码为 143。检查程序最初只接受 0，导致快照前置检查误报；已按实际运行程序行为纠正，保留首次检查失败记录。143 也不能单独证明健康停机，仍须结合关机完成日志、标记及系统盘无人占用验证。

另有 AMD / Hyper-V 中断兼容线索：[QEMU 上游问题 3468](https://gitlab.com/qemu-project/qemu/-/work_items/3468)。本次为新机追加 `-hv-avic,-hv-apicv` 后容器重建触发了安装流程，因此尚不能据此宣称兼容问题已解决。

第二次 Web 实测会话 `web-xxxx` 在替换系统上再次完成包校验、WSL MSI 安装和功能状态回读；启用 WSL/VMP 后仍卡在 Windows Boot Manager，节点离线、SSH 握手失败。已停止本轮模型与独立观察器，保存 `windows-container-dev-web-round2-verdict.json`，没有把后续人工修复算作本轮自动重启通过。

进一步核对实际 `/run/proc.sh` 发现默认 `HV=Y` 添加 `hv_passthrough`。[QEMU 官方文档](https://www.qemu.org/docs/master/system/i386/hyperv.html)明确说明该模式覆盖其它 `hv-*` 设置，因此命令行里出现 `-hv-avic` 不足以证明实际禁用了该功能。后续专用测试机的候选方案是 `HV=N` 加显式 Hyper-V 功能，包括嵌套所需的 `hv-stimer-direct` 及依赖；完成真实 Windows/WSL 启动前不得写成已解决。该试验保留健康的 `snapshots/pre-wsl-r2-20261019` 和卡住来宾的停机副本，后者是非正常关机现场，不能冒充健康恢复点。

新机冷启动还观察到 RTC/时区造成约 8 小时钟差。不能只凭 `LastBootUpTime` 字符串变化判定重启，应结合宿主实际关机/启动记录、系统运行时间、重启后的心跳和新截图；时钟差需另行修复。

显式 Hyper-V 试验已确认 Windows 能在 VMP/WSL 功能 Enabled 的情况下重新进入桌面：原生系统身份仍为 `WIN-JM2PTI2AMKB`，SSH 为 Running / Automatic，WSL 版本 3.0.3，节点在线且新桌面检查成功。实际 QEMU 参数不含 `hv_passthrough`，含 `hv-stimer-direct=on`；完整证据为 `win11-devtest-r2-explicit-hv-native.txt` 与 `win11-devtest-r2-after-explicit-hv-check.json`。这是 Windows 恢复启动的结果；Linux 发行版、容器和 IDE 调试仍需后续验收。

后续 Web 继续执行已实际启动 Ubuntu 24.04.5，`uname` 返回 `6.18.54.1-microsoft-standard-WSL2`、退出码 0；证据为 `win11-devtest-r2-wsl-linux-native.txt`。此前 PowerShell 在“不支持嵌套虚拟化”的 stderr 警告处中止，但 Ubuntu 已经注册为 WSL 2；该警告涉及在 WSL 内再次运行虚拟机，不能直接当成 WSL 2 启动失败。技能已补充原生进程分流输出、读取真实退出码以及避免重复安装的说明。

Web 自动化助手两次读到页面异步初始化期间的旧 `cur`，导致提交监听超时，实际任务已经送入另一新会话。已按完整用户消息精确匹配恢复实际会话 ID，没有重发任务。后续新建会话时直接使用 `POST /api/sessions` 的响应 ID，并等待页面切到该 ID；不能只等待 `cur` 非空或与刚读取的值不同。这个问题已定位到测试助手的时序，不能无证据归为产品会话丢失。

## 本轮初始化/恢复验收的重启现场

用户允许新机真实开关机后，LiteCode Web `web-xxxx` 在 `WIN-JM2PTI2AMKB` 完成锁屏测试：交互会话1发起锁屏，锁屏后节点 Shell 和经 `win666` 跳板的 SSH 均成功回读独立测试标记，GUI 如实返回 `desktop_unavailable`。预约于 08:16:40 UTC 的 Windows 重启后，SSH 握手持续失败、节点离线。监督者读取专用虚拟机控制台，实际画面停在 Windows Boot Manager / Windows for Docker 启动画面，并非已进入桌面；不能用容器 Running 或 QEMU `VM status: running` 代替 Windows 启动成功。

现场证据 `windows-lifecycle-vm-diagnostic.json`、`windows-lifecycle-reboot-console-v3.json` 和 `windows-lifecycle-reboot-console.png`。原始首次进程故障测试约130秒自动恢复，救援任务 `used=false`；已把代理的定时补拉从5分钟改为1分钟，修复后尚待复测。模型直接探测控制容器 `127.0.0.1:2223` 没有诊断效力，Windows SSH 的该地址属于跳板侧，不能据此称 Windows 端口拒绝连接。

本轮监督者人工恢复只针对同一专用容器执行 `docker restart --time 120`，执行前核对项目、持久化存储与 `windows.boot`，执行后核对容器ID/镜像/挂载未变。此步骤独立记录为人工恢复，不算 LiteCode 无人干预重启验收通过；未重装系统、未操作旧机。

随后通过 LiteCode Web 启动已审查的修复作业：健康系统在10.65秒内正常停机，保存独立快照 `snapshots/pre-powercycle-20261019`，既有检查脚本逐字节验证通过，才变更专用测试机的两项环境参数 `ARGUMENTS: "-action reboot=shutdown"`、`TZ: "Asia/Shanghai"`。系统盘 inode 保持不变，CPU/安装源不变。宿主作业脚本与进度保存在专用目录 `lifecycle-powercycle-job.py` / `lifecycle-powercycle-progress.json`。作业启动助手沿用了旧的 actor 提示语；真实执行入口以 Web `windows-lifecycle-powercycle-start-submission.json` 和会话原始工具调用为准，不能据提示语误判为监督者直接执行。

[QEMU 官方参数](https://www.qemu.org/docs/master/system/qemu-manpage.html)支持将来宾重启事件改为退出；此处结合原有容器 `unless-stopped` 策略，避免在同一 QEMU 实例里执行卡住的重启路径。它是这台嵌套虚拟化测试机的恢复方案，不是 Windows 代理在物理机上自启的前提。RTC 使用 `base=localtime`，容器 TZ 必须与来宾时区匹配；本机中国标准时间实测修复后 UTC 不再慢8小时。首次重启失败与人工恢复证据保留。修复后LiteCode发出Windows原生重启指令（08:31:19 UTC，延时10秒），同一容器自动重启计数0→1、StartedAt08:31:41；Windows原生bootUTC08:31:45.8、uptime37秒，sshd Running/Automatic，session1代理自动拉起，实际节点Shell和新桌面截图通过。备用救援08:32:07记录used=false，未介入。报告 windows-lifecycle-lifecycle-fast-20261019-Status-2d8f1dc1.json、新图 Node-Desktop-a8275b7f0af04305ab2e6d1fe4ef0a39-screen.png，原始工具证据 windows-lifecycle-reboot-old-native-evidence.json；这次才是修复后无人工拉起的端到端重启通过。


用户随后要求暂停，并认为十分钟足够做快速验收。本轮网络故障从08:34:25 UTC开始，09:12:33 UTC收尾，约38分钟；监督者通过未锁定的虚拟机控制台恢复网卡，再通过SSH停止并删除本轮SYSTEM worker与备用网络恢复任务。代理未手工重启即自动回连，09:13:30原生节点Shell确认Ethernet Up、sshd Running/Automatic、测试任务为空、原标记文件保留。此为短时断网后的自动回连通过，不是一小时自动恢复通过；控制台辅助输入/截图时序失败记录保留，人工收尾与LiteCode执行证据分开。用户暂停后不自动重新开启长测试。证据 windows-lifecycle-short-offline-cleanup.json、windows-lifecycle-paused-native-evidence.json，独立结论 windows-lifecycle-supervisor-verdict.json。

## 操作前的必要检查

1. 核对 SSH 宿主别名、容器名、Compose 项目、Windows 主机名及节点别名，所有证据明确属于同一台机器。
2. 读取实际容器的 mounts 和 Compose 解析结果。系统盘、固件变量和状态文件必须位于预期的持久化目录。不要输出展开后的完整环境配置，其中可能包含密码。
3. 核对镜像版本及当前运行脚本对“已安装”的判定。检查真实系统盘和 `windows.boot`；缺失标记时先诊断，禁止直接重建。不同版本应重新核对标记名称，不能套用其它版本的假设。
4. 在健康状态下完成正常关机，确认 QEMU 已退出，再保存系统盘、固件/NVRAM、状态文件及 Compose 的停机快照。快照不得是正在写入的系统盘副本、硬链接或指向原盘的软链接。
5. 使用技能中的 `assets/check_vm_recreate.py` 做操作前检查；通过后才继续既定的重建步骤。脚本不会修改磁盘、生成完成标记或替代关机/快照流程。
6. 安装介质与运行配置分开管理。安装完成后按镜像支持的方法退出安装路径，确认后续启动选择已有系统盘。修改安装源、CPU 参数或容器配置前重新检查这些前提。
7. 计划内重启后，检查新的启动时间、SSH 自动服务、重启之后的新心跳和新截图。旧的 online 状态可能尚未超时，不能独自证明重启恢复。
8. 连续 SSH 握手失败时检查来宾控制台及宿主日志；不要一直更换相同探活命令，也不要把宿主 Docker 内的容器当作 Windows 内开发容器。

## Claude 原始创建记录

项目目录 `/opt/litecode/.claude/` 只有命令、代理配置及本地设置。该项目的完整 Claude 会话位于 `/home/ubuntu/.claude/projects/-opt-litecode/`。

- `c73f6d23-3c81-47d0-ba45-4e2da37c49e9.jsonl` 第 1455 行，时间 `2026-10-16T07:47:54.644Z`，包含原始 Windows 创建文件。
- `8d60d66f-95f8-4c24-a2e0-0d2ac433dbc9.jsonl` 第 39827 行有相应记录。
- 原始模板：`/tmp/claude-1000/-opt-litecode/8d60d66f-95f8-4c24-a2e0-0d2ac433dbc9/scratchpad/vm/docker-compose.yml` 和 `install.bat`。
- 原流程使用 `dockerproxy.net/dockurr/windows:latest`、`./win11:/storage`、`./oem:/oem`、KVM、6 核 / 6GB / 100GB，OEM 使用缓存安装包准备 OpenSSH、Python、Git、VS Code。

历史文件用于确认创建方法，不代表历史安装包版本、开放端口或安全设置适合直接照抄。重新部署时固定镜像摘要，校验安装包来源/签名，使用独立凭据及受限入口。
