package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/ncruces/zenity"
	"golang.org/x/sys/windows/registry"
)

// LiteCode 跑在独立的 WSL 子系统 "LiteCode" 里 (根文件系统 = 发行镜像导出), 不依赖 Docker。
const distro = "LiteCode"

func distroDir() string { return filepath.Join(dataDir(), "distro") }

// wsl 执行 wsl.exe; WSL_UTF8=1 让 -l 等输出 UTF-8 (默认 UTF-16)
func wsl(timeout time.Duration, args ...string) (string, error) {
	return run(timeout, "wsl.exe", args...)
}

// inDistro 以 root 在子系统里执行命令
func inDistro(timeout time.Duration, args ...string) (string, error) {
	return wsl(timeout, append([]string{"-d", distro, "-u", "root", "--"}, args...)...)
}

// inDistroCode 同 inDistro, 另返回退出码 (超时等拿不到退出码时为 -1), 供更新/回滚按退出码判定结果
func inDistroCode(timeout time.Duration, args ...string) (string, int, error) {
	out, err := inDistro(timeout, args...)
	if err == nil {
		return out, 0, nil
	}
	if ee, ok := err.(*exec.ExitError); ok {
		return out, ee.ExitCode(), err
	}
	return out, -1, err
}

// inDistroIO 带标准输入/输出流 (写配置、备份/恢复数据)
func inDistroIO(timeout time.Duration, stdin io.Reader, stdout io.Writer, args ...string) error {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "wsl.exe", append([]string{"-d", distro, "-u", "root", "--"}, args...)...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	var errb bytes.Buffer
	cmd.Stdin, cmd.Stdout, cmd.Stderr = stdin, stdout, &errb
	err := cmd.Run()
	logf("$ wsl(io) %s -> err=%v stderr=%s", strings.Join(args, " "), err, trunc(errb.String(), 300))
	if err != nil {
		return fmt.Errorf("%v %s", err, trunc(errb.String(), 300))
	}
	return nil
}

// wslReady: 实测有的机器 WSL 已装好、发行版也能跑, `wsl --status` 却返回非 0 (误判成没装 → 重装又返回 1 → 安装失败)。
// 依次看 `wsl --version`、`wsl --status`、商店版 WSL 程序文件, 任一成立就算已装。
func wslReady() bool {
	if _, err := wsl(60*time.Second, "--version"); err == nil {
		return true
	}
	if _, err := wsl(60*time.Second, "--status"); err == nil {
		return true
	}
	return fileExists(filepath.Join(os.Getenv("ProgramFiles"), "WSL", "wsl.exe"))
}

func distroExists() bool {
	out, err := wsl(60*time.Second, "-l", "-q")
	if err != nil {
		return false
	}
	for _, l := range strings.Split(strings.ReplaceAll(out, "\x00", ""), "\n") {
		if strings.EqualFold(strings.TrimSpace(l), distro) {
			return true
		}
	}
	return false
}

func importDistro(rootfs string) error {
	os.MkdirAll(distroDir(), 0o755)
	out, err := wsl(60*time.Minute, "--import", distro, distroDir(), rootfs, "--version", "2")
	if err != nil && importStuck(out) {
		// [2026-10-21 真机演练] 导入被强杀后, WSL 服务里会留一个状态为 Installing 的半成品,
		// 再导入报"此分发的安装、卸载或转换正在进行"。关掉 WSL 让它消失, 清掉残留磁盘文件再导一次。
		logf("导入被上次中断的半成品挡住, wsl --shutdown 后清理重试")
		wsl(2*time.Minute, "--shutdown")
		wsl(2*time.Minute, "--unregister", distro)
		os.Remove(filepath.Join(distroDir(), "ext4.vhdx"))
		out, err = wsl(60*time.Minute, "--import", distro, distroDir(), rootfs, "--version", "2")
	}
	if err != nil {
		return fmt.Errorf("导入 LiteCode 子系统失败: %v %s", err, trunc(out, 300))
	}
	return nil
}

func importStuck(out string) bool {
	for _, k := range []string{"正在进行", "in progress", "WSL_E_DISTRO_INSTALLING", "ERROR_ALREADY_EXISTS", "已存在"} {
		if strings.Contains(out, k) {
			return true
		}
	}
	return false
}

