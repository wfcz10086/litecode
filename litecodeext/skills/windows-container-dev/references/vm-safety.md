# Windows 虚拟机变更前检查

这份说明随 LiteCode 技能一起分发；仓库完整操作记录在 `docs/WINDOWS_VM_OPERATIONS.md`。控制端容器未挂载仓库上级目录时，仍必须执行以下规则。

## 保留系统盘

先核对宿主 SSH 别名、Compose 项目、容器、持久化目录和 Windows 主机名。只操作已授权的新测试机，不能重启、锁定或重建原有业务机。

dockurr/windows v6.06 的普通安装路径要求系统盘和 `windows.boot` 同时存在。盘存在但完成标记缺失时，容器重建可能触发“安装未完成”清理，删除已有系统盘。2026-10-19 的测试确实发生过此错误；不能直接 `up --force-recreate`、更换安装源或手造完成标记。

先让健康的 Windows 正常关机，保留配置原有的两分钟宽限，检查实际运行脚本、关机日志和运行程序自动生成的完成标记。确认 QEMU 不再持有系统盘后，制作包含系统盘、固件/NVRAM、状态及配置的独立离线快照，再运行：

```text
python3 assets/check_vm_recreate.py --storage <系统盘目录> --snapshot <离线快照的系统盘目录>
```

检查需要 Linux 宿主管理员权限。脚本只读，不关机、不生成标记、不重建容器；拒绝运行中的磁盘、不完整副本、软链接、硬链接和内容不一致的快照。保持暂停的 QEMU 仍占用系统盘，也不算离线。字节一致不保证 Windows 文件系统健康：卡死后停机的现场副本必须注明非正常关机，并另保留之前的健康恢复点。

## 嵌套虚拟化

在本轮 AMD / QEMU 11.1.1 测试中，Windows 普通冷启动正常，但启用 WSL/VMP 后卡在 Windows Boot Manager。单独追加 `-hv-avic` 未解决；默认 `hv_passthrough` 会覆盖其它 Hyper-V 选项，不能仅根据命令文本认定功能已关闭。

只对专用测试机，在有效离线快照保护下改为 `HV=N`，使用 host CPU 并显式开启 `svm`、`npt`、`hv-relaxed`、`hv-vapic`、`hv-vpindex`、`hv-time`、`hv-synic`、`hv-stimer`、`hv-stimer-direct`、`hv-runtime`、`hv-frequencies`，设置 `hv-spinlocks=0xfff`，关闭 `hv-avic` / `hv-apicv` / `hv-evmcs` 后，已实际恢复 Windows 启动、SSH 自动服务和节点桌面。这个 AMD 配置不能直接套用到 Intel；Linux 发行版、容器和 IDE 仍须分别验收。

依据：[QEMU Hyper-V 选项及依赖](https://www.qemu.org/docs/master/system/i386/hyperv.html)。不把候选配置或上游案例当成本机验收结果，不修改宿主全局 KVM 参数来代替来宾排查。

WSL 的“不支持嵌套虚拟化”提示也可能只表示不能在 WSL 内再次运行虚拟机；实际测试已出现 PowerShell 在 stderr 警告处中止，但 Ubuntu 事实上已经注册为 WSL 2。先通过 `Start-Process` 分流输出、读取真实退出码和发行版状态，再实际执行 Linux `uname`。不要把警告、PowerShell 包装错误和 Linux 启动错误混成一种结果。`.wslconfig` 的 `nestedVirtualization` 控制 WSL 内额外的虚拟机能力，普通 Docker 容器不要求它。

## 验收与凭据

- 系统时间可能在启动后校正，不能只凭 `LastBootUpTime` 字符串变化判断重启。结合宿主启动记录、系统运行时间、新心跳和新截图。
- 锁屏、断网、重启、WSL、容器、断点、HTTP、文件回读和 OCR 分开记录。有必需项未执行或失败，整体未通过。
- 不把密码、私钥、接入码写进聊天、脚本参数或报告。直接使用已有 SSH 别名；工具缺少安全的提权入口时报告缺口，不从配置里复制密码拼到命令中。
- Web 测试记录实际请求的会话 ID。新建会话使用创建请求返回的 ID，等待页面切换完成。监听超时时先核对是否已经提交，不直接重发任务。
