package upgrade

import (
	"fmt"
	"strings"
)

// litecode-apply-update 的退出码约定 (见 packaging/windows/wsl/litecode-apply-update)
const (
	ApplyOK          = 0
	ApplyUnchanged   = 2 // 代码包无效/准备阶段失败, 运行中的版本没动过
	ApplyRestored    = 3 // 切换中途失败, 已恢复原版本
	ApplyNotRestored = 4 // 切换中途失败且恢复失败, 需人工处理
)

// CodeOps 是代码更新用到的外部操作。Apply/Rollback 返回 (输出, 退出码, 错误); 超时等拿不到退出码时 code = -1。
type CodeOps struct {
	Log      func(format string, a ...any)
	Stop     func()
	Apply    func() (string, int, error)
	Rollback func() (string, int, error)
	Start    func() bool // 启动服务并等健康检查通过
	Version  func() string
	LogHint  string // 提示用户去哪看日志
}

// Level 对应弹窗类型
type Level int

const (
	Info Level = iota
	Warn
	Error
)

// CodeResult: Running 表示提示发出时服务确实通过了健康检查。
type CodeResult struct {
	Updated bool
	Running bool
	Level   Level
	Message string
}

func short(s string) string {
	s = strings.TrimSpace(s)
	if r := []rune(s); len(r) > 300 {
		return "…" + string(r[len(r)-300:])
	}
	return s
}

// RunCodeUpdate: 停服务 → 原地替换 → 启动并健康检查; 新版起不来就回滚并再次确认服务真起来了。
// 每条提示都只根据实际结果 (健康检查 + 子系统里的版本号) 生成。
func RunCodeUpdate(o CodeOps, newVer string) CodeResult {
	o.Stop()
	out, code, err := o.Apply()
	if err != nil {
		o.Log("代码更新失败 code=%d err=%v out=%s", code, err, short(out))
		running := o.Start()
		cur := o.Version()
		reason := short(out)
		if reason == "" {
			reason = err.Error()
		}
		switch {
		case running && cur == newVer: // 脚本超时等拿不到结果, 但实际已经切换并正常运行
			return CodeResult{Updated: true, Running: true, Level: Info, Message: "已更新到 " + cur}
		case running:
			return CodeResult{Running: true, Level: Warn, Message: fmt.Sprintf(
				"更新到 %s 失败，当前仍是 %s，服务正常运行。\n原因：%s", newVer, cur, reason)}
		case code == ApplyUnchanged || code == ApplyRestored:
			return CodeResult{Level: Error, Message: fmt.Sprintf(
				"更新到 %s 失败，程序文件已保持/恢复为 %s，但服务没有启动成功。\n原因：%s\n%s", newVer, cur, reason, o.LogHint)}
		default:
			return CodeResult{Level: Error, Message: fmt.Sprintf(
				"更新到 %s 失败，自动恢复原版本也没有完成，服务目前没有运行（当前版本记录：%s）。\n原因：%s\n"+
					"旧版本保存在子系统的 /opt/litecode.prev。可重新运行完整安装包修复（对话和设置会保留）。\n%s",
				newVer, cur, reason, o.LogHint)}
		}
	}
	if o.Start() {
		cur := o.Version()
		if cur != newVer {
			return CodeResult{Running: true, Level: Warn, Message: fmt.Sprintf(
				"更新脚本已完成，但当前版本是 %s（预期 %s）。服务正常运行。", cur, newVer)}
		}
		return CodeResult{Updated: true, Running: true, Level: Info, Message: "已更新到 " + cur}
	}
	// 新版起不来 → 回滚 → 再次确认服务真起来了
	o.Log("新版本 %s 启动失败, 回滚", newVer)
	o.Stop()
	rout, rcode, rerr := o.Rollback()
	if rerr != nil {
		o.Log("回滚失败 code=%d err=%v out=%s", rcode, rerr, short(rout))
		reason := short(rout)
		if reason == "" {
			reason = rerr.Error()
		}
		return CodeResult{Level: Error, Message: fmt.Sprintf(
			"新版本 %s 启动失败，自动回滚也失败了，服务目前没有运行。\n回滚输出：%s\n"+
				"可重新运行完整安装包修复（对话和设置会保留）。\n%s", newVer, reason, o.LogHint)}
	}
	running := o.Start()
	cur := o.Version()
	if running {
		return CodeResult{Running: true, Level: Warn, Message: fmt.Sprintf(
			"新版本 %s 启动失败，已回滚到 %s，服务已恢复正常运行。\n%s", newVer, cur, o.LogHint)}
	}
	return CodeResult{Level: Error, Message: fmt.Sprintf(
		"新版本 %s 启动失败，已回滚到 %s，但服务仍没有启动成功。\n%s", newVer, cur, o.LogHint)}
}
