package main

import (
	_ "embed"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"fyne.io/systray"
	"github.com/ncruces/zenity"

	"litecode-launcher/upgrade"
)

//go:embed assets/litecode.ico
var iconICO []byte

func runTray() {
	systray.Run(onTrayReady, func() {})
}

func setTip(s string) { systray.SetTooltip(s) }

func onTrayReady() {
	systray.SetIcon(iconICO)
	systray.SetTitle(appName)
	setTip("LiteCode · 启动中…")

	mOpen := systray.AddMenuItem("打开 LiteCode", "")
	systray.AddSeparator()
	mStart := systray.AddMenuItem("启动服务", "")
	mStop := systray.AddMenuItem("停止服务", "")
	mUpdate := systray.AddMenuItem("检查更新", "")
	mLogs := systray.AddMenuItem("查看日志", "")
	mFolder := systray.AddMenuItem("打开数据文件夹", "")
	mGuide := systray.AddMenuItem("使用说明", "")
	addLocalControlMenu()
	systray.AddSeparator()
	mQuit := systray.AddMenuItem("退出（服务保持运行）", "")

	go raiseOwnDialogs() // 托盘弹窗 (更新提示等) 置顶, 否则被别的窗口挡住看不到
	go watchService()    // 服务不论谁拉起的, 掉了就拉回来
	go bringUpAtLaunch()
	go autoCheckUpdate()

	go func() {
		for {
			select {
			case <-mOpen.ClickedCh:
				if webReady() {
					openWindow()
				} else {
					go bringUp(true)
				}
			case <-mStart.ClickedCh:
				userStopped.Store(false)
				go bringUp(true)
			case <-mStop.ClickedCh:
				userStopped.Store(true)
				stopService()
				setTip("LiteCode · 已停止")
			case <-mUpdate.ClickedCh:
				go checkUpdate(true)
			case <-mLogs.ClickedCh:
				showLogs()
			case <-mFolder.ClickedCh:
				exec.Command("explorer", dataDir()).Start()
			case <-mGuide.ClickedCh:
				exec.Command("rundll32", "url.dll,FileProtocolHandler", filepath.Join(dataDir(), "guide.html")).Start()
			case <-mQuit.ClickedCh:
				systray.Quit()
				return
			}
		}
	}()
}

func bringUp(open bool) bool {
	setTip("LiteCode · 正在启动…")
	if err := startService(); err != nil {
		setTip("LiteCode · 启动失败")
		zenity.Error(err.Error(), zenity.Title(appName))
		return false
	}
	if !waitHealthy(5 * time.Minute) {
		setTip("LiteCode · 启动超时，请查看日志")
		return false
	}
	setTip(fmt.Sprintf("LiteCode %s · 运行中", codeVersion()))
	if open {
		openWindow()
	}
	return true
}

// bringUpAtLaunch: 托盘刚启动 (多半是开机自启) 时拉起服务并开窗口。
// 开机时 Windows「恢复上次的应用」/ Edge 会抢在服务起来前把上次的 LiteCode 窗口恢复出来, 那时页面是
// "127.0.0.1 无法访问"; 之后 openWindow 只会把它切到前台, 用户看到的还是空白。所以服务原本没起来的话,
// 就绪后先关掉这个旧窗口再开新的。
func bringUpAtLaunch() {
	wasReady := webReady()
	if !bringUp(false) {
		return
	}
	if !wasReady {
		if h := existingAppWindow(); h != 0 {
			logf("启动前已有 LiteCode 窗口 (多半是开机时浏览器恢复的空白页), 关掉重开")
			closeWindow(h)
			for i := 0; i < 20 && existingAppWindow() != 0; i++ {
				time.Sleep(250 * time.Millisecond)
			}
		}
	}
	openWindow()
	go ensureModels() // 模型包不进安装包, 服务起来后后台补齐
}

func showLogs() {
	tail := func(p string, n int) string {
		b, _ := os.ReadFile(p)
		if len(b) > n {
			b = b[len(b)-n:]
		}
		return string(b)
	}
	f := filepath.Join(os.TempDir(), "litecode-logs.txt")
	os.WriteFile(f, []byte("==== LiteCode 服务日志 ====\r\n"+tail(serviceLog(), 60000)+
		"\r\n\r\n==== 启动器日志 ====\r\n"+tail(logPath(), 20000)), 0o644)
	exec.Command("notepad", f).Start()
}

// newer: 点分数字版本比较 (1.10.16.92 > 1.10.16.91)
func newer(a, b string) bool {
	pa, pb := strings.Split(a, "."), strings.Split(b, ".")
	for i := 0; i < len(pa) || i < len(pb); i++ {
		var x, y int
		if i < len(pa) {
			x, _ = strconv.Atoi(pa[i])
		}
		if i < len(pb) {
			y, _ = strconv.Atoi(pb[i])
		}
		if x != y {
			return x > y
		}
	}
	return false
}

// autoCheckUpdate: 启动 2 分钟后查一次, 之后每天一次; 有新版只提示, 用户确认才更新
func autoCheckUpdate() {
	time.Sleep(2 * time.Minute)
	for {
		checkUpdate(false)
		time.Sleep(24 * time.Hour)
	}
}

