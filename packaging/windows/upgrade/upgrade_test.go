package upgrade

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

var specs = []Spec{
	{Path: "opt/litecode/config.json", RequiredIf: "etc/litecode-configured"},
	{Path: "root/.litecode"},
	{Path: "root/.wechatbot"},
	{Path: "etc/litecode-configured"},
}

func mkTarGz(t *testing.T, files map[string]string) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	tw := tar.NewWriter(zw)
	for name, body := range files {
		if strings.HasSuffix(name, "/") {
			tw.WriteHeader(&tar.Header{Name: name, Typeflag: tar.TypeDir, Mode: 0o755})
			continue
		}
		tw.WriteHeader(&tar.Header{Name: name, Typeflag: tar.TypeReg, Mode: 0o644, Size: int64(len(body))})
		tw.Write([]byte(body))
	}
	tw.Close()
	zw.Close()
	return buf.Bytes()
}

func TestProbeScriptRunsInBash(t *testing.T) {
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("no bash")
	}
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, "a/dir/sub"), 0o755)
	os.WriteFile(filepath.Join(root, "a/dir/sub/f"), []byte("12345"), 0o644)
	os.WriteFile(filepath.Join(root, "a/file"), []byte("xy"), 0o644)
	rel := strings.TrimPrefix(root, "/")
	sp := []Spec{{Path: rel + "/a/dir"}, {Path: rel + "/a/file"}, {Path: rel + "/a/none"}}
	script, err := ProbeScript(sp)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("bash", "-s")
	cmd.Stdin = strings.NewReader(script)
	out, err := cmd.Output()
	if err != nil {
		t.Fatal(err)
	}
	p, err := ParseProbe(string(out), sp)
	if err != nil {
		t.Fatalf("%v\n%s", err, out)
	}
	if len(p.Present) != 2 || len(p.Missing) != 1 || p.Bytes != 7 {
		t.Fatalf("unexpected probe %+v\n%s", p, out)
	}
}

func TestProbeScriptRejectsUnsafePath(t *testing.T) {
	for _, bad := range []string{"/abs", "a b", "a/../b", "a;rm"} {
		if _, err := ProbeScript([]Spec{{Path: bad}}); err == nil {
			t.Fatalf("%q accepted", bad)
		}
	}
}

func TestParseProbe(t *testing.T) {
	ok := "OK 10 opt/litecode/config.json\nOK 5 root/.litecode\nMISSING root/.wechatbot\nOK 20 etc/litecode-configured\n"
	p, err := ParseProbe(ok, specs)
	if err != nil || p.Bytes != 35 || len(p.Missing) != 1 || p.Missing[0] != "root/.wechatbot" {
		t.Fatalf("p=%+v err=%v", p, err)
	}
	cases := map[string]string{
		"读取": strings.Replace(ok, "OK 5 root/.litecode", "ERR root/.litecode", 1),
		"必需": strings.Replace(ok, "OK 10 opt/litecode/config.json", "MISSING opt/litecode/config.json", 1),
		"没有": strings.Replace(ok, "OK 5 root/.litecode\n", "", 1),
	}
	for want, out := range cases {
		if _, err := ParseProbe(out, specs); err == nil || !strings.Contains(err.Error(), want) {
			t.Fatalf("want error containing %q, got %v", want, err)
		}
	}
	// 未配置过: config.json 缺失允许跳过
	unconf := "MISSING opt/litecode/config.json\nOK 5 root/.litecode\nMISSING root/.wechatbot\nMISSING etc/litecode-configured\n"
	if _, err := ParseProbe(unconf, specs); err != nil {
		t.Fatal(err)
	}
}

func TestVerifyBackup(t *testing.T) {
	present := []string{"opt/litecode/config.json", "root/.litecode"}
	good := mkTarGz(t, map[string]string{"opt/litecode/config.json": "0123456789", "root/.litecode/": "", "root/.litecode/m.json": "abcde"})
	if st, err := VerifyBackup(bytes.NewReader(good), present, 15); err != nil || st.Bytes != 15 {
		t.Fatalf("st=%+v err=%v", st, err)
	}
	if _, err := VerifyBackup(bytes.NewReader(good[:len(good)/2]), present, 15); err == nil {
		t.Fatal("truncated archive accepted")
	}
	if _, err := VerifyBackup(bytes.NewReader(good), append(present, "root/.wechatbot"), 15); err == nil {
		t.Fatal("missing path accepted")
	}
	if _, err := VerifyBackup(bytes.NewReader(good), present, 1000); err == nil {
		t.Fatal("too small accepted")
	}
	if _, err := VerifyBackup(strings.NewReader("not gzip"), present, 0); err == nil {
		t.Fatal("garbage accepted")
	}
}

// fakeDistro 模拟子系统: data 是"子系统里的数据", 升级 = 清空 data 换版本
type fakeDistro struct {
	t          *testing.T
	exists     bool
	ver        string
	data       map[string]string
	calls      []string
	failImport bool
	failUnreg  bool
	failRest   bool
	badBackup  bool
}

