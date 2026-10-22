// LiteCode Windows 启动器: 一个 exe 同时负责首次安装和托盘。
//
// 首次运行 (没有安装标记): 准备 Docker (WSL → Docker Desktop 静默安装) → 加载 LiteCode 离线镜像 →
// 生成配置 (登录密码 / 模型) → 创建容器 → 桌面和开始菜单快捷方式 + 开机自启 → 进入托盘。
// 之后每次运行: 确保 Docker 和容器在跑, 托盘常驻, 自动打开 LiteCode 窗口。
//
// 下载源和版本信息来自 BaseURL/latest.json, 所有下载都校验 sha256。
package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/ncruces/zenity"
	"golang.org/x/sys/windows"
)

// 发布目录 (latest.json / 代码更新包 / 完整安装包); 构建时可用 -ldflags "-X main.BaseURL=..." 覆盖
var BaseURL = "https://your-domain.example/5090/litecode-win/"

var Version = "dev"

const (
	appName       = "LiteCode"
	containerName = "litecode"
	webURL        = "http://127.0.0.1:18790/"
	healthURL     = "http://127.0.0.1:18789/health"
)

func dataDir() string {
	d := os.Getenv("LOCALAPPDATA")
	if d == "" {
		d, _ = os.UserConfigDir()
	}
	return filepath.Join(d, appName)
}

func installedMarker() string { return filepath.Join(dataDir(), "installed.json") }

func main() {
	setup := flag.Bool("setup", false, "重新运行安装流程")
	flag.BoolVar(&opt.Unattended, "unattended", false, "无人值守: 不弹输入/确认框 (自动化测试、批量部署)")
	flag.StringVar(&opt.Password, "password", "", "登录密码 (无人值守时必填)")
	flag.StringVar(&opt.ModelURL, "model-url", "", "模型 API 地址 (OpenAI 兼容)")
	flag.StringVar(&opt.ModelName, "model-name", "", "模型名称")
	flag.StringVar(&opt.ModelKey, "model-key", "", "模型 API Key")
	flag.BoolVar(&opt.CheckOnly, "selftest", false, "只打印环境检查结果 (WSL / 子系统 / 版本 / 服务) 后退出")
	flag.BoolVar(&opt.NoTray, "no-tray", false, "装完不进托盘 (自动化测试)")
	flag.BoolVar(&opt.NoLocalControl, "no-local-control", false, "安装时不接入本机操控 (OpenSSH + 桌面代理)")
	removeLocal := flag.Bool("remove-local-control", false, "移除本机桌面代理 (卸载程序调用) 后退出")
	localOnly := flag.Bool("local-control", false, "只做本机操控接入 (同托盘「重新接入本机操控」, 批量部署/自动化用) 后退出")
	flag.Parse()
	// 计划任务/开机自启拉起时工作目录是 C:\Windows\System32; 任何相对路径 (含 wsl 里的 cwd) 都会落到系统目录
	// (2026-10-22 真机: 模型包解压到了 System32\.new)。先切到自己的数据目录。
	if err := os.MkdirAll(dataDir(), 0o755); err == nil {
		_ = os.Chdir(dataDir())
	}

	// 这两项可与运行中的托盘并存 (不抢启动器互斥锁)
	if *removeLocal || *localOnly {
		os.Setenv("WSL_UTF8", "1")
		os.MkdirAll(dataDir(), 0o755)
		logInit()
		if *removeLocal {
			removeLocalControl()
			return
		}
		res, err := setupLocalControl()
		logf("本机操控接入 (--local-control): %+v err=%v", res, err)
		if err != nil {
			os.Exit(1)
		}
		return
	}

	// 同一用户只跑一个启动器; 已有实例时只把窗口打开
	name, _ := windows.UTF16PtrFromString("Local\\LiteCodeLauncher")
	h, err := windows.CreateMutex(nil, false, name)
	if err == windows.ERROR_ALREADY_EXISTS {
		openWindow()
		return
	}
	defer windows.CloseHandle(h)

	os.Setenv("WSL_UTF8", "1") // wsl.exe 输出 UTF-8 (默认 UTF-16)
	if err := os.MkdirAll(dataDir(), 0o755); err != nil {
		fatal("无法创建数据目录", err)
	}
	logInit()
	logf("启动 LiteCode 启动器 %s", Version)

	if opt.CheckOnly {
		selftest()
		return
	}
	if *setup || !fileExists(installedMarker()) {
		if err := runSetup(); err != nil {
			logf("安装失败: %v", err)
			fatal("安装没有完成", err)
		}
	}
	if opt.NoTray {
		logf("安装完成, --no-tray 退出")
		return
	}
	runTray()
}

// 命令行选项 (无人值守安装)
var opt struct {
	Unattended, CheckOnly                   bool
	NoTray, NoLocalControl                  bool
	Password, ModelURL, ModelName, ModelKey string
}

func fatal(what string, err error) {
	if opt.Unattended {
		logf("FATAL %s: %v", what, err)
		os.Exit(2)
	}
	_ = zenity.Error(fmt.Sprintf("%s：\n%v\n\n日志：%s", what, err, logPath()), zenity.Title(appName))
	os.Exit(1)
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// selftest: 给自动化验收用, 结果写进 launcher.log 并以 selftest.txt 落盘
func selftest() {
	lines := []string{
		fmt.Sprintf("launcher=%s", Version),
		fmt.Sprintf("wsl_ready=%v", wslReady()),
		fmt.Sprintf("distro_exists=%v", distroExists()),
		fmt.Sprintf("code_version=%s", codeVersion()),
		fmt.Sprintf("rootfs_version=%s", rootfsVersion()),
		fmt.Sprintf("configured=%v", distroFile("/etc/litecode-configured") != ""),
		fmt.Sprintf("web_ready=%v", webReady()),
		fmt.Sprintf("server_healthy=%v", serverHealthy()),
		fmt.Sprintf("upgrade_pending=%v", fileExists(filepath.Join(dataDir(), "upgrade-pending.json"))),
	}
	out := strings.Join(lines, "\r\n")
	logf("selftest:\n%s", out)
	os.WriteFile(filepath.Join(dataDir(), "selftest.txt"), []byte(out), 0o644)
}
