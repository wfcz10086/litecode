package main

// 操控本机: 安装时 (以及托盘「接入本机操控」) 把这台电脑接入成 LiteCode 自己的 Windows 节点 "this-pc"。
//   1. 管理员: 装 OpenSSH Server 并设为开机自启 (安装包自带的离线包优先, 再用 Windows 可选功能), 只放行本机/局域网;
//   2. 管理员: 标准节点接入 (windows_node_bootstrap.ps1) —— 桌面代理 (当前用户登录后运行, 截图/点击/OCR) + SSH 公钥;
//   3. 子系统: 等代理上线, 登记 SSH 别名 this-pc-ssh 并实测。
// 脚本由子系统里的 lib/desktop_local_node.py 生成 (接入码 10 分钟有效), 经 -EncodedCommand 交给提权进程, 不落盘。
// 任何一步失败都不影响 LiteCode 主程序, 托盘菜单可「重新接入本机操控」。

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"fyne.io/systray"
	"github.com/ncruces/zenity"
	"golang.org/x/sys/windows"
)

const localNodeScript = "/opt/litecode/lib/desktop_local_node.py"

func localControlMarker() string { return filepath.Join(dataDir(), "local-control.json") }
func localControlLog() string    { return filepath.Join(dataDir(), "local-control.log") }

type localControlResult struct {
	OK       bool   `json:"ok"`
	SSHOK    bool   `json:"ssh_ok"`
	State    string `json:"state"`
	Exit     int    `json:"exit"`
	Message  string `json:"message"`
	NodeID   string `json:"id"`
	Finished string `json:"finished_at"`
}

func localControlDone() bool {
	var r localControlResult
	b, err := os.ReadFile(localControlMarker())
	return err == nil && json.Unmarshal(b, &r) == nil && r.OK
}

// lastJSON: 子系统里 python 的标准输出可能夹带日志行, 取最后一个 JSON 对象行
func lastJSON(out string, v any) error {
	lines := strings.Split(strings.TrimSpace(out), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		l := strings.TrimSpace(lines[i])
		if strings.HasPrefix(l, "{") && json.Unmarshal([]byte(l), v) == nil {
			return nil
		}
	}
	return fmt.Errorf("没有拿到结果: %s", trunc(out, 300))
}

func pyInDistro(timeout time.Duration, v any, args ...string) error {
	var out strings.Builder
	err := inDistroIO(timeout, nil, &out, append([]string{"python3", localNodeScript}, args...)...)
	if err != nil {
		return err
	}
	return lastJSON(out.String(), v)
}

// exitMessage: 管理员脚本退出码 → 给用户看的说明 (与 desktop_local_node.EXIT_* 对应)
func exitMessage(code int) string {
	switch code {
	case 0:
		return ""
	case 10:
		return "OpenSSH Server 没装上（常见原因：连不上 Windows 更新），桌面代理已接入，截图/点击照常可用。"
	case 20:
		return "OpenSSH 已就绪，但桌面代理没装上（常见原因：首次需联网下载 Python 与依赖）。"
	case 30:
		return "OpenSSH 与桌面代理都没装上。"
	case 40:
		return "管理员确认时用了另一个 Windows 账户，桌面代理没有安装。请用当前账户（需是管理员）重新接入。"
	case 1, 1223, -1: // 没点"是" (Start-Process -Verb RunAs 报错退出 1)
		return "管理员权限确认被取消，或接入脚本没能启动。"
	}
	return fmt.Sprintf("接入脚本退出码 %d。", code)
}

