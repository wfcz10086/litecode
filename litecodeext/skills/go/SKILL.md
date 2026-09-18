---
name: go
description: Go 语言开发：环境探测、模块管理、构建/测试/格式化，以及写完必须运行验证的完整循环。触发词：go、golang、.go、go mod、goroutine、gin、gRPC、go build、go test
keywords: go golang .go goroutine gin grpc fiber echo cobra viper gomod gosum
---

# Go 开发技能

## 第一步：环境探测（每次 Go 任务必须先做）

```bash
# 1. 确认 Go 安装
go version

# 2. 确认模块状态
ls go.mod 2>/dev/null && cat go.mod || echo "no go.mod"

# 3. 确认工作目录
pwd && ls -la
```

如果没有 go.mod，先初始化：
```bash
go mod init <module_name>
# module_name 格式：github.com/user/repo 或 项目名
```

---

## 构建与验证循环（写完代码必须执行）

```
写代码 → go build → go vet → go test → 修复 → 循环直到全部通过
```

```bash
# 格式化（提交前必做）
gofmt -w .

# 构建（检查语法和类型错误）
go build ./...

# 静态检查（比编译器更严格）
go vet ./...

# 运行测试
go test ./... -v

# 竞态检测（并发代码必加）
go test -race ./...

# 覆盖率
go test ./... -coverprofile=coverage.out && go tool cover -html=coverage.out
```

---

## 依赖管理

```bash
# 添加依赖
go get github.com/gin-gonic/gin@latest

# 整理依赖（删除无用、补全缺失）
go mod tidy

# 查看依赖树
go mod graph

# 离线构建（确保 vendor 完整）
go mod vendor
go build -mod=vendor ./...
```

---

## 常用项目结构

```
project/
├── cmd/
│   └── main/
│       └── main.go        # 入口
├── internal/              # 私有包（不可被外部引用）
│   ├── handler/
│   ├── service/
│   └── repository/
├── pkg/                   # 公开包
├── api/                   # protobuf / openapi 定义
├── config/
├── go.mod
├── go.sum
└── Makefile
```

---

## HTTP 服务启动模式

用 `background=true` + `health_url` 启动，启动前先检查端口：

```bash
# 检查端口占用
ss -tlnp | grep :8080 || echo "port free"

# 如果被占用：
kill $(lsof -t -i:8080) 2>/dev/null || true

# 构建后启动
go build -o ./bin/server ./cmd/main && ./bin/server
```

---

## 错误处理规范

```go
// 标准错误包装（Go 1.13+）
if err != nil {
    return fmt.Errorf("operation failed: %w", err)
}

// 错误判断
if errors.Is(err, os.ErrNotExist) { ... }
var myErr *MyError
if errors.As(err, &myErr) { ... }
```

---

## 测试写法

```go
func TestXxx(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    string
        wantErr bool
    }{
        {"normal case", "foo", "bar", false},
        {"error case", "",    "",    true},
    }
    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := Xxx(tt.input)
            if (err != nil) != tt.wantErr {
                t.Errorf("error = %v, wantErr %v", err, tt.wantErr)
            }
            if got != tt.want {
                t.Errorf("got = %v, want %v", got, tt.want)
            }
        })
    }
}
```

---

## 常见问题速查

| 症状 | 原因 | 修复 |
|------|------|------|
| `cannot find module` | go.mod 缺失或路径错 | `go mod init` / `go mod tidy` |
| `undefined: Xxx` | 包未导入 / 首字母小写 | 检查 import / 改为大写 |
| `declared but not used` | 变量未使用 | 用 `_` 或删除变量 |
| `imported and not used` | 导入未使用 | 删除 import |
| `go: updates to go.sum needed` | go.sum 过期 | `go mod tidy` |
| goroutine leak | 没有等待退出 | 用 `sync.WaitGroup` 或 `context.WithCancel` |

---

## 并发模式

```go
// Worker Pool
func workerPool(jobs <-chan Job, results chan<- Result, n int) {
    var wg sync.WaitGroup
    for i := 0; i < n; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            for job := range jobs {
                results <- process(job)
            }
        }()
    }
    wg.Wait()
    close(results)
}

// Context 超时
ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
defer cancel()
```
