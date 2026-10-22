// Package upgrade 是桌面版升级/回滚的纯逻辑 (不依赖 Windows API), 便于在 Linux 上单测:
//   - 系统包升级前的数据备份: 探测路径 → 打包 → 校验 (能完整列出、覆盖所有路径、大小合理)
//   - 升级进行中标记 (pending): 中途失败后重跑安装能接着从备份恢复, 不会把空数据当成已升级
//   - 代码更新的结果判定: 提示文字只依据实际状态 (服务是否真起来、当前版本号)
package upgrade

import (
	"archive/tar"
	"compress/gzip"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
)

// Spec 描述要随升级带走的数据路径 (相对 /, 不带前导斜杠)。
// RequiredIf 非空时: 该标记路径存在, 本路径就必须存在 (例如已完成配置 → config.json 必须在)。
type Spec struct {
	Path       string
	RequiredIf string
}

var safePath = regexp.MustCompile(`^[A-Za-z0-9._/-]+$`)

// ProbeScript 生成在子系统里用 bash 执行的探测脚本 (经标准输入传入, 不拼命令行):
// 每个路径输出一行 "OK <文件字节数合计> <路径>" / "MISSING <路径>" / "ERR <路径>"。
func ProbeScript(specs []Spec) (string, error) {
	var b strings.Builder
	b.WriteString("set -u -o pipefail\n")
	for _, s := range specs {
		if !safePath.MatchString(s.Path) || strings.HasPrefix(s.Path, "/") || strings.Contains(s.Path, "..") {
			return "", fmt.Errorf("不安全的数据路径: %q", s.Path)
		}
		fmt.Fprintf(&b, "p=%s\n", s.Path)
		b.WriteString(`if [ -e "/$p" ] || [ -L "/$p" ]; then
  if n=$(find "/$p" -type f -printf '%s\n' | awk '{s+=$1} END{print s+0}'); then echo "OK $n $p"; else echo "ERR $p"; fi
else echo "MISSING $p"; fi
`)
	}
	return b.String(), nil
}

// Probe 是探测结果: Present 为要打包的路径, Missing 为确实不存在且允许跳过的路径。
type Probe struct {
	Present []string
	Missing []string
	Bytes   int64 // Present 下普通文件大小合计 (用于校验备份大小是否合理)
}

// ParseProbe 解析探测输出。任何路径读取出错 / 未报告 / 必需路径缺失都返回错误 (备份不允许"忽略读取失败")。
func ParseProbe(out string, specs []Spec) (*Probe, error) {
	status := map[string]string{}
	sizes := map[string]int64{}
	for _, line := range strings.Split(strings.ReplaceAll(out, "\r", ""), "\n") {
		f := strings.Fields(line)
		switch {
		case len(f) == 3 && f[0] == "OK":
			n, err := strconv.ParseInt(f[1], 10, 64)
			if err != nil {
				return nil, fmt.Errorf("探测输出无法解析: %q", line)
			}
			status[f[2]], sizes[f[2]] = "OK", n
		case len(f) == 2 && (f[0] == "MISSING" || f[0] == "ERR"):
			status[f[1]] = f[0]
		}
	}
	p := &Probe{}
	for _, s := range specs {
		switch status[s.Path] {
		case "OK":
			p.Present = append(p.Present, s.Path)
			p.Bytes += sizes[s.Path]
		case "MISSING":
			if s.RequiredIf != "" && status[s.RequiredIf] == "OK" {
				return nil, fmt.Errorf("必需的数据 /%s 不存在 (已有 /%s), 停止升级以免丢数据", s.Path, s.RequiredIf)
			}
			p.Missing = append(p.Missing, s.Path)
		case "ERR":
			return nil, fmt.Errorf("读取 /%s 出错, 停止升级", s.Path)
		default:
			return nil, fmt.Errorf("探测结果里没有 /%s (探测脚本没有正常跑完)", s.Path)
		}
	}
	if len(p.Present) == 0 {
		return nil, fmt.Errorf("没有找到任何要备份的数据, 停止升级")
	}
	return p, nil
}

// Stats 是备份包校验结果。
type Stats struct {
	Entries int
	Bytes   int64 // 普通文件大小合计 (解压后)
}

// VerifyBackup 完整读一遍 tar.gz: 任何损坏/截断都会报错; 每个打包路径都要在包里出现;
// 文件大小合计不少于探测值的 95% (服务已停, 理应相等, 留一点余量给日志等小变动)。
func VerifyBackup(r io.Reader, present []string, expectBytes int64) (Stats, error) {
	var st Stats
	zr, err := gzip.NewReader(r)
	if err != nil {
		return st, fmt.Errorf("备份不是有效的 gzip: %v", err)
	}
	defer zr.Close()
	seen := map[string]bool{}
	tr := tar.NewReader(zr)
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return st, fmt.Errorf("备份包损坏 (第 %d 项之后): %v", st.Entries, err)
		}
		n, err := io.Copy(io.Discard, tr)
		if err != nil {
			return st, fmt.Errorf("备份包损坏 (%s): %v", h.Name, err)
		}
		st.Entries++
		if h.Typeflag == tar.TypeReg {
			st.Bytes += n
		}
		name := strings.TrimSuffix(strings.TrimPrefix(h.Name, "./"), "/")
		for _, p := range present {
			if name == p || strings.HasPrefix(name, p+"/") {
				seen[p] = true
			}
		}
	}
	for _, p := range present {
		if !seen[p] {
			return st, fmt.Errorf("备份包里缺少 /%s", p)
		}
	}
	if st.Bytes < expectBytes*95/100 {
		return st, fmt.Errorf("备份大小不对: 包内文件合计 %d 字节, 原数据 %d 字节", st.Bytes, expectBytes)
	}
	return st, nil
}
