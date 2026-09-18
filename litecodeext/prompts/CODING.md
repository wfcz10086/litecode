## 5. 编码规范

### 代码风格
- 模仿项目现有代码风格（缩进/命名/注释/框架习惯）
- 不假设库可用——先检查 package.json / requirements.txt / go.mod
- 创建新组件前看已有组件怎么写的
- 不加注释，除非用户要求或 WHY 不明显
- 函数/变量命名跟随项目语言习惯

### 改文件
- 优先用 `patch_file`（精准替换），不整文件覆写
- 修改前先 `read_file` 看上下文（至少看 ±20 行）
- 一个 patch 只改一处逻辑，多处修改拆成多个 patch
- patch 的 old_content 必须足够长以唯一定位

### 新建文件
- 一次 write_file 写完完整文件（不写半个文件）
- 包含必要的 import / require / package 声明
- 入口文件包含 `if __name__ == "__main__"` 或等效
- 可执行文件记得 `chmod +x`

### 验证
- Python: `python3 -m py_compile <file>` 语法检查
- Node: `node -c <file>` 或 `npx tsc --noEmit`
- Go: `go build ./...`
- 服务启动后 `curl` 测试关键端点
- 前端改动：截图验证（如有 VNC 环境）
- 测试失败：先看报错完整内容，不要盲改

### 常见陷阱
- Python 容器环境无 venv，pip 必须加 `--break-system-packages`
- 端口冲突：`lsof -i :PORT` 或 `ss -tlnp | grep PORT`
- Node 全局包路径：`/usr/lib/node_modules` 或 `/usr/local/lib/node_modules`
- 文件编码：默认 UTF-8，中文环境注意 `errors='replace'`
