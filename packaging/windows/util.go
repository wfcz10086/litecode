package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
)

var logger *log.Logger

func logPath() string { return filepath.Join(dataDir(), "launcher.log") }

func logInit() {
	f, err := os.OpenFile(logPath(), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		logger = log.New(io.Discard, "", 0)
		return
	}
	logger = log.New(f, "", log.LstdFlags)
}

func logf(format string, a ...any) {
	if logger != nil {
		logger.Printf(format, a...)
	}
}

// run 执行命令, 不弹黑色控制台窗口; 返回合并输出
func run(timeout time.Duration, name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	var b, eb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &b, &eb
	err := cmd.Run()
	// 2026-10-22 用户机: Windows 开着本机代理 (Clash 等) 时 wsl.exe 会打 "wsl: 检测到 localhost 代理配置…NAT 模式…" 警告;
	// 以前 stdout/stderr 合在一起, 警告混进 cat 出来的 JSON 和版本号 ("读默认配置失败: invalid character 'w'")。
	// 现在只返回 stdout (去掉 wsl: 开头的警告行); stderr 只在出错时附上, 方便看原因。
	out := stripWSLNotice(b.String())
	if err != nil {
		if e := stripWSLNotice(eb.String()); e != "" {
			out = strings.TrimSpace(out + "\n" + e)
		}
	}
	logf("$ %s %s -> err=%v out=%s stderr=%s", name, redact(strings.Join(args, " ")), err, trunc(out, 400), trunc(strings.TrimSpace(eb.String()), 200))
	if ctx.Err() == context.DeadlineExceeded {
		return out, fmt.Errorf("%s 超时 (%s)", name, timeout)
	}
	return out, err
}

// runElevated 以管理员身份运行并等待, 返回退出码。本进程已是管理员时直接运行 (不弹 UAC,
// 自动化测试用最高权限计划任务启动安装器); 否则经 Start-Process -Verb RunAs 弹一次 UAC 确认。
func runElevated(exe string, args string) (int, error) {
	if windows.GetCurrentProcessToken().IsElevated() {
		_, err := run(2*time.Hour, exe, splitArgs(args)...)
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode(), nil
		}
		if err != nil {
			return -1, err
		}
		return 0, nil
	}
	ps := fmt.Sprintf("$p = Start-Process -FilePath '%s' -ArgumentList '%s' -Verb RunAs -Wait -PassThru; exit $p.ExitCode",
		strings.ReplaceAll(exe, "'", "''"), strings.ReplaceAll(args, "'", "''"))
	_, err := run(2*time.Hour, "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps)
	if ee, ok := err.(*exec.ExitError); ok {
		return ee.ExitCode(), nil
	}
	if err != nil {
		return -1, err
	}
	return 0, nil
}

func splitArgs(s string) []string { return strings.Fields(s) }

func redact(s string) string {
	for _, k := range []string{"api_key", "password", "token"} {
		if i := strings.Index(strings.ToLower(s), k); i >= 0 {
			return s[:i] + k + "=***"
		}
	}
	return s
}

func trunc(s string, n int) string {
	if len(s) > n {
		return s[:n] + "…"
	}
	return s
}

type Release struct {
	Version         string `json:"version"`   // 代码版本
	CodeFile        string `json:"code_file"` // 代码更新包 (litecode/ + entrypoint.sh)
	CodeSHA256      string `json:"code_sha256"`
	CodeSize        int64  `json:"code_size"`
	RootfsVersion   string `json:"rootfs_version"` // 系统包版本: 比本机新才需要完整更新
	InstallerFile   string `json:"installer_file"` // 完整离线安装包
	InstallerSHA256 string `json:"installer_sha256"`
	InstallerSize   int64  `json:"installer_size"`
	ModelsFile      string `json:"models_file"` // 模型包 (本地 TTS + 知识库向量模型), 不进安装包, 托盘后台下载 (models.go)
	ModelsSHA256    string `json:"models_sha256"`
	ModelsSize      int64  `json:"models_size"`
}

