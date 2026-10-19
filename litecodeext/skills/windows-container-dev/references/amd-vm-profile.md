# AMD 测试机的嵌套 WSL 配置记录

适用于 Linux KVM 宿主上的专用 Windows 测试虚拟机。先读 [虚拟机变更检查](vm-safety.md)；已有机器必须先完成停机、快照和检查脚本，不能直接复制配置重建。不要给 Intel 机器套用 `svm` / `npt`。

本轮实测环境是 Ryzen AI MAX+ 395、宿主 Linux 7.0.0-34、运行镜像 v6.06 / QEMU 11.1.1、Windows 11 Pro 26200.8037、WSL 3.0.3。缓存镜像引用为：

```text
dockerproxy.net/dockurr/windows@sha256:16fd7c39a3e48bb946d29c59dc6015942e5b900a1fa5d7e5552053bfc48ee2ea
```

实测使用 4 vCPU、8GB 内存、独立 96GB 系统盘。相关 Compose 片段如下，不能替代包含独立存储、凭据、端口及设备挂载的完整配置：

```yaml
environment:
  CPU_MODEL: host
  CPU_CORES: '4'
  RAM_SIZE: 8G
  DISK_SIZE: 96G
  VMX: 'Y'
  HV: 'N'
  CPU_FLAGS: svm=on,npt=on,hv-relaxed=on,hv-vapic=on,hv-vpindex=on,hv-time=on,hv-synic=on,hv-stimer=on,hv-stimer-direct=on,hv-runtime=on,hv-frequencies=on,hv-spinlocks=0xfff,-hv-avic,-hv-apicv,-hv-evmcs
stop_grace_period: 2m
restart: unless-stopped
```

实际 QEMU 命令已核对不含 `hv_passthrough`。完成有保护的变更后，Windows 在 VMP/WSL 功能 Enabled 的情况下进入桌面，SSH Running / Automatic、节点心跳和新截图均通过；随后实际在 Windows SSH 会话内启动 Ubuntu 24.04.5，`uname` 返回 `6.18.54.1-microsoft-standard-WSL2`、退出码 0。

这些结果证明本轮 Windows 和 WSL Linux 启动；Docker、IDE 断点、文件及 OCR 另行验收。其它 CPU、运行镜像或 Windows 更新版本需要重新验证，不保证配置普遍有效。不要改宿主全局 `ignore_msrs` 或其它业务虚拟机来套用本例。

共享 ISO 与各机系统盘分开管理，复用已校验的完整 ISO；只用当前机器自己的系统盘和 NVRAM。SSH/RDP/noVNC 管理端口沿本轮受限方案只绑定宿主回环地址，异地管理使用已验证的可信隧道；节点本身用出站 HTTPS 回连。密码、接入码、私钥不放入此模板。
