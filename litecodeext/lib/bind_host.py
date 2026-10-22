"""服务监听地址: 环境变量 LITECODE_BIND_HOST 优先, 否则用调用方给的默认值 (配置或 0.0.0.0)。

Windows 桌面版启动脚本 (packaging/windows/wsl/litecode-wsl-start) 与托盘程序设为 127.0.0.1,
避免用户开 WSL 镜像网络时局域网直连; 服务器版 docker 部署不设该变量, 行为不变。
"""
import os


def bind_host(default: str = "0.0.0.0") -> str:
    return os.environ.get("LITECODE_BIND_HOST", "").strip() or default or "0.0.0.0"
