# 自己编译与发布

[返回 README](../README.md)

普通使用不需要看这页：Windows 下载安装包，Linux 用 `./start.sh`。这里写给要改代码、自己打包的人。

## 1. 服务器镜像（Linux Docker 版）

```bash
./start.sh --build        # 用仓库里的 Dockerfile 构建 litecode:latest 并启动
```

- `Dockerfile` 装好系统库、Python 依赖、Playwright 浏览器、noVNC、sherpa-onnx，默认用国内源（华为云基础镜像、阿里云 apt / pip、npmmirror）。
- 代码目录在 `docker-compose.yml` 里按目录挂载进容器：**改代码不用重建镜像**，`./start.sh --restart` 就生效。只有改了 `Dockerfile` 或依赖才需要 `--build`。

## 2. Windows 安装包

在一台 Linux 构建机上打包（不需要 Windows）。

**准备**

- Docker（并已用上一步构建出基础镜像 `litecode:latest`）
- Go 1.26 以上、`makensis`（Ubuntu：`sudo apt install nsis`）
- 磁盘留 8 GB

**打包**

```bash
LITECODE_DIST_DIR=$PWD/dist/win \
LITECODE_UPDATE_URL=https://你的下载目录/ \
LITECODE_NO_PUSH=1 \
bash packaging/release.sh 2.0.12 --full
```

| 参数 | 作用 |
|---|---|
| 第一个参数 `2.0.12` | 版本号，必须和 `litecodeext/lib/version.py` 里的一致，否则直接报错 |
| `--full` | 同时出完整离线安装包；不带时只出约 9 MB 的代码更新包（1~2 分钟） |
| `--skip-tests` | 跳过发布前测试（不推荐） |
| `LITECODE_DIST_DIR` | 产物输出目录 |
| `LITECODE_UPDATE_URL` | 托盘检查更新、下载模型包的地址，把产物放到这个目录即可（任何静态文件服务器都行） |
| `LITECODE_NO_PUSH=1` | 只在本地打标签，不推送 |

**产物**

| 文件 | 说明 |
|---|---|
| `LiteCode-Setup-<版本>.exe` | 完整离线安装包（WSL 系统包 + 托盘 + 自带 OpenSSH / Python 依赖） |
| `litecode-code-<版本>.tar.gz` | 代码更新包，托盘「检查更新」用 |
| `latest.json` | 版本信息：代码包、安装包、模型包的文件名、大小和 sha256 |
| `litecode-models-*.tar` | 本地配音和知识库向量模型（有模型源时才出），托盘装好后在后台下载 |

**流程**：预检 → 跑全部测试（`scripts/run_release_tests.sh`，任何失败都停）→ 导出公开版代码 + 隐私自检 → 拼 WSL 根文件系统 → 编译 Go 托盘 → NSIS 打包 → 写 `latest.json` → 打标签。

**更新源**：托盘先读 `main.GitHubLatest`（默认是本仓库的 `latest.json`），代码包从 GitHub 标签源码包下载；GitHub 连不上再用 `LITECODE_UPDATE_URL`。自己 fork 的话编译时改掉：

```bash
go build -ldflags "-X main.GitHubLatest=https://raw.githubusercontent.com/<你>/<仓库>/main/latest.json" ...
```

原理、子系统结构和排障见 [DESKTOP_WINDOWS.md](DESKTOP_WINDOWS.md)。

## 3. 测试

```bash
cd litecodeext && python3 -m pytest -q -n auto tests/   # 单元与集成测试 (需要 pytest-xdist)
bash smoke.sh                                           # 冒烟测试
cd packaging/windows && go test ./... && GOOS=windows go vet .
```
