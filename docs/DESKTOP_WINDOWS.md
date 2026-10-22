# LiteCode Windows 桌面版：打包、发布与更新

> 状态（2026-10-21）：WSL 版的系统包、代码更新包、更新/回滚脚本已在 Linux 上模拟验证通过；Windows 实机安装测试进行中，通过前不对外宣称可用。

## 一、用户看到的是什么

和普通桌面软件一样：

- 一个离线安装包 `LiteCode-Setup-<版本>.exe`（约 1.6 GB），装时**不联网下载任何东西**。
- 双击 →「下一步」→ 填模型接口 → 桌面、开始菜单出现 LiteCode 图标，开机自启。
- 默认登录密码 `admin`（只对本机 127.0.0.1 开放），登录后在「设置 → 修改密码」改掉。
- 安装包自带 WSL 官方安装包，离线可装：只开启还没开的系统功能，真的新开了才要求重启一次。
- 使用说明：开始菜单「LiteCode 使用说明」，或托盘菜单「使用说明」（源文件 `packaging/windows/assets/guide.html`）。
- 设置页只列你自己填的模型（示例配置里的其他模型 key 都是占位符，不带进桌面版）。
- 右下角托盘常驻，菜单：打开 / 启动服务 / 停止服务 / 检查更新 / 查看日志 / 打开数据文件夹 / 使用说明 / 接入本机操控（已接入时为「重新接入本机操控」）/ 退出。
- 能操控本机：文件直接生成到桌面/文档/下载，命令行优先；需要看界面时截图、点击（见「六之二、操控本机」）。
- 窗口是 Edge/Chrome 的应用模式（独立窗口、无地址栏），地址 `http://127.0.0.1:18790`，**只对本机开放**。
- 「设置 → 应用」里可以卸载，卸载会一并删除 LiteCode 子系统和数据。

## 二、两种运行方式

同一次构建产出三样东西，代码、配置格式、更新包完全一致：

| 产物 | 给谁 | 怎么跑 |
|---|---|---|
| Docker 镜像 | Linux 服务器（生产部署方式）、已装 Docker Desktop 的用户 | `docker compose up -d` |
| WSL 系统包 + 离线安装包 | 普通 Windows 用户（默认推荐） | 双击安装，托盘管理 |
| 代码更新包（约 9 MB） | 以上两种 | 托盘「检查更新」或命令一键更新 |

**为什么 Windows 默认用 WSL 而不是 Docker Desktop**：少一整层。不用装 Docker Desktop（650 MB、首次启动慢、会要求把账户加进 docker-users 组并注销重登、大公司商用收费），只依赖 Windows 自带的 WSL。实测 Docker 版安装器在测试机上就卡在了 WSL/Docker 这些环节。

## 三、WSL 版的结构

- 子系统名 `LiteCode`，根文件系统 = 发行镜像（公开导出、压平、隐私扫描过）+ WSL 启动配置：
  - `/etc/wsl.conf`：不启用 systemd、默认 root、**不把 Windows PATH 拼进来**（避免 Windows 的 python/node 抢先）。
  - `/etc/litecode.env`：镜像原有的环境变量（WSL 不认 Docker 的 ENV）。
  - `/usr/local/bin/litecode-wsl-start`：相当于容器入口；先卸掉 WSLg 挂成只读的 `/tmp/.X11-unix`，自己的 Xvfb `:99` 才能起来。
  - `/usr/local/bin/litecode-apply-update`、`litecode-rollback`：代码更新与回滚。
  - `/etc/litecode-version`（代码版本）、`/etc/litecode-rootfs-version`（系统包版本）、`/etc/litecode-configured`（首次配置完成标记）。
- 托盘程序持有一个隐藏的前台进程 `wsl.exe -d LiteCode -u root -- litecode-wsl-start`：它在，服务就在；意外退出 5 分钟内最多自动拉起 3 次。停止 = `wsl --terminate LiteCode`。
- WSL2 默认 NAT 网络，子系统里监听的端口只转发到 Windows 的 localhost，局域网访问不到。
- 数据都在子系统的虚拟磁盘里（`%LOCALAPPDATA%\LiteCode\distro\ext4.vhdx`）：对话、记忆、工作区、配置。

