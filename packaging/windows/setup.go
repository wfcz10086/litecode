package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode/utf16"

	"github.com/ncruces/zenity"

	"litecode-launcher/upgrade"
)

// 安装包 (NSIS) 把 LiteCode.exe 和系统包 payload\litecode-rootfs-<版本>.tar.gz 放进 %LOCALAPPDATA%\LiteCode,
// 然后运行 `LiteCode.exe --setup`。全程不联网下载。
func payloadRootfs() (path, ver string) {
	m, _ := filepath.Glob(filepath.Join(dataDir(), "payload", "litecode-rootfs-*.tar.gz"))
	if len(m) == 0 {
		return "", ""
	}
	sort.Strings(m)
	p := m[len(m)-1]
	v := strings.TrimSuffix(strings.TrimPrefix(filepath.Base(p), "litecode-rootfs-"), ".tar.gz")
	return p, v
}

// payloadCode: 安装包附带的代码更新包 (payload\litecode-code-<版本>.tar.gz)
func payloadCode() string {
	m, _ := filepath.Glob(filepath.Join(dataDir(), "payload", "litecode-code-*.tar.gz"))
	if len(m) == 0 {
		return ""
	}
	sort.Strings(m)
	return m[len(m)-1]
}

// 升级系统包时要带过去的数据 (对话、记忆、工作区、配置、草稿技能、微信登录)。
// 只有确实不存在的路径才跳过 (记日志); 已完成配置 (有 etc/litecode-configured) 时 config.json 必须在。
var dataSpecs = []upgrade.Spec{
	{Path: "opt/litecode/config.json", RequiredIf: "etc/litecode-configured"},
	{Path: "opt/litecode/skills/drafts"}, {Path: "tmp/litecode_workspace"},
	{Path: "root/.litecode"}, {Path: "root/.wechatbot"}, {Path: "etc/litecode-configured"},
	{Path: "var/lib/litecode/kb_pg"},  // 内置知识库 PostgreSQL 数据目录 (wsl/litecode-kb-pg)
	{Path: "var/lib/litecode/models"}, // 模型包 (models.go): 升级不重下
}

func newUpgrader(payloadVer string, say func(int, string, ...any)) *upgrade.Upgrader {
	return &upgrade.Upgrader{Dir: dataDir(), Specs: dataSpecs, Payload: payloadVer, Env: upgrade.Env{
		Log:           logf,
		Say:           say,
		DistroExists:  distroExists,
		RootfsVersion: rootfsVersion,
		Stop:          stopService,
		RunProbe: func(script string) (string, error) {
			var out strings.Builder
			err := inDistroIO(10*time.Minute, strings.NewReader(script), &out, "bash", "-s")
			logf("备份探测: %s", trunc(out.String(), 600))
			return out.String(), err
		},
		Backup: func(dst string, paths []string) error {
			f, err := os.Create(dst)
			if err != nil {
				return err
			}
			// 不加 --ignore-failed-read: 任何读取失败都让 tar 非 0 退出, 升级随之停止
			err = inDistroIO(30*time.Minute, nil, f, append([]string{"tar", "-czf", "-", "-C", "/"}, paths...)...)
			if serr := f.Sync(); err == nil {
				err = serr
			}
			if cerr := f.Close(); err == nil {
				err = cerr
			}
			return err
		},
		Unregister: func() error {
			wsl(2*time.Minute, "--terminate", distro)
			if out, err := wsl(10*time.Minute, "--unregister", distro); err != nil {
				return fmt.Errorf("%v %s", err, trunc(out, 300))
			}
			return nil
		},
		Import: func() error {
			rootfs, _ := payloadRootfs()
			if rootfs == "" {
				return fmt.Errorf("安装包不完整：没找到 LiteCode 系统包 (payload)")
			}
			return importDistro(rootfs)
		},
		Restore: func(src string) error {
			b, err := os.Open(src)
			if err != nil {
				return err
			}
			defer b.Close()
			return inDistroIO(30*time.Minute, b, io.Discard, "tar", "-xzf", "-", "-C", "/")
		},
	}}
}