func (f *fakeDistro) env() Env {
	return Env{
		Log:           func(string, ...any) {},
		Say:           func(int, string, ...any) {},
		DistroExists:  func() bool { return f.exists },
		RootfsVersion: func() string { return f.ver },
		Stop:          func() { f.calls = append(f.calls, "stop") },
		RunProbe: func(string) (string, error) {
			var b strings.Builder
			for _, s := range specs {
				if v, ok := f.data[s.Path]; ok {
					b.WriteString("OK " + strconv.Itoa(len(v)) + " " + s.Path + "\n")
				} else {
					b.WriteString("MISSING " + s.Path + "\n")
				}
			}
			return b.String(), nil
		},
		Backup: func(dst string, paths []string) error {
			f.calls = append(f.calls, "backup")
			m := map[string]string{}
			for _, p := range paths {
				m[p] = f.data[p]
			}
			b := mkTarGz(f.t, m)
			if f.badBackup {
				b = b[:len(b)/2]
			}
			return os.WriteFile(dst, b, 0o644)
		},
		Unregister: func() error {
			f.calls = append(f.calls, "unregister")
			if f.failUnreg {
				return errors.New("unreg boom")
			}
			f.exists, f.data, f.ver = false, nil, ""
			return nil
		},
		Import: func() error {
			f.calls = append(f.calls, "import")
			if f.failImport {
				return errors.New("import boom")
			}
			f.exists, f.ver, f.data = true, "new", map[string]string{}
			return nil
		},
		Restore: func(src string) error {
			f.calls = append(f.calls, "restore")
			if f.failRest {
				return errors.New("restore boom")
			}
			b, err := os.ReadFile(src)
			if err != nil {
				return err
			}
			zr, _ := gzip.NewReader(bytes.NewReader(b))
			tr := tar.NewReader(zr)
			for {
				h, err := tr.Next()
				if err != nil {
					break
				}
				var buf bytes.Buffer
				buf.ReadFrom(tr)
				f.data[h.Name] = buf.String()
			}
			return nil
		},
	}
}

func newFake(t *testing.T) *fakeDistro {
	return &fakeDistro{t: t, exists: true, ver: "old", data: map[string]string{
		"opt/litecode/config.json": `{"a":1}`, "root/.litecode": "memories", "etc/litecode-configured": "2026"}}
}

func backups(dir string) []string {
	m, _ := filepath.Glob(filepath.Join(dir, "upgrade-backup-*"))
	return m
}

func TestUpgradeHappyPathKeepsBackupUntilFinalize(t *testing.T) {
	dir := t.TempDir()
	f := newFake(t)
	u := &Upgrader{Env: f.env(), Dir: dir, Specs: specs, Payload: "new"}
	p, err := u.Prepare()
	if err != nil || p == nil || p.Stage != StageRestored {
		t.Fatalf("p=%+v err=%v", p, err)
	}
	if f.data["opt/litecode/config.json"] != `{"a":1}` || f.data["root/.litecode"] != "memories" {
		t.Fatalf("data not restored: %v", f.data)
	}
	if len(backups(dir)) != 1 {
		t.Fatal("backup must survive until health check passes")
	}
	u.Finalize(p)
	if len(backups(dir)) != 0 || fileExists(u.PendingPath()) {
		t.Fatal("finalize did not clean up")
	}
}

func TestUpgradeBadBackupStopsBeforeAnyChange(t *testing.T) {
	f := newFake(t)
	f.badBackup = true
	u := &Upgrader{Env: f.env(), Dir: t.TempDir(), Specs: specs, Payload: "new"}
	if _, err := u.Prepare(); err == nil || !strings.Contains(err.Error(), "未做任何改动") {
		t.Fatalf("err=%v", err)
	}
	for _, c := range f.calls {
		if c == "unregister" {
			t.Fatal("unregistered after bad backup")
		}
	}
	if !f.exists || f.ver != "old" || len(backups(u.Dir)) != 0 {
		t.Fatal("distro changed or partial backup left")
	}
}

func TestUpgradeResumeAfterFailedImport(t *testing.T) {
	dir := t.TempDir()
	f := newFake(t)
	f.failImport = true
	u := &Upgrader{Env: f.env(), Dir: dir, Specs: specs, Payload: "new"}
	_, err := u.Prepare()
	if err == nil || !strings.Contains(err.Error(), "备份") {
		t.Fatalf("err=%v", err)
	}
	if len(backups(dir)) != 1 {
		t.Fatal("backup lost after failed import")
	}
	// 重跑安装: 子系统已不存在, 应导入并从备份恢复
	f.failImport = false
	u.Env = f.env()
	p, err := u.Prepare()
	if err != nil || p == nil || f.data["root/.litecode"] != "memories" {
		t.Fatalf("resume failed p=%+v err=%v data=%v", p, err, f.data)
	}
}