// checkUpdate 两级更新:
//   - 代码更新 (常见, ~10MB): 下载代码包 → 停服务 → 子系统内原地替换 (保留配置和数据) → 启动; 起不来自动回滚
//   - 系统包更新 (底层依赖变了, 少见): 下载新的完整安装包并运行, 安装包会先备份数据再换新
func checkUpdate(manual bool) {
	rel, err := fetchRelease()
	if err != nil {
		if manual {
			zenity.Error("检查更新失败: "+err.Error(), zenity.Title(appName))
		}
		return
	}
	curCode, curRootfs := codeVersion(), rootfsVersion()
	switch {
	case curRootfs != "" && newer(rel.RootfsVersion, curRootfs):
		if ok, _ := askYes(fmt.Sprintf("LiteCode %s 需要完整更新（底层组件有变化），下载约 %s。\n对话和设置会保留，现在更新吗？",
			rel.Version, mb(rel.InstallerSize))); ok {
			fullUpdate(rel)
		}
	case newer(rel.Version, curCode):
		if ok, _ := askYes(fmt.Sprintf("LiteCode 有新版本 %s（当前 %s），下载约 %s，更新约 1 分钟。\n对话和设置会保留，现在更新吗？",
			rel.Version, curCode, mb(rel.CodeSize))); ok {
			codeUpdate(rel)
		}
	default:
		if manual {
			zenity.Info("已经是最新版本 "+curCode, zenity.Title(appName))
		}
	}
}

func codeUpdate(rel *Release) {
	dlg, _ := zenity.Progress(zenity.Title("更新 LiteCode "+rel.Version), zenity.MaxValue(100))
	defer func() {
		if dlg != nil {
			dlg.Close()
		}
	}()
	f := filepath.Join(dataDir(), "downloads", rel.CodeFile)
	os.MkdirAll(filepath.Dir(f), 0o755)
	if err := download(BaseURL+rel.CodeFile, f, rel.CodeSHA256, func(d, t int64) {
		dlg.Text(fmt.Sprintf("下载… %s / %s", mb(d), mb(t)))
		dlg.Value(int(50 * d / max64(t, 1)))
	}); err != nil {
		zenity.Error("下载失败: "+err.Error(), zenity.Title(appName))
		return
	}
	dlg.Text("安装新版本…")
	dlg.Value(60)
	wslPkg := winToWSL(f)
	started := 0
	res := upgrade.RunCodeUpdate(upgrade.CodeOps{
		Log:  logf,
		Stop: stopService,
		Apply: func() (string, int, error) {
			defer os.Remove(f)
			return inDistroCode(5*time.Minute, "/usr/local/bin/litecode-apply-update", wslPkg, rel.Version)
		},
		Rollback: func() (string, int, error) {
			dlg.Text("新版本启动失败，正在回滚…")
			return inDistroCode(5*time.Minute, "/usr/local/bin/litecode-rollback")
		},
		Start: func() bool {
			started++
			if started == 1 {
				dlg.Text("启动并检查服务…")
				dlg.Value(80)
			} else {
				dlg.Text("重新启动并检查服务…")
			}
			setTip("LiteCode · 正在启动…")
			if err := startService(); err != nil {
				logf("启动失败: %v", err)
				setTip("LiteCode · 启动失败")
				return false
			}
			ok := waitHealthy(5 * time.Minute)
			if ok {
				setTip(fmt.Sprintf("LiteCode %s · 运行中", codeVersion()))
			} else {
				setTip("LiteCode · 启动失败，请查看日志")
			}
			return ok
		},
		Version: codeVersion,
		LogHint: "日志：托盘 → 查看日志（" + serviceLog() + "）",
	}, rel.Version)
	logf("代码更新结果: %+v", res)
	dlg.Close()
	dlg = nil
	switch res.Level {
	case upgrade.Info:
		zenity.Info(res.Message, zenity.Title(appName))
	case upgrade.Warn:
		zenity.Warning(res.Message, zenity.Title(appName))
	default:
		zenity.Error(res.Message, zenity.Title(appName))
	}
	if res.Running {
		openWindow()
	}
}

func fullUpdate(rel *Release) {
	dlg, _ := zenity.Progress(zenity.Title("下载 LiteCode "+rel.Version), zenity.MaxValue(100))
	f := filepath.Join(dataDir(), "downloads", rel.InstallerFile)
	os.MkdirAll(filepath.Dir(f), 0o755)
	err := download(BaseURL+rel.InstallerFile, f, rel.InstallerSHA256, func(d, t int64) {
		dlg.Text(fmt.Sprintf("下载完整安装包… %s / %s", mb(d), mb(t)))
		dlg.Value(int(100 * d / max64(t, 1)))
	})
	dlg.Close()
	if err != nil {
		zenity.Error("下载失败: "+err.Error(), zenity.Title(appName))
		return
	}
	// 安装包会结束本托盘、备份数据、换新系统包、恢复数据
	exec.Command(f).Start()
	systray.Quit()
}

// userStopped: 用户在托盘点了「停止服务」—— 巡检不去拉起。
var userStopped atomic.Bool

// watchService 每 30 秒看一次服务: 不在、用户没点停止、子系统还在 → 拉起。
// [2026-10-21 真机演练] 托盘启动时若服务已由安装程序拉起, 它只记"不重复启动"却不监视;
// 之后 wsl --shutdown / 服务崩溃, 服务就一直停着, 要用户手动点启动。
func watchService() {
	time.Sleep(90 * time.Second) // 让开机/安装时的首次启动先走完
	for {
		time.Sleep(30 * time.Second)
		if userStopped.Load() || webReady() {
			continue
		}
		svc.Lock()
		held := svc.cmd != nil
		svc.Unlock()
		if held || !distroExists() {
			continue // 自己持有的进程由退出回调处理; 子系统不在时由 startService 提示, 这里不刷屏
		}
		logf("巡检: 服务不在 (非用户停止), 重新拉起")
		bringUp(false)
	}
}
