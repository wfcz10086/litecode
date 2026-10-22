package upgrade

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"time"
)

// 升级阶段 (写在 pending 标记里):
//   - StageBackedUp:  备份已校验, 旧子系统还在 (此时中断, 数据仍在旧子系统里)
//   - StageReplacing: 即将/已经卸载旧子系统 (此时起数据只在备份里, 必须从备份恢复)
//   - StageRestored:  数据已恢复进新子系统, 等新版服务启动并通过健康检查后才删除备份
const (
	StageBackedUp  = "backed_up"
	StageReplacing = "replacing"
	StageRestored  = "restored"
)

// Pending 是升级进行中标记, 落在数据目录; 只有新版通过健康检查后才和备份一起删除。
type Pending struct {
	Backup  string    `json:"backup"`
	From    string    `json:"from"`
	To      string    `json:"to"`
	Stage   string    `json:"stage"`
	Updated time.Time `json:"updated"`
}

func LoadPending(path string) (*Pending, error) {
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var p Pending
	if err := json.Unmarshal(b, &p); err != nil {
		return nil, fmt.Errorf("升级标记 %s 已损坏: %v", path, err)
	}
	return &p, nil
}

// SavePending 原子写入 (临时文件 + 改名), 断电只会看到旧标记或完整新标记。
func SavePending(path string, p *Pending) error {
	p.Updated = time.Now()
	b, _ := json.MarshalIndent(p, "", "  ")
	tmp := path + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err = f.Write(b); err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, path)
}

// Env 是升级用到的外部操作 (Windows 上由 wsl.exe 实现, 测试里用假实现)。
type Env struct {
	Log           func(format string, a ...any)
	Say           func(pct int, format string, a ...any)
	DistroExists  func() bool
	RootfsVersion func() string
	Stop          func()
	RunProbe      func(script string) (string, error)
	Backup        func(dst string, paths []string) error // 打包到 dst (tar.gz)
	Unregister    func() error                           // 结束并卸载旧子系统
	Import        func() error                           // 导入安装包里的新系统包
	Restore       func(src string) error                 // 把备份解回子系统
}

type Upgrader struct {
	Env
	Dir     string // 备份与标记所在目录 (数据目录)
	Specs   []Spec
	Payload string // 安装包里的系统包版本; "" = 没有 payload
}

func (u *Upgrader) PendingPath() string { return filepath.Join(u.Dir, "upgrade-pending.json") }

var unsafeName = regexp.MustCompile(`[^A-Za-z0-9._-]`)

// Prepare 完成"子系统就绪 + 数据就位"(安装第 2 步)。返回非 nil 的 Pending 表示本次涉及升级:
// 调用方必须在服务启动并通过健康检查后调用 Finalize 才删备份; 失败时用 KeptBackupHint 告诉用户备份位置。
func (u *Upgrader) Prepare() (*Pending, error) {
	p, err := LoadPending(u.PendingPath())
	if err != nil {
		return nil, err
	}
	if p != nil {
		u.Log("发现未完成的升级: %+v", *p)
		if p.Backup == "" || !fileExists(p.Backup) {
			return nil, fmt.Errorf("上次升级未完成, 但备份文件不见了: %q (标记 %s)。为避免用空数据覆盖, 已停止", p.Backup, u.PendingPath())
		}
	}
	exists := u.DistroExists()
	// 上次停在"已备份、旧子系统还在"或卸载其实没发生: 数据仍在旧子系统里, 丢掉旧备份重新走一遍
	if p != nil && (p.Stage == StageBackedUp || (p.Stage == StageReplacing && exists && u.RootfsVersion() == p.From)) {
		u.Log("旧子系统仍在 (版本 %s), 作废上次的备份重新升级", p.From)
		u.discard(p)
		p = nil
	}
	if p == nil {
		switch {
		case !exists && u.Payload == "":
			return nil, fmt.Errorf("安装包不完整：没找到 LiteCode 系统包 (payload)")
		case !exists:
			u.Say(15, "导入 LiteCode（约 1–3 分钟）…")
			return nil, u.Import()
		case u.Payload == "":
			return nil, nil
		}
		cur := u.RootfsVersion()
		if cur == u.Payload {
			return nil, nil
		}
		if p, err = u.backup(cur); err != nil {
			return nil, err
		}
		u.Say(25, "换新版本…")
		p.Stage = StageReplacing
		if err := SavePending(u.PendingPath(), p); err != nil {
			return nil, fmt.Errorf("写升级标记失败, 未做任何改动: %v (备份在 %s)", err, p.Backup)
		}
		if err := u.Unregister(); err != nil {
			return nil, fmt.Errorf("卸载旧版失败: %v%s", err, KeptBackupHint(p))
		}
		exists = false
	}
	if p.Stage == StageReplacing {
		if !exists {
			if u.Payload == "" {
				return nil, fmt.Errorf("安装包不完整, 无法导入新版本%s", KeptBackupHint(p))
			}
			u.Say(30, "导入新版本…")
			if err := u.Import(); err != nil {
				return nil, fmt.Errorf("%v%s", err, KeptBackupHint(p))
			}
		}
		u.Say(40, "恢复对话和设置…")
		if err := u.Restore(p.Backup); err != nil {
			return nil, fmt.Errorf("恢复数据失败: %v%s", err, KeptBackupHint(p))
		}
		p.Stage = StageRestored
		if err := SavePending(u.PendingPath(), p); err != nil {
			u.Log("写升级标记失败 (不影响数据): %v", err)
		}
	}
	return p, nil
}