// setupLocalControl 跑完整的本机接入, 结果写 local-control.json; 返回给用户看的一句话。
func setupLocalControl() (localControlResult, error) {
	res := localControlResult{Finished: time.Now().Format(time.RFC3339)}
	defer func() {
		b, _ := json.Marshal(res)
		os.WriteFile(localControlMarker(), b, 0o644)
	}()
	// 离线 OpenSSH 包从子系统里拷出来给提权脚本用 (脚本内校验 sha256 与微软签名)
	zip := filepath.Join(dataDir(), "payload", "openssh.zip")
	var zp struct {
		Path string `json:"path"`
	}
	if err := pyInDistro(time.Minute, &zp, "zip-path"); err == nil && zp.Path != "" {
		os.MkdirAll(filepath.Dir(zip), 0o755)
		if f, err := os.Create(zip); err == nil {
			err = inDistroIO(2*time.Minute, nil, f, "cat", zp.Path)
			f.Close()
			if err != nil {
				os.Remove(zip)
			}
		}
	}
	defer os.Remove(zip)
	var prep struct {
		ID     string `json:"id"`
		Script string `json:"script"`
	}
	// wsl.exe 会经 shell 转发参数 (反斜杠被吃掉): Windows 路径与账户名整体 base64(JSON) 传
	params, _ := json.Marshal(map[string]string{"user": os.Getenv("USERDOMAIN") + `\` + os.Getenv("USERNAME"),
		"zip": zip, "log": localControlLog()})
	if err := pyInDistro(2*time.Minute, &prep, "prepare", "--params", base64.StdEncoding.EncodeToString(params)); err != nil {
		res.Message = "生成接入脚本失败: " + err.Error()
		return res, err
	}
	res.NodeID = prep.ID
	code, err := runElevated("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -EncodedCommand "+encodePS(prep.Script))
	logf("本机接入脚本退出码 %d err=%v (日志 %s)", code, err, localControlLog())
	res.Exit = code
	if err != nil {
		res.Message = "运行接入脚本失败: " + err.Error()
		return res, err
	}
	if code != 0 && code != 10 { // 10 = 只有 OpenSSH 没装上, 桌面代理照常接入
		res.Message = exitMessage(code)
		return res, fmt.Errorf("%s", res.Message)
	}
	var fin struct {
		State string `json:"state"`
		Error string `json:"error"`
		SSHOK bool   `json:"ssh_ok"`
	}
	if err := pyInDistro(5*time.Minute, &fin, "finish", "--id", prep.ID, "--wait", "180"); err != nil {
		res.Message = "确认节点上线失败: " + err.Error()
		return res, err
	}
	res.State, res.SSHOK = fin.State, fin.SSHOK
	res.OK = fin.State == "online"
	switch {
	case !res.OK:
		res.Message = fmt.Sprintf("桌面代理已安装，但 3 分钟内没有上线（状态 %s %s）。请确认已登录 Windows 桌面，稍后在托盘重新接入。", fin.State, fin.Error)
		return res, fmt.Errorf("%s", res.Message)
	case fin.SSHOK:
		res.Message = "已接入本机操控：LiteCode 可以截图/点击本机桌面（this-pc），并经 SSH 执行命令（this-pc-ssh）。"
	default:
		res.Message = "已接入本机操控（this-pc）。" + exitMessage(code) + " SSH 暂时连不上，命令行操作照常走 WSL。"
	}
	return res, nil
}

// setupStepLocalControl: 安装流程的最后一步 (默认执行; --no-local-control 跳过)。失败只提示, 不算安装失败。
func setupStepLocalControl(say func(int, string, ...any)) {
	if opt.NoLocalControl || localControlDone() {
		return
	}
	if opt.Unattended && !windows.GetCurrentProcessToken().IsElevated() {
		logf("无人值守且非管理员: 跳过本机操控接入 (托盘菜单可补做)")
		return
	}
	if !opt.Unattended {
		// 2026-10-22 用户反馈: 这一步要联网下 Python 和依赖, 代理/网络慢时安装窗口一直卡在这里。
		// 交互安装改成登记一下, 安装窗口直接结束; 托盘起来后在后台接入, 完成后弹通知 (见 resumeLocalControl)。
		os.WriteFile(localControlPending(), []byte(time.Now().Format(time.RFC3339)), 0o644)
		say(96, "本机操控会在 LiteCode 启动后于后台接入，完成后右下角提示")
		return
	}
	say(96, "接入本机操控（安装 OpenSSH 与桌面代理，会请求一次管理员权限，约 2~5 分钟）…")
	res, err := setupLocalControl()
	logf("本机操控接入: %+v err=%v", res, err)
	if err != nil && !opt.Unattended {
		zenity.Warning("LiteCode 已装好，但"+res.Message+"\n\n可稍后在托盘菜单点「重新接入本机操控」。\n日志："+localControlLog(),
			zenity.Title(appName))
	}
}

// addLocalControlMenu: 托盘菜单「接入本机操控 / 重新接入本机操控」
func addLocalControlMenu() {
	title := func() string {
		if localControlDone() {
			return "重新接入本机操控"
		}
		return "接入本机操控"
	}
	m := systray.AddMenuItem(title(), "安装 OpenSSH 与桌面代理，让 LiteCode 能截图、点击和执行命令操控这台电脑")
	go func() {
		for range m.ClickedCh {
			if !webReady() {
				zenity.Warning("LiteCode 服务还没启动好，请稍后再试。", zenity.Title(appName))
				continue
			}
			if ok, _ := askYes("将安装/更新 OpenSSH Server（开机自启，只允许本机和局域网、仅密钥登录）和 LiteCode 桌面代理，\n" +
				"让 LiteCode 能截图、点击和执行命令操控这台电脑。需要管理员权限，约 2~5 分钟。继续吗？"); !ok {
				continue
			}
			m.Disable()
			go zenity.Notify("正在接入本机操控，完成后会提示…", zenity.Title(appName))
			res, err := setupLocalControl()
			logf("托盘本机操控接入: %+v err=%v", res, err)
			if err != nil {
				zenity.Warning(res.Message+"\n\n日志："+localControlLog(), zenity.Title(appName))
			} else {
				zenity.Info(res.Message, zenity.Title(appName))
			}
			m.SetTitle(title())
			m.Enable()
		}
	}()
}

// removeLocalControl: 卸载 LiteCode 时 (NSIS 调 --remove-local-control) 移除本机桌面代理。
// OpenSSH Server 保留 (系统组件, 用户可能另有用途), 只撤销代理与它的 SSH 公钥。
func removeLocalControl() {
	script := `$base = Join-Path $env:LOCALAPPDATA 'LiteCodeNodes'
foreach ($d in @(Get-ChildItem $base -Directory -ErrorAction SilentlyContinue)) {
  $f = Join-Path $d.FullName 'node.json'; $u = Join-Path $d.FullName 'uninstall.ps1'
  if ((Test-Path $f) -and (Test-Path $u)) {
    $c = Get-Content $f -Raw | ConvertFrom-Json
    if ($c.alias -eq 'this-pc' -and $c.server -eq 'http://127.0.0.1:18790') {
      try { & $u -Root $d.FullName -Schedule } catch { Write-Warning $_.Exception.Message }
    }
  }
}`
	code, err := runElevated("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -EncodedCommand "+encodePS(script))
	logf("卸载本机桌面代理: 退出码 %d err=%v", code, err)
	os.Remove(localControlMarker())
}

func localControlPending() string { return filepath.Join(dataDir(), "local-control-pending") }

// resumeLocalControl: 安装时登记的本机操控接入, 托盘启动、服务就绪后在后台做 (只做一次; 失败可从托盘菜单重试)
func resumeLocalControl() {
	if !fileExists(localControlPending()) {
		return
	}
	if localControlDone() {
		os.Remove(localControlPending())
		return
	}
	for i := 0; i < 60 && !webReady(); i++ {
		time.Sleep(5 * time.Second)
	}
	os.Remove(localControlPending()) // 不论成败只自动做一次, 免得每次开机都弹管理员确认
	setTip("LiteCode · 正在接入本机操控…")
	go zenity.Notify("正在后台接入本机操控（会请求一次管理员权限，联网下载组件约 2~5 分钟），期间可以正常使用 LiteCode。",
		zenity.Title(appName))
	res, err := setupLocalControl()
	logf("本机操控接入(后台): %+v err=%v", res, err)
	setTip(fmt.Sprintf("LiteCode %s · 运行中", codeVersion()))
	if err != nil {
		go zenity.Notify("本机操控没接入成功，可在托盘菜单点「接入本机操控」重试。", zenity.Title(appName))
		return
	}
	go zenity.Notify(res.Message, zenity.Title(appName))
}