func runSetup() error {
	var dlg zenity.ProgressDialog
	if !opt.Unattended {
		dlg, _ = zenity.Progress(zenity.Title("安装 LiteCode"), zenity.MaxValue(100))
	}
	say := func(pct int, f string, a ...any) {
		msg := fmt.Sprintf(f, a...)
		logf("[setup] %s", msg)
		if dlg != nil {
			dlg.Text(msg)
			if pct >= 0 {
				dlg.Value(pct)
			}
		}
	}
	closeDlg := func() {
		if dlg != nil {
			dlg.Close()
			dlg = nil
		}
	}
	defer closeDlg()

	rootfs, ver := payloadRootfs()

	// 1. WSL (Windows 自带; 从没开过时要管理员权限 + 可能重启一次)
	say(3, "检查 WSL…")
	rebootMark := filepath.Join(dataDir(), "wsl-reboot-requested")
	if !wslReady() && fileExists(rebootMark) {
		// 已经为开启 WSL 重启过一次, 回来还是不可用: 不再要求重启 (否则会无限重启)
		clearRunOnce()
		os.Remove(rebootMark)
		return fmt.Errorf("已经重启过一次，WSL 仍然不可用，安装停止（不会再要求重启）。\n" +
			"常见原因：BIOS 未开启虚拟化；虚拟机里没开嵌套虚拟化；公司策略禁用了 WSL。\n" +
			"可在管理员 PowerShell 里运行 wsl --status 和 wsl --install 查看具体提示")
	}
	if wslReady() {
		os.Remove(rebootMark)
	} else {
		// [2026-10-21 实机] wsl --install 在这类机器上直接返回 1。改为: dism 只开没开的两个系统功能 +
		// 静默装安装包自带的 WSL (payload\wsl.msi, 离线); 只有真的新开了系统功能才需要重启。
		say(5, "开启 WSL（会请求一次管理员权限）…")
		code, err := enableWSL()
		logf("开启 WSL 退出码 %d err=%v", code, err)
		if err != nil || (code != 0 && code != 3010) {
			return fmt.Errorf("开启 WSL 失败 (退出码 %d)。可在管理员 PowerShell 里运行 wsl --install 看提示", code)
		}
		if code == 3010 || !wslReady() {
			closeDlg()
			setRunOnce("--setup")
			os.WriteFile(rebootMark, []byte(time.Now().Format(time.RFC3339)), 0o644)
			if opt.Unattended {
				logf("WSL 已开启, 需要重启后继续 (RunOnce 已登记)")
				os.Exit(3010)
			}
			if ok, _ := askYes("WSL 已开启，需要重启电脑后继续安装。\n重启后会自动接着装，现在重启吗？"); ok {
				run(30*time.Second, "shutdown", "/r", "/t", "5")
			}
			os.Exit(0)
		}
	}

	// 1b. 本机代理 → WSL 镜像网络 (否则子系统用不了代理, wsl.exe 还会往输出里打警告)
	ensureWSLConfig(say)

	// 2. 子系统: 没有就导入; 已有但系统包版本不同 → 备份数据并校验 → 换新 → 恢复。
	// 备份在新版服务通过健康检查后才删; 中途失败重跑安装会从备份接着恢复 (见 upgrade 包)。
	up := newUpgrader(ver, say)
	pending, err := up.Prepare()
	if err != nil {
		return err
	}

	// 2b. 系统包没变 (依赖层走缓存, 内部版本仍是旧的), 但安装包带的代码更新 → 只换代码, 不重导子系统。
	// [2026-10-21] 原来系统包文件名按发布版本命名、和子系统内部版本永远对不上, 每次跑安装包都整套重导。
	if pkg := payloadCode(); pkg != "" && newer(Version, codeVersion()) {
		say(55, "更新 LiteCode 代码 %s → %s…", codeVersion(), Version)
		stopService()
		out, err := inDistro(5*time.Minute, "/usr/local/bin/litecode-apply-update", winToWSL(pkg), Version)
		logf("安装包代码更新: %v %s", err, trunc(out, 300))
		if err != nil {
			inDistro(5*time.Minute, "/usr/local/bin/litecode-rollback")
			return fmt.Errorf("代码更新失败, 已保持原版本 %s: %v%s", codeVersion(), err, upgrade.KeptBackupHint(pending))
		}
	}

	// 3. 首次配置 (弹窗问密码 / 模型时先收起进度条)
	if distroFile("/etc/litecode-configured") == "" {
		closeDlg()
		if err := ensureConfig(); err != nil {
			return fmt.Errorf("%v%s", err, upgrade.KeptBackupHint(pending))
		}
		if !opt.Unattended {
			dlg, _ = zenity.Progress(zenity.Title("安装 LiteCode"), zenity.MaxValue(100))
		}
	}

	// 4. 启动并等健康检查通过; 升级的备份、安装包里的系统包到这一步成功后才删
	say(70, "启动 LiteCode（首次约 30 秒）…")
	if err := startService(); err != nil {
		return fmt.Errorf("%v%s", err, upgrade.KeptBackupHint(pending))
	}
	if !waitHealthy(5 * time.Minute) {
		return fmt.Errorf("LiteCode 启动超时，日志: %s%s", serviceLog(), upgrade.KeptBackupHint(pending))
	}
	up.Finalize(pending)
	if rootfs != "" {
		os.Remove(rootfs) // 已导入且新版正常运行, 释放 1.6GB
	}

	// 5. 快捷方式 + 开机自启
	say(95, "创建桌面快捷方式…")
	makeShortcuts()
	setAutostart(true)
	b, _ := json.Marshal(map[string]any{"code_version": codeVersion(), "rootfs_version": rootfsVersion(),
		"installed_at": time.Now().Format(time.RFC3339)})
	os.WriteFile(installedMarker(), b, 0o644)
	// 6. 操控本机 (OpenSSH + 桌面代理); 失败只提示, 不影响主程序
	setupStepLocalControl(say)
	say(100, "安装完成")
	return nil
}