func TestUpgradeResumeAfterFailedRestore(t *testing.T) {
	dir := t.TempDir()
	f := newFake(t)
	f.failRest = true
	u := &Upgrader{Env: f.env(), Dir: dir, Specs: specs, Payload: "new"}
	if _, err := u.Prepare(); err == nil {
		t.Fatal("expected restore failure")
	}
	// 重跑: 子系统已是新版 (空数据), 版本相同也必须从备份恢复, 不能当作已升级
	f.failRest = false
	u.Env = f.env()
	p, err := u.Prepare()
	if err != nil || p == nil || f.data["opt/litecode/config.json"] != `{"a":1}` {
		t.Fatalf("p=%+v err=%v data=%v", p, err, f.data)
	}
	n := 0
	for _, c := range f.calls {
		if c == "import" {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("should not re-import when distro exists, calls=%v", f.calls)
	}
}

func TestUpgradeUnregisterFailedRedoes(t *testing.T) {
	dir := t.TempDir()
	f := newFake(t)
	f.failUnreg = true
	u := &Upgrader{Env: f.env(), Dir: dir, Specs: specs, Payload: "new"}
	if _, err := u.Prepare(); err == nil || !strings.Contains(err.Error(), "备份") {
		t.Fatalf("err=%v", err)
	}
	f.failUnreg = false
	u.Env = f.env()
	p, err := u.Prepare()
	if err != nil || p == nil || f.ver != "new" || f.data["root/.litecode"] != "memories" {
		t.Fatalf("p=%+v err=%v", p, err)
	}
	if len(backups(dir)) != 1 {
		t.Fatalf("stale backup not discarded: %v", backups(dir))
	}
}

func TestUpgradeMissingBackupRefuses(t *testing.T) {
	dir := t.TempDir()
	u := &Upgrader{Env: newFake(t).env(), Dir: dir, Specs: specs, Payload: "new"}
	SavePending(u.PendingPath(), &Pending{Backup: filepath.Join(dir, "gone.tar.gz"), From: "old", To: "new", Stage: StageReplacing})
	if _, err := u.Prepare(); err == nil || !strings.Contains(err.Error(), "备份文件不见了") {
		t.Fatalf("err=%v", err)
	}
}

func TestNoUpgradeWhenSameVersion(t *testing.T) {
	f := newFake(t)
	f.ver = "new"
	u := &Upgrader{Env: f.env(), Dir: t.TempDir(), Specs: specs, Payload: "new"}
	if p, err := u.Prepare(); p != nil || err != nil || len(f.calls) != 0 {
		t.Fatalf("p=%v err=%v calls=%v", p, err, f.calls)
	}
}

// ── 代码更新 ──

type fakeCode struct {
	applyCode, rollCode int
	applyErr, rollErr   bool
	starts              []bool // 每次 Start 的结果
	ver                 string
	applied, rolled     bool
	n                   int
}

func (f *fakeCode) ops(newVer string) CodeOps {
	return CodeOps{
		Log:  func(string, ...any) {},
		Stop: func() {},
		Apply: func() (string, int, error) {
			if f.applyErr {
				return "APPLY_FAILED: x", f.applyCode, errors.New("exit")
			}
			f.applied, f.ver = true, newVer
			return "UPDATED", 0, nil
		},
		Rollback: func() (string, int, error) {
			if f.rollErr {
				return "ROLLBACK_FAILED: y", f.rollCode, errors.New("exit")
			}
			f.rolled, f.ver = true, "1.0"
			return "ROLLED_BACK 1.0", 0, nil
		},
		Start: func() bool {
			r := f.starts[f.n]
			f.n++
			return r
		},
		Version: func() string { return f.ver },
	}
}

func TestCodeUpdateOutcomes(t *testing.T) {
	cases := []struct {
		name    string
		f       fakeCode
		level   Level
		running bool
		want    string
	}{
		{"ok", fakeCode{ver: "1.0", starts: []bool{true}}, Info, true, "已更新到 2.0"},
		{"rollback ok", fakeCode{ver: "1.0", starts: []bool{false, true}}, Warn, true, "已回滚到 1.0，服务已恢复正常运行"},
		{"rollback but still down", fakeCode{ver: "1.0", starts: []bool{false, false}}, Error, false, "仍没有启动成功"},
		{"rollback script failed", fakeCode{ver: "1.0", rollErr: true, rollCode: 4, starts: []bool{false}}, Error, false, "自动回滚也失败"},
		{"apply unchanged running", fakeCode{ver: "1.0", applyErr: true, applyCode: 2, starts: []bool{true}}, Warn, true, "当前仍是 1.0，服务正常运行"},
		{"apply restored but down", fakeCode{ver: "1.0", applyErr: true, applyCode: 3, starts: []bool{false}}, Error, false, "没有启动成功"},
		{"apply broken", fakeCode{ver: "?", applyErr: true, applyCode: 4, starts: []bool{false}}, Error, false, "/opt/litecode.prev"},
	}
	for _, c := range cases {
		f := c.f
		r := RunCodeUpdate(f.ops("2.0"), "2.0")
		if r.Level != c.level || r.Running != c.running || !strings.Contains(r.Message, c.want) {
			t.Errorf("%s: got %+v", c.name, r)
		}
		if f.n != len(f.starts) {
			t.Errorf("%s: Start called %d times, want %d", c.name, f.n, len(f.starts))
		}
	}
}
