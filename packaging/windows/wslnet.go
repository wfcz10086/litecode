package main

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

// 2026-10-22 用户机: Windows 开着本机代理 (Clash 等, 系统代理指向 127.0.0.1) 时, WSL 默认的 NAT 网络用不了
// 这个代理, 每条 wsl.exe 命令还会打一行 "wsl: 检测到 localhost 代理配置…" 警告。安装时检测到这种情况, 就在
// %USERPROFILE%\.wslconfig 的 [wsl2] 段加 networkingMode=mirrored (WSL 和 Windows 共用网络, 子系统里也能走
// 本机代理)。用户已经设过 networkingMode 的一律不动; 需要 Windows 11 22H2 (内部版本 22621) 及以上。

var reNetworkingMode = regexp.MustCompile(`(?im)^\s*networkingMode\s*=`)

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

// withMirrored: 在 .wslconfig 内容里加 networkingMode=mirrored; 已有 networkingMode 时原样返回 changed=false
func withMirrored(content string) (string, bool) {
	if reNetworkingMode.MatchString(content) {
		return content, false
	}
	content = strings.ReplaceAll(content, "\r\n", "\n")
	lines := strings.Split(content, "\n")
	for i, l := range lines {
		if strings.EqualFold(strings.TrimSpace(l), "[wsl2]") {
			out := append([]string{}, lines[:i+1]...)
			out = append(out, "networkingMode=mirrored")
			out = append(out, lines[i+1:]...)
			return strings.Join(out, "\r\n"), true
		}
	}
	if strings.TrimSpace(content) != "" && !strings.HasSuffix(content, "\n") {
		content += "\n"
	}
	return strings.ReplaceAll(content, "\n", "\r\n") + "[wsl2]\r\nnetworkingMode=mirrored\r\n", true
}

// ensureWSLNetworking: 检测到本机代理时把 WSL 切到镜像网络。失败只记日志, 不影响安装。
func ensureWSLNetworking(say func(int, string, ...any)) {
	proxy, ok := localhostProxy()
	if !ok {
		return
	}
	if b := windowsBuild(); b < 22621 {
		logf("检测到本机代理 %s, 但 Windows 内部版本 %d < 22621 不支持 WSL 镜像网络, 跳过", proxy, b)
		return
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return
	}
	path := filepath.Join(home, ".wslconfig")
	old, _ := os.ReadFile(path)
	content, changed := withMirrored(string(old))
	if !changed {
		logf("检测到本机代理 %s; .wslconfig 已设置 networkingMode, 不改", proxy)
		return
	}
	if len(old) > 0 {
		_ = os.WriteFile(path+".litecode-bak", old, 0o644)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		logf("写 .wslconfig 失败: %v", err)
		return
	}
	say(-1, "检测到本机代理，WSL 改用镜像网络（子系统里也能用这个代理）…")
	logf("检测到本机代理 %s: .wslconfig 加上 networkingMode=mirrored, 重启 WSL 生效", proxy)
	wsl(2*time.Minute, "--shutdown")
}