func distroFile(path string) string {
	out, err := inDistro(30*time.Second, "cat", path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(out)
}

func codeVersion() string   { return distroFile("/etc/litecode-version") }
func rootfsVersion() string { return distroFile("/etc/litecode-rootfs-version") }

// winToWSL: Windows 路径 → 子系统内路径 (/mnt/c/...)
func winToWSL(p string) string {
	out, err := inDistro(30*time.Second, "wslpath", "-a", p)
	if err == nil && strings.HasPrefix(out, "/") {
		return strings.TrimSpace(out)
	}
	p = strings.ReplaceAll(p, `\`, "/")
	return "/mnt/" + strings.ToLower(p[:1]) + p[2:]
}

// ── 服务: 托盘持有一个前台 wsl.exe 进程跑 litecode-wsl-start, 它在子系统就在, 它退出服务就停 ──

var svc struct {
	sync.Mutex
	cmd      *exec.Cmd
	done     chan struct{} // cmd 退出时关闭
	stopping bool
	crashes  []time.Time
}

func serviceLog() string { return filepath.Join(dataDir(), "service.log") }

// serviceStatusScript: 子系统里是否已有 LiteCode 服务 (litecode-wsl-start 写的 pid 文件; 旧系统包没有 pid 文件, 再按进程名找)。
// 经标准输入交给 bash, 不经 wsl.exe 命令行转义。[e] 写法避免 pgrep 匹配到自己所在的 shell。
const serviceStatusScript = `p=$(cat /run/litecode-service.pid 2>/dev/null)
if [ -n "$p" ] && grep -qaE 'entrypoint.sh|litecode-wsl-start' /proc/$p/cmdline 2>/dev/null; then echo RUNNING; exit 0; fi
if pgrep -f '[e]ntrypoint.sh all' >/dev/null 2>&1; then echo RUNNING; exit 0; fi
echo STOPPED
`

// serviceRunning: 服务已经在跑 (Web 健康检查通过, 或子系统里服务进程还在、正在启动)。
// [2026-10-21 实机] 安装 (--setup) 已拉起服务, 托盘再启动时 Web 还没就绪又拉一次 → 两个 wsl.exe、两套服务抢端口。
func serviceRunning() bool {
	if webReady() {
		return true
	}
	var out strings.Builder
	if err := inDistroIO(30*time.Second, strings.NewReader(serviceStatusScript), &out, "bash", "-s"); err != nil {
		return false
	}
	return strings.Contains(out.String(), "RUNNING")
}

// exitAlreadyRunning: litecode-wsl-start 发现服务已在运行时的退出码 (不算崩溃, 不自动重拉)
const exitAlreadyRunning = 75

// bindEnv: 桌面版服务只监听 127.0.0.1 (WSL 开镜像网络时 0.0.0.0 会让局域网直连)。
// 经 WSLENV 传进子系统, 旧系统包的启动脚本没设这个变量时同样生效; 服务器版 docker 部署不受影响。
func bindEnv() []string {
	env := os.Environ()
	wslenv := "LITECODE_BIND_HOST/u:LITECODE_DESKTOP/u"
	for i, kv := range env {
		if strings.HasPrefix(strings.ToUpper(kv), "WSLENV=") {
			if cur := kv[len("WSLENV="):]; cur != "" {
				wslenv = cur + ":" + wslenv
			}
			env = append(env[:i], env[i+1:]...)
			break
		}
	}
	// LITECODE_DESKTOP=1: 告诉服务它是桌面版 (系统提示里注入本机 Windows 目录与操作方式)
	return append(env, "LITECODE_BIND_HOST=127.0.0.1", "LITECODE_DESKTOP=1", "WSLENV="+wslenv)
}

func startService() error {
	svc.Lock()
	defer svc.Unlock()
	if svc.cmd != nil {
		return nil
	}
	if serviceRunning() { // 已在跑 (安装时拉起的 / 上次退出托盘时保持运行的), 不重复拉起
		logf("服务已在运行, 不重复启动")
		return nil
	}
	if !distroExists() {
		// 安装/升级没做完 (子系统不在): 别反复拉起, 直接告诉用户怎么办 (2026-10-21 真机演练: 原来每 5 秒失败一次)
		msg := "LiteCode 还没有装好（找不到 LiteCode 子系统），请重新运行安装程序。"
		if b, err := os.ReadFile(filepath.Join(dataDir(), "upgrade-pending.json")); err == nil {
			msg += "\n上次升级没有完成，重新安装会自动从备份恢复对话和设置。\n" + trunc(string(b), 300)
		}
		logf("子系统不存在, 不启动服务")
		return errors.New(msg)
	}
	if st, err := os.Stat(serviceLog()); err == nil && st.Size() > 20<<20 {
		os.Rename(serviceLog(), serviceLog()+".old")
	}
	lf, err := os.OpenFile(serviceLog(), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	fmt.Fprintf(lf, "\r\n==== %s 启动 LiteCode ====\r\n", time.Now().Format("2006-01-02 15:04:05"))
	cmd := exec.Command("wsl.exe", "-d", distro, "-u", "root", "--", "/usr/local/bin/litecode-wsl-start")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	cmd.Stdout, cmd.Stderr = lf, lf
	cmd.Env = bindEnv()
	if err := cmd.Start(); err != nil {
		lf.Close()
		return fmt.Errorf("启动 LiteCode 失败: %v", err)
	}
	svc.cmd, svc.stopping = cmd, false
	svc.done = make(chan struct{})
	done := svc.done
	logf("服务进程已启动 pid=%d", cmd.Process.Pid)
	go func() {
		err := cmd.Wait()
		lf.Close()
		svc.Lock()
		stopping := svc.stopping
		svc.cmd = nil
		close(done)
		svc.Unlock()
		logf("服务进程退出 err=%v stopping=%v", err, stopping)
		if stopping {
			return
		}
		if cmd.ProcessState != nil && cmd.ProcessState.ExitCode() == exitAlreadyRunning {
			logf("子系统里已有服务在运行, 本次不重复启动")
			return
		}
		// 意外退出: 5 分钟内最多自动拉起 3 次
		now := time.Now()
		svc.Lock()
		recent := []time.Time{}
		for _, t := range svc.crashes {
			if now.Sub(t) < 5*time.Minute {
				recent = append(recent, t)
			}
		}
		svc.crashes = append(recent, now)
		n := len(svc.crashes)
		svc.Unlock()
		if n <= 3 {
			time.Sleep(5 * time.Second)
			startService()
		} else {
			setTip("LiteCode · 反复退出，请查看日志")
		}
	}()
	return nil
}

// stopService 结束子系统 (服务随之停止), 并等托盘持有的 wsl.exe 退出:
// 否则紧接着的 startService 会看到旧 cmd 还没清掉而什么都不做 (更新/回滚后服务起不来)。
func stopService() {
	svc.Lock()
	svc.stopping = true
	done := svc.done
	svc.Unlock()
	wsl(2*time.Minute, "--terminate", distro)
	if done != nil {
		select {
		case <-done:
		case <-time.After(30 * time.Second):
			logf("等服务进程退出超时 (30 秒)")
		}
	}
}

// ── 注册表: 开机自启 / 重启后继续安装 ──

func setRunOnce(args string) {
	k, _, err := registry.CreateKey(registry.CURRENT_USER, `Software\Microsoft\Windows\CurrentVersion\RunOnce`, registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	_ = k.SetStringValue("LiteCodeSetup", fmt.Sprintf(`"%s" %s`, filepath.Join(dataDir(), "LiteCode.exe"), args))
}

func clearRunOnce() {
	k, err := registry.OpenKey(registry.CURRENT_USER, `Software\Microsoft\Windows\CurrentVersion\RunOnce`, registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	_ = k.DeleteValue("LiteCodeSetup")
}

func setAutostart(on bool) {
	k, _, err := registry.CreateKey(registry.CURRENT_USER, `Software\Microsoft\Windows\CurrentVersion\Run`, registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	if on {
		_ = k.SetStringValue(appName, fmt.Sprintf(`"%s"`, filepath.Join(dataDir(), "LiteCode.exe")))
	} else {
		_ = k.DeleteValue(appName)
	}
}

// ── Web ──

func webReady() bool {
	c := &http.Client{Timeout: 5 * time.Second}
	resp, err := c.Get(webURL)
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == 200
}

// serverHealthy: LiteCode Server 的公开存活探针 /health
func serverHealthy() bool {
	c := &http.Client{Timeout: 5 * time.Second}
	resp, err := c.Get(healthURL)
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == 200
}

// waitHealthy: Web 页面和 Server /health 都正常才算服务真起来了 (升级删备份、更新判定成功都以它为准)
func waitHealthy(limit time.Duration) bool {
	t0 := time.Now()
	for time.Since(t0) < limit {
		if webReady() && serverHealthy() {
			return true
		}
		time.Sleep(3 * time.Second)
	}
	return false
}

func waitWeb(limit time.Duration) bool {
	t0 := time.Now()
	for time.Since(t0) < limit {
		if webReady() {
			return true
		}
		time.Sleep(3 * time.Second)
	}
	return false
}

// openWindow: 服务就绪才开窗口 (2026-10-21 实机: 导入还没完就双击图标, 窗口一片空白);
// 没就绪先提示, 最多等 3 分钟。优先用 Edge / Chrome 的应用模式 (独立窗口, 没有地址栏), 都没有就用默认浏览器。
func openWindow() {
	if !webReady() {
		go zenity.Notify("LiteCode 正在启动，就绪后会自动打开窗口…", zenity.Title(appName))
		if !waitWeb(3 * time.Minute) {
			zenity.Warning("LiteCode 还没启动好，可在托盘菜单「查看日志」里看原因。", zenity.Title(appName))
			return
		}
	}
	openWindowNow()
}

func openWindowNow() {
	if h := existingAppWindow(); h != 0 { // 已经开着就切过去, 不再新开一个
		bringToFront(h)
		return
	}
	for _, p := range []string{
		filepath.Join(os.Getenv("ProgramFiles(x86)"), `Microsoft\Edge\Application\msedge.exe`),
		filepath.Join(os.Getenv("ProgramFiles"), `Microsoft\Edge\Application\msedge.exe`),
		filepath.Join(os.Getenv("ProgramFiles"), `Google\Chrome\Application\chrome.exe`),
		filepath.Join(os.Getenv("LOCALAPPDATA"), `Google\Chrome\Application\chrome.exe`),
	} {
		if fileExists(p) {
			if exec.Command(p, "--app="+webURL, "--window-size=1280,860").Start() == nil {
				return
			}
		}
	}
	_ = exec.Command("rundll32", "url.dll,FileProtocolHandler", webURL).Start()
}