func fetchRelease() (*Release, error) {
	c := &http.Client{Timeout: 30 * time.Second}
	resp, err := c.Get(BaseURL + "latest.json")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("latest.json HTTP %d", resp.StatusCode)
	}
	var r Release
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return nil, err
	}
	return &r, nil
}

// download 断点续传下载到 dst, 校验 sha256; progress(已下载, 总大小)
func download(url, dst, wantSHA string, progress func(done, total int64)) error {
	part := dst + ".part"
	if fileExists(dst) {
		if ok, _ := sha256Is(dst, wantSHA); ok {
			return nil
		}
		os.Remove(dst)
	}
	var have int64
	if st, err := os.Stat(part); err == nil {
		have = st.Size()
	}
	req, _ := http.NewRequest("GET", url, nil)
	if have > 0 {
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-", have))
	}
	resp, err := (&http.Client{}).Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	flag := os.O_CREATE | os.O_WRONLY
	switch resp.StatusCode {
	case 206:
		flag |= os.O_APPEND
	case 200:
		have = 0
		flag |= os.O_TRUNC
	default:
		return fmt.Errorf("下载 %s: HTTP %d", url, resp.StatusCode)
	}
	total := have + resp.ContentLength
	f, err := os.OpenFile(part, flag, 0o644)
	if err != nil {
		return err
	}
	buf := make([]byte, 1<<20)
	last := time.Now()
	for {
		n, rerr := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := f.Write(buf[:n]); werr != nil {
				f.Close()
				return werr
			}
			have += int64(n)
			if progress != nil && time.Since(last) > 300*time.Millisecond {
				progress(have, total)
				last = time.Now()
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			f.Close()
			return fmt.Errorf("下载中断 (可重新运行继续): %w", rerr)
		}
	}
	f.Close()
	if ok, got := sha256Is(part, wantSHA); !ok {
		os.Remove(part)
		return fmt.Errorf("文件校验失败: %s sha256=%s 期望 %s", filepath.Base(dst), got, wantSHA)
	}
	return os.Rename(part, dst)
}

func sha256Is(p, want string) (bool, string) {
	f, err := os.Open(p)
	if err != nil {
		return false, ""
	}
	defer f.Close()
	h := sha256.New()
	io.Copy(h, f)
	got := hex.EncodeToString(h.Sum(nil))
	return want == "" || strings.EqualFold(got, want), got
}


// urlFor: latest.json 里的文件名是相对 BaseURL 的; 写成完整网址时直接用
// (代码更新包可以指向 GitHub 的标签源码包, 发版 = 推标签)
func urlFor(name string) string {
	if strings.HasPrefix(name, "https://") || strings.HasPrefix(name, "http://") {
		return name
	}
	return BaseURL + name
}

// localName: 下载到本地用的文件名 (完整网址取最后一段; GitHub 源码包网址最后一段是标签名, 补上扩展名)
func localName(name string) string {
	if i := strings.LastIndex(name, "/"); i >= 0 && strings.Contains(name, "://") {
		name = name[i+1:]
		if !strings.Contains(name, ".") || strings.HasPrefix(name, "v") && !strings.HasSuffix(name, ".gz") {
			name += ".tar.gz"
		}
	}
	return name
}

// stripWSLNotice 去掉 wsl.exe 自己打的提示行 (以 "wsl:" 开头, 如 localhost 代理 / NAT 模式警告), 其余原样
func stripWSLNotice(s string) string {
	var keep []string
	for _, line := range strings.Split(strings.ReplaceAll(s, "\r\n", "\n"), "\n") {
		if strings.HasPrefix(strings.TrimSpace(strings.TrimPrefix(line, "\ufeff")), "wsl:") {
			continue
		}
		keep = append(keep, line)
	}
	return strings.TrimSpace(strings.Join(keep, "\n"))
}
