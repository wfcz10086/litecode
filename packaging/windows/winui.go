package main

import (
	"os"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

// 窗口相关的小工具 (2026-10-21 真机演练发现):
//   - 托盘弹出的"有新版本, 现在更新吗?"被 LiteCode 窗口挡在后面, 只在任务栏闪 —— Windows 不让后台进程抢前台,
//     用户基本看不到。这里把本进程的对话框 (#32770) 置顶并闪烁。
//   - 托盘每启动一次都新开一个 LiteCode 应用窗口, 测试机上堆了 4 个。已有窗口就切到前台, 不再新开。

var (
	user32                       = windows.NewLazySystemDLL("user32.dll")
	procFindWindowExW            = user32.NewProc("FindWindowExW")
	procGetWindowThreadProcessId = user32.NewProc("GetWindowThreadProcessId")
	procIsWindowVisible          = user32.NewProc("IsWindowVisible")
	procGetWindowTextW           = user32.NewProc("GetWindowTextW")
	procGetClassNameW            = user32.NewProc("GetClassNameW")
	procSetWindowPos             = user32.NewProc("SetWindowPos")
	procSetForegroundWindow      = user32.NewProc("SetForegroundWindow")
	procShowWindow               = user32.NewProc("ShowWindow")
	procIsIconic                 = user32.NewProc("IsIconic")
	procFlashWindow              = user32.NewProc("FlashWindow")
	procGetWindowLongW           = user32.NewProc("GetWindowLongW")
	procPostMessageW             = user32.NewProc("PostMessageW")
)

const (
	swRestore      = 9
	swpNoMove      = 0x0002
	swpNoSize      = 0x0001
	swpShowWindow  = 0x0040
	gwlExStyle     = ^uintptr(19) // -20
	wsExTopmost    = 0x00000008
	hwndTopmostPtr = ^uintptr(0) // (HWND)-1
	wmClose        = 0x0010
)

func winText(h uintptr) string {
	buf := make([]uint16, 256)
	n, _, _ := procGetWindowTextW.Call(h, uintptr(unsafe.Pointer(&buf[0])), 256)
	return syscall.UTF16ToString(buf[:n])
}

func winClass(h uintptr) string {
	buf := make([]uint16, 128)
	n, _, _ := procGetClassNameW.Call(h, uintptr(unsafe.Pointer(&buf[0])), 128)
	return syscall.UTF16ToString(buf[:n])
}

func winPID(h uintptr) uint32 {
	var pid uint32
	procGetWindowThreadProcessId.Call(h, uintptr(unsafe.Pointer(&pid)))
	return pid
}

// topWindows 枚举可见的顶层窗口 (FindWindowEx 逐个往下找, 不用回调)。
func topWindows() []uintptr {
	var out []uintptr
	var h uintptr
	for i := 0; i < 4096; i++ {
		h, _, _ = procFindWindowExW.Call(0, h, 0, 0)
		if h == 0 {
			break
		}
		if v, _, _ := procIsWindowVisible.Call(h); v != 0 {
			out = append(out, h)
		}
	}
	return out
}

func bringToFront(h uintptr) {
	if ic, _, _ := procIsIconic.Call(h); ic != 0 {
		procShowWindow.Call(h, swRestore)
	}
	procSetForegroundWindow.Call(h)
}

// closeWindow 请窗口自己关闭 (WM_CLOSE, 等同点右上角 ×), 不强杀浏览器进程。
func closeWindow(h uintptr) {
	procPostMessageW.Call(h, wmClose, 0, 0)
}

// raiseOwnDialogs 后台循环: 本进程弹出的对话框一出现就置顶 + 闪烁任务栏 (只处理一次)。
func raiseOwnDialogs() {
	me := uint32(os.Getpid())
	done := map[uintptr]bool{}
	for {
		time.Sleep(700 * time.Millisecond)
		for _, h := range topWindows() {
			if done[h] || winPID(h) != me || winClass(h) != "#32770" {
				continue
			}
			ex, _, _ := procGetWindowLongW.Call(h, gwlExStyle)
			if ex&wsExTopmost == 0 {
				procSetWindowPos.Call(h, hwndTopmostPtr, 0, 0, 0, 0, swpNoMove|swpNoSize|swpShowWindow)
			}
			procSetForegroundWindow.Call(h)
			procFlashWindow.Call(h, 1)
			done[h] = true
		}
		for h := range done { // 关掉的对话框句柄可能被复用, 清掉
			if v, _, _ := procIsWindowVisible.Call(h); v == 0 {
				delete(done, h)
			}
		}
	}
}

// existingAppWindow 找已打开的 LiteCode 应用窗口 (Edge/Chrome --app, 类名 Chrome_WidgetWin_1, 标题以 LiteCode 开头)。
var debugWindows = os.Getenv("LITECODE_DEBUG_WINDOWS") != "" // 排查窗口问题时设此环境变量

func existingAppWindow() uintptr {
	me := uint32(os.Getpid())
	all := topWindows()
	defer func() {
		if debugWindows {
			var b strings.Builder
			for _, h := range all {
				b.WriteString(winClass(h) + "|" + winText(h) + "; ")
			}
			logf("窗口枚举 %d 个: %s", len(all), b.String())
		}
	}()
	for _, h := range all {
		if winPID(h) == me || winClass(h) != "Chrome_WidgetWin_1" {
			continue
		}
		if strings.HasPrefix(strings.TrimSpace(winText(h)), appName) {
			return h
		}
	}
	return 0
}