## 四、更新机制（两级）

| | 代码更新（常见） | 系统包更新（少见） |
|---|---|---|
| 什么时候 | 只改了 LiteCode 代码、提示词、技能 | 底层依赖变了：系统库、浏览器、Python 包 |
| 下载 | 代码包约 9 MB | 完整安装包约 1.6 GB |
| 过程 | 停服务 → 子系统内原地替换 `/opt/litecode` → 启动 | 运行新安装包：备份数据 → 注销旧子系统 → 导入新的 → 恢复数据 |
| 保留 | `config.json`、`skills/drafts`、全部数据 | 对话、记忆、工作区、配置、草稿技能、微信登录 |
| 失败 | 新版起不来 → 自动回滚到 `/opt/litecode.prev` | 任何一步失败都不删备份，提示备份位置 |

- 托盘启动 2 分钟后查一次，之后每天一次；有新版**只提示，用户确认才更新**。
- 判断依据是 `latest.json`：`version` 比本机代码版本新 → 代码更新；`rootfs_version` 比本机系统包新 → 完整更新。
- 所有下载都校验 sha256，断点续传。

已验证（Linux 模拟）：更新后版本切到新版、`config.json` 和草稿技能保留、旧版留作 `.prev`；回滚后回到旧版且新版期间改的配置保留。

## 五、发布：一键脚本

```bash
# 只改了代码（最常见）：1–2 分钟出代码包并上传，所有桌面用户托盘提示更新
bash packaging/release.sh 1.10.16.92

# 底层依赖变了：重建镜像 + 系统包 + 完整安装包，约 10 分钟
bash packaging/release.sh 1.10.17.0 --full
```

脚本步骤：
1. `scripts/make_public_release.sh` 导出公开版并自检（无密钥 / 无私人数据 / 无真实主机），不过就停。
2. 打代码包（`litecode/` + `entrypoint.sh`，来自公开导出）。
3. `--full` 时：以 `litecode:v12-r1` 为底换上公开版代码 → `docker export | docker import` 压平成单层（底层旧代码/状态不随包带出）→ `privacy_scan.py`（生产 config 里的真实密钥 + 内部 IP/主机名，命中即停）→ 加 WSL 配置导出根文件系统 → NSIS 打离线安装包。
4. 上传到发布目录，写 `latest.json`（版本号 + 各文件 sha256 + 大小）。

发布目录由 `packaging/windows/main.go` 的 `BaseURL` 决定（构建时可用 `-ldflags "-X main.BaseURL=..."` 覆盖），建议放在只能按文件名下载、关闭目录列表的静态目录里。

## 六、构建产物与代码位置

| 位置 | 内容 |
|---|---|
| `packaging/windows/*.go` | 托盘 + 安装逻辑（Go，交叉编译成单个 exe，约 7 MB）：`main.go` 入口与命令行参数、`setup.go` 安装/升级、`wsl.go` 子系统与服务管理、`tray.go` 托盘与两级更新、`config.go` 首次配置、`util.go` 下载/校验/提权 |
| `packaging/windows/installer.nsi` | NSIS 离线安装包脚本（普通用户权限，装到 `%LOCALAPPDATA%\LiteCode`，带卸载） |
| `packaging/windows/privacy_scan.py` | 发行镜像隐私扫描 |
| `packaging/windows/assets/` | 图标 |
| `/opt/build/litecode-win/` | 构建中间产物（不进仓库） |

安装器命令行参数（自动化测试、批量部署用）：

```
LiteCode.exe --setup                      重新跑安装/升级流程
LiteCode.exe --setup --unattended --password <密码> --model-url <地址> --model-name <模型> --model-key <key> --no-tray
LiteCode.exe --selftest                   把 WSL / 子系统 / 版本 / 服务状态写到 selftest.txt
LiteCode.exe --local-control              只做本机操控接入 (OpenSSH + 桌面代理), 可与运行中的托盘并存
LiteCode.exe --setup --no-local-control   安装时不接入本机操控
LiteCode.exe --remove-local-control       移除本机桌面代理 (卸载程序调用; OpenSSH 保留)
LiteCode-Setup-<版本>.exe /S              静默解包（不自动进入配置，配合上面的 --unattended）
```

