package main

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

// 2026-10-22 用户机: Windows 开着本机代理 (Clash 等, 系统代理指向 127.0.0.1) 时, WSL 默认的 NAT 网络用不了
// 这个代理, 每条 wsl.exe 命令还会打一行 "wsl: 检测到 localhost 代理配置…" 警告。安装时检测到这种情况, 就在
// %USERPROFILE%\.wslconfig 的 [wsl2] 段加 networkingMode=mirrored (WSL 和 Windows 共用网络, 子系统里也能走
// 本机代理)。用户已经设过 networkingMode 的一律不动; 需要 Windows 11 22H2 (内部版本 22621) 及以上。

// localhostProxy: 当前用户的系统代理是否指向本机
func localhostProxy() (string, bool) {
	k, err := registry.OpenKey(registry.CURRENT_USER, `Software\Microsoft\Windows\CurrentVersion\Internet Settings`, registry.QUERY_VALUE)
	if err == nil {
		defer k.Close()
		if on, _, err := k.GetIntegerValue("ProxyEnable"); err == nil && on == 1 {
			if srv, _, err := k.GetStringValue("ProxyServer"); err == nil && isLocalAddr(srv) {
				return srv, true
			}
		}
	}
	for _, e := range []string{"HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"} {
		if v := os.Getenv(e); isLocalAddr(v) {
			return v, true
		}
	}
	return "", false
}

func isLocalAddr(s string) bool {
	s = strings.ToLower(s)
	return strings.Contains(s, "127.0.0.1") || strings.Contains(s, "localhost") || strings.Contains(s, "[::1]")
}

func windowsBuild() uint32 {
	return windows.RtlGetVersion().BuildNumber
}

// withWSL2Key: 在 .wslconfig 的 [wsl2] 段加 key=value; 已经有这个 key (用户自己设过) 时原样返回 changed=false
func withWSL2Key(content, key, value string) (string, bool) {
	if regexp.MustCompile(`(?im)^\s*` + regexp.QuoteMeta(key) + `\s*=`).MatchString(content) {
		return content, false
	}
	content = strings.ReplaceAll(content, "\r\n", "\n")
	lines := strings.Split(content, "\n")
	for i, l := range lines {
		if strings.EqualFold(strings.TrimSpace(l), "[wsl2]") {
			out := append([]string{}, lines[:i+1]...)
			out = append(out, key+"="+value)
			out = append(out, lines[i+1:]...)
			return strings.Join(out, "\r\n"), true
		}
	}
	if strings.TrimSpace(content) != "" && !strings.HasSuffix(content, "\n") {
		content += "\n"
	}
	return strings.ReplaceAll(content, "\n", "\r\n") + "[wsl2]\r\n" + key + "=" + value + "\r\n", true
}

func withMirrored(content string) (string, bool) {
	return withWSL2Key(content, "networkingMode", "mirrored")
}

// totalMemoryGB: 物理内存 (GB, 向下取整)
func totalMemoryGB() int {
	type memStatusEx struct {
		Length               uint32
		MemoryLoad           uint32
		TotalPhys            uint64
		AvailPhys            uint64
		TotalPageFile        uint64
		AvailPageFile        uint64
		TotalVirtual         uint64
		AvailVirtual         uint64
		AvailExtendedVirtual uint64
	}
	m := memStatusEx{}
	m.Length = uint32(unsafe.Sizeof(m))
	r, _, _ := windows.NewLazySystemDLL("kernel32.dll").NewProc("GlobalMemoryStatusEx").Call(uintptr(unsafe.Pointer(&m)))
	if r == 0 {
		return 0
	}
	return int(m.TotalPhys >> 30)
}

// wslResources: WSL2 默认 CPU 已是全部逻辑核, 但内存只给一半; 本地配音 / CPU 生图 / 浏览器都吃内存。
// 显式写满 CPU, 内存给物理内存的 75% (至少留 4GB 给 Windows)。用户自己设过的不动。
func wslResources() (processors, memoryGB int) {
	processors = runtime.NumCPU()
	if t := totalMemoryGB(); t > 0 {
		memoryGB = t * 3 / 4
		if t-memoryGB < 4 {
			memoryGB = t - 4
		}
		if memoryGB < t/2 {
			memoryGB = t / 2
		}
	}
	return
}

// ensureWSLConfig: 写 .wslconfig (CPU 全部核、内存 75%; 有本机代理时镜像网络), 有改动就重启 WSL 生效。失败只记日志, 不影响安装。
func ensureWSLConfig(say func(int, string, ...any)) {
	home, err := os.UserHomeDir()
	if err != nil {
		return
	}
	path := filepath.Join(home, ".wslconfig")
	old, _ := os.ReadFile(path)
	content, changed := string(old), false
	var notes []string
	if cpu, mem := wslResources(); cpu > 0 {
		var c bool
		if content, c = withWSL2Key(content, "processors", strconv.Itoa(cpu)); c {
			changed = true
			notes = append(notes, fmt.Sprintf("CPU %d 核", cpu))
		}
		if mem > 0 {
			if content, c = withWSL2Key(content, "memory", fmt.Sprintf("%dGB", mem)); c {
				changed = true
				notes = append(notes, fmt.Sprintf("内存 %dGB", mem))
			}
		}
	}
	if proxy, ok := localhostProxy(); ok {
		if b := windowsBuild(); b < 22621 {
			logf("检测到本机代理 %s, 但 Windows 内部版本 %d < 22621 不支持 WSL 镜像网络, 跳过", proxy, b)
		} else {
			var c bool
			if content, c = withMirrored(content); c {
				changed = true
				notes = append(notes, "镜像网络 (子系统里也能用本机代理 "+proxy+")")
			}
		}
	}
	if !changed {
		logf(".wslconfig 已有 CPU/内存/网络设置, 不改")
		return
	}
	if len(old) > 0 {
		_ = os.WriteFile(path+".litecode-bak", old, 0o644)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		logf("写 .wslconfig 失败: %v", err)
		return
	}
	msg := "WSL 设置: " + strings.Join(notes, "、")
	say(-1, msg+"…")
	logf("%s; 重启 WSL 生效", msg)
	wsl(2*time.Minute, "--shutdown")
}