func (u *Upgrader) backup(cur string) (*Pending, error) {
	u.Say(10, "升级 LiteCode %s → %s：备份对话和设置…", cur, u.Payload)
	u.Stop()
	script, err := ProbeScript(u.Specs)
	if err != nil {
		return nil, err
	}
	out, err := u.RunProbe(script)
	if err != nil {
		return nil, fmt.Errorf("检查要备份的数据失败, 未做任何改动: %v", err)
	}
	probe, err := ParseProbe(out, u.Specs)
	if err != nil {
		return nil, fmt.Errorf("%v (未做任何改动)", err)
	}
	for _, m := range probe.Missing {
		u.Log("备份: 可选路径 /%s 不存在, 跳过", m)
	}
	name := fmt.Sprintf("upgrade-backup-%s-to-%s-%s.tar.gz", unsafeName.ReplaceAllString(cur, "_"),
		unsafeName.ReplaceAllString(u.Payload, "_"), time.Now().Format("20060102-150405"))
	dst := filepath.Join(u.Dir, name)
	part := dst + ".part"
	if err := u.Backup(part, probe.Present); err != nil {
		os.Remove(part)
		return nil, fmt.Errorf("备份数据失败, 未做任何改动: %v", err)
	}
	f, err := os.Open(part)
	if err != nil {
		return nil, fmt.Errorf("备份文件打不开, 未做任何改动: %v", err)
	}
	st, err := VerifyBackup(f, probe.Present, probe.Bytes)
	f.Close()
	if err != nil {
		os.Remove(part)
		return nil, fmt.Errorf("备份校验失败, 未做任何改动: %v", err)
	}
	if err := os.Rename(part, dst); err != nil {
		return nil, fmt.Errorf("保存备份失败, 未做任何改动: %v", err)
	}
	u.Log("备份完成并校验通过: %s (%d 项, %d 字节数据)", dst, st.Entries, st.Bytes)
	p := &Pending{Backup: dst, From: cur, To: u.Payload, Stage: StageBackedUp}
	if err := SavePending(u.PendingPath(), p); err != nil {
		return nil, fmt.Errorf("写升级标记失败, 未做任何改动: %v (备份在 %s)", err, dst)
	}
	return p, nil
}

func (u *Upgrader) discard(p *Pending) {
	os.Remove(p.Backup)
	os.Remove(u.PendingPath())
}

// Finalize 在新版服务通过健康检查后调用: 删除备份和升级标记。
func (u *Upgrader) Finalize(p *Pending) {
	if p == nil {
		return
	}
	u.Log("新版已通过健康检查, 删除升级备份 %s", p.Backup)
	u.discard(p)
}

// KeptBackupHint 失败提示里说明备份位置和下一步。
func KeptBackupHint(p *Pending) string {
	if p == nil {
		return ""
	}
	return fmt.Sprintf("\n\n你的对话和设置已备份在：\n%s\n备份不会被删除。重新运行安装程序会自动从这份备份继续恢复。", p.Backup)
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}