退出码：`3010` 需要重启（已登记重启后自动继续），`2` 失败（看 `launcher.log`）。

## 六之二、操控本机

桌面版就跑在用户自己的 Windows 电脑上，分两层操控这台电脑：

**第一层：命令行（默认就有，不需要任何安装）**

- 服务启动时检测到自己在 WSL 里（`LITECODE_DESKTOP=1`，或 `/proc/version` 含 microsoft 且能找到 `/mnt/c/Windows/.../powershell.exe`），用 `powershell.exe` 查一次 Windows 用户名与 桌面/文档/下载 路径，在系统提示 `{{ENV}}` 段注入一小段本机说明（代码 `litecodeext/lib/desktop_env.py`，启动后内容固定，不破坏 KV 前缀缓存）。服务器版 docker / 普通 Linux 不注入。
- 模型因此会：生成的 pptx/pdf/xlsx 直接写到 `/mnt/c/Users/<用户>/Desktop|Documents|Downloads` 并回复 Windows 路径；Windows 命令用 `powershell.exe -NoProfile -Command`（服务进程 PATH 末尾追加了 Windows PowerShell / System32，不抢 Linux 的 python/node）；打开文件/文件夹/程序用 `Start-Process`；不再让用户运行节点注册命令。
- 真机实测（v2.0.2 + 本改动）：桌面建 txt 7 秒 2 次工具调用；做一页 PDF 放文档目录 30 秒；打开下载文件夹 4 秒 1 次工具调用。

**第二层：看界面 / 点鼠标（安装时默认接入）**

- 安装配置的最后一步请求一次管理员权限（UAC），做三件事（托盘「接入本机操控 / 重新接入本机操控」重做同样的事；`LiteCode.exe --local-control` 命令行同效）：
  1. **OpenSSH Server**：没有 sshd 才装——优先用安装包自带的微软官方 Win32-OpenSSH（sha256 + 微软 Authenticode 签名校验），不行再用 Windows 可选功能；设为开机自启；防火墙只放行本地子网（WSL 子系统所在网段属于本地子网），可选功能自带的放行所有地址的规则停用；新装的只允许密钥登录（`PasswordAuthentication no`），装过的记在 `%ProgramData%\LiteCode\openssh-installed-by-litecode.json`。本机已有 sshd 时只确保开机自启并运行，不改配置和防火墙。
  2. **桌面代理**：复用标准节点接入（`windows_node_bootstrap.ps1`），节点别名 `this-pc`，控制端 `http://127.0.0.1:18790`（经 WSL localhost 转发，桌面版只监听本机），默认开启 shell 能力（模型在 WSL 里本来就能调 powershell.exe，不额外放权）。代理以当前用户在登录后的桌面会话里运行（截图/OCR/点击）。首次需联网下载 Python 与依赖（本机已有 Python 3.12/3.13 则复用）。
  3. **SSH 别名**：代理上线后登记 `this-pc-ssh`（Windows 用户 + 接入时生成的私钥，私钥只在子系统里），地址 = WSL 默认网关（镜像网络时为 127.0.0.1），每次服务启动刷新；实测一次 `cmd /c echo`。
- 脚本由子系统里的 `litecodeext/lib/desktop_local_node.py prepare` 生成（一次性接入码 10 分钟有效），托盘经 `-EncodedCommand` 交给提权进程，不落盘；参数经 base64(JSON) 传进子系统（`wsl.exe --` 走 shell，会吃掉 Windows 路径里的反斜杠）。日志 `%LOCALAPPDATA%\LiteCode\local-control.log`，结果 `local-control.json`。
- 任何一步失败都不算安装失败，只提示；UAC 里点「否」即跳过。UAC 里换成别的管理员账户时不装代理（代理会落到那个账户，操控不到当前桌面）。`--no-local-control` 安装时跳过。
- 重新接入：旧 `this-pc` 先撤销并删除记录，新代理装好后在本机卸载旧代理（新公钥已在，旧代理的卸载不会动共享的 sshd）。

**怎么关掉**