// enableWSL 以管理员身份: 只开启还没开的 WSL / 虚拟机平台功能, 没装 WSL 程序就装安装包自带的 msi。
// 返回 3010 = 需要重启。
func enableWSL() (int, error) {
	msi := filepath.Join(dataDir(), "payload", "wsl.msi")
	script := fmt.Sprintf(`$r = 0
foreach ($f in 'Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform') {
  if ((Get-WindowsOptionalFeature -Online -FeatureName $f).State -ne 'Enabled') {
    dism.exe /online /enable-feature /featurename:$f /all /norestart | Out-Null
    if ($LASTEXITCODE -eq 3010) { $r = 3010 } elseif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
}
$wsl = Join-Path $env:ProgramFiles 'WSL\wsl.exe'
if (-not (Test-Path $wsl) -and (Test-Path '%s')) {
  $p = Start-Process msiexec.exe -ArgumentList '/i', '"%s"', '/qn', '/norestart' -Wait -PassThru
  if ($p.ExitCode -eq 3010) { $r = 3010 } elseif ($p.ExitCode -ne 0) { exit $p.ExitCode }
}
exit $r`, msi, msi)
	return runElevated("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -EncodedCommand "+encodePS(script))
}

func encodePS(s string) string {
	u := utf16.Encode([]rune(s))
	b := make([]byte, len(u)*2)
	for i, v := range u {
		b[2*i], b[2*i+1] = byte(v), byte(v>>8)
	}
	return base64.StdEncoding.EncodeToString(b)
}

func makeShortcuts() {
	exe := filepath.Join(dataDir(), "LiteCode.exe")
	ps := fmt.Sprintf(`$s = New-Object -ComObject WScript.Shell
foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  $l = $s.CreateShortcut((Join-Path $d 'LiteCode.lnk'))
  $l.TargetPath = '%s'; $l.WorkingDirectory = '%s'; $l.IconLocation = '%s,0'; $l.Description = 'LiteCode'
  $l.Save()
}`, exe, dataDir(), exe)
	run(60*time.Second, "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps)
}

func askYes(q string) (bool, error) {
	if opt.Unattended {
		return true, nil
	}
	err := zenity.Question(q, zenity.Title(appName), zenity.OKLabel("是"), zenity.CancelLabel("否"))
	return err == nil, err
}

func mb(n int64) string { return fmt.Sprintf("%.0f MB", float64(n)/1048576) }

func max64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}