- 只停桌面代理：Web「Windows 节点」里撤销/卸载 `this-pc`，或卸载 LiteCode（卸载程序会请求一次管理员权限移除本机代理）。
- 关 SSH：`Stop-Service sshd; Set-Service sshd -StartupType Disabled`；不想要可在「设置 → 系统 → 可选功能」卸载 OpenSSH 服务器，或运行 `C:\Program Files\OpenSSH\uninstall-sshd.ps1`（自带包装的）。卸载 LiteCode 不会删 OpenSSH（系统组件，可能另有用途）。

**安全说明**

- SSH 只允许密钥登录、只放行本机/局域网；私钥只在 LiteCode 子系统里（`.secrets/windows_nodes/ssh/`），模型看不到路径（节点列表已过滤）。
- 桌面代理只连本机 `127.0.0.1:18790`，下发的组件全部验签（与普通节点同一套签名清单）。
- 操控能力与"在 WSL 里跑 powershell.exe"同权限：都是当前 Windows 用户；SSH 走管理员组公钥（安装用户须是管理员）。

## 七、排障

- 日志：托盘「查看日志」，或 `%LOCALAPPDATA%\LiteCode\launcher.log`（安装器）和 `service.log`（服务）。
- `wsl --status` 在部分机器上 WSL 已装好也返回非 0，安装器改为依次看 `wsl --version`、`wsl --status`、WSL 程序文件，任一成立即视为已装（实测踩过）。
- 电脑从没开过 WSL：安装器会请求一次管理员权限开启，并提示重启；重启后自动继续。BIOS 未开虚拟化时 WSL 起不来，只能进 BIOS 打开。
- 子系统手工检查：`wsl -d LiteCode -u root -- cat /etc/litecode-version`。

## 八、历史：先做过 Docker Desktop 版

最早一版安装器（7 MB）在线下载 Docker Desktop 和镜像再安装，依赖 WSL + Docker Desktop 都正常，环节多。实机第一次就因 WSL 判断失败中断，用户反馈"应该是完整的封装"，于是改成现在的 WSL 离线安装包。Docker 方式保留给 Linux 服务器和已装 Docker Desktop 的用户。

## 九、实机踩坑记录 (2026-10-22, 开着本机代理的 Windows 11)

| 现象 | 原因 | 处理 |
|---|---|---|
| 安装报「读默认配置失败: invalid character 'w'」, 备份文件名里有 `wsllocalhost…NAT…` 乱码 | Windows 系统代理指向 127.0.0.1 时, `wsl.exe` 每条命令都打一行 `wsl: 检测到 localhost 代理配置…` 提示, 旧版安装器把它和命令输出混在一起读 | 安装器只读标准输出并过滤 `wsl:` 提示行; 检测到本机代理 (Windows ≥ 22621) 自动在 `.wslconfig` 加 `networkingMode=mirrored` (已有设置不动, 原文件备份为 `.wslconfig.litecode-bak`)。手动绕过: cmd 里 `echo [wsl2]> "%USERPROFILE%\.wslconfig"`、`echo networkingMode=mirrored>> "%USERPROFILE%\.wslconfig"`、`wsl --shutdown` 后重跑安装 |
| 安装卡在「接入本机操控」很久, 之后提示桌面代理没上线 | 这一步要联网下载 OpenSSH / Python / 依赖, 代理或网络慢时会卡 | 交互安装改成托盘启动后在后台接入, 开始和完成都弹通知; 失败从托盘菜单「接入本机操控」重试 (日志 `%LOCALAPPDATA%\LiteCode\local-control.log`) |
| 对话报 `Illegal header value b'Bearer '` | 安装时没填 API Key, 请求头成了 `Bearer ` | 空 Key 一律按 `EMPTY` 处理 (服务端加载配置和安装器写配置两处) |
| PowerShell 下载报「未能为 SSL/TLS 安全通道建立信任关系」, 浏览器却能打开 | 下载站用的 Let's Encrypt ECDSA 证书链到新根 (Root YE / ISRG Root X2), Windows 系统证书库可能没有 (要联网临时下载, 被代理挡住) | 下载站证书改用 RSA (链到 ISRG Root X1, 所有 Windows 内置); 自建下载站时同样用 RSA 证书 |
