package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/ncruces/zenity"
	"golang.org/x/sys/windows"
)

// 模型包: 本地纯 CPU 语音 (Kokoro) + 知识库向量模型 (text2vec ONNX), 约 860MB 且几乎压不动。
// 2026-10-22 放进系统包后 rootfs 2.44GB, 超过 NSIS 单个安装包 2GB 上限 (makensis mmap 失败), 所以单独发布:
// latest.json 的 models_file / models_sha256 / models_size; 托盘启动后后台下载 (断点续传 + sha256 校验),
// 流式解到子系统 /var/lib/litecode/models (升级保留区, /opt/litecode-models 是指向它的软链)。
// 下载完成前: 配音自动走 edge-tts, 知识库提示"模型下载中"; 离线机器可把模型包手动放到 %LOCALAPPDATA%\LiteCode\ 再重启托盘。

const modelsDir = "/var/lib/litecode/models"

var modelsMu sync.Mutex

func modelsMarker(sha string) string {
	if len(sha) > 12 {
		sha = sha[:12]
	}
	return modelsDir + "/.ok-" + sha
}

// ensureModels: 子系统里没有对应版本的模型就下载导入。幂等; 同一时间只跑一个; 失败只记日志, 下次启动再试。
func ensureModels() {
	if !modelsMu.TryLock() {
		return
	}
	defer modelsMu.Unlock()
	rel, err := fetchRelease()
	if err != nil {
		rel = localModelsPack() // 离线: 用户手动放进数据目录的模型包 (文件名带 sha 前 12 位, 实际 sha 要对得上)
		err = nil
	}
	if rel == nil || rel.ModelsFile == "" || rel.ModelsSHA256 == "" {
		logf("模型包: 跳过 (%v, file=%q)", err, func() string {
			if rel == nil {
				return ""
			}
			return rel.ModelsFile
		}())
		return
	}
	marker := modelsMarker(rel.ModelsSHA256)
	if _, err := inDistro(30*time.Second, "test", "-f", marker); err == nil {
		return // 已就绪
	}
	dst := filepath.Join(dataDir(), rel.ModelsFile)
	if ok, _ := sha256Is(dst, rel.ModelsSHA256); !ok {
		if rel.Version == "local" {
			logf("模型包: 本地文件 %s 校验不符, 不导入", rel.ModelsFile)
			return
		}
		if free, ok := freeBytes(dataDir()); ok && free < uint64(rel.ModelsSize)*3 {
			logf("模型包: 磁盘剩余 %d MB 不足 (需要约 %d MB), 暂不下载", free>>20, rel.ModelsSize*3>>20)
			return
		}
		logf("模型包: 开始下载 %s (%d MB)", rel.ModelsFile, rel.ModelsSize>>20)
		go zenity.Notify("正在后台下载本地语音和知识库模型 (约 "+fmt.Sprint(rel.ModelsSize>>20)+" MB), 期间配音会先用在线语音。",
			zenity.Title(appName))
		err := download(BaseURL+rel.ModelsFile, dst, rel.ModelsSHA256, func(done, total int64) {
			if total > 0 {
				setTip(fmt.Sprintf("LiteCode · 下载模型 %d%%", done*100/total))
			}
		})
		if err != nil {
			logf("模型包: 下载失败 (下次启动继续): %v", err)
			setTip(fmt.Sprintf("LiteCode %s · 运行中", codeVersion()))
			return
		}
	}
	f, err := os.Open(dst)
	if err != nil {
		logf("模型包: 打开失败: %v", err)
		return
	}
	defer f.Close()
	// 解到临时目录再整体替换, 半途失败不会留下残缺模型; 软链 /opt/litecode-models 由系统包提供, 这里兜底补上
	// 注意: wsl.exe -- 转发参数时会经一层 shell, 脚本里的 $变量 会在外层被展开成空 (2026-10-22 真机:
	// "mv: missing destination file operand after '.old'"), 所以全部写字面路径, 不用任何 $。
	d := modelsDir
	script := strings.Join([]string{
		"set -e", "rm -rf " + d + ".new", "mkdir -p " + d + ".new", "tar -x -C " + d + ".new",
		"rm -rf " + d + ".old", "if [ -d " + d + " ]; then mv " + d + " " + d + ".old; fi",
		"mv " + d + ".new " + d, "rm -rf " + d + ".old",
		"[ -e /opt/litecode-models ] || ln -s " + d + " /opt/litecode-models", "touch " + marker,
	}, "; ")
	if strings.Contains(script, "$") {
		logf("模型包: 内部错误, 导入脚本含 $")
		return
	}
	var out strings.Builder
	if err := inDistroIO(30*time.Minute, f, &out, "bash", "-c", script); err != nil {
		logf("模型包: 导入子系统失败: %v", err)
		return
	}
	f.Close()
	_ = os.Remove(dst) // 已导入, 删 Windows 侧副本省空间 (升级时模型目录在保留区, 不用重下)
	logf("模型包: 已就绪 %s", marker)
	setTip(fmt.Sprintf("LiteCode %s · 运行中", codeVersion()))
	go zenity.Notify("本地语音和知识库模型已就绪, 断网也能配音和查知识库。", zenity.Title(appName))
}

// freeBytes: 目录所在磁盘的可用空间 (拿不到时 ok=false, 调用方照常下载)
func freeBytes(dir string) (uint64, bool) {
	p, err := windows.UTF16PtrFromString(dir)
	if err != nil {
		return 0, false
	}
	var free, total, totalFree uint64
	if err := windows.GetDiskFreeSpaceEx(p, &free, &total, &totalFree); err != nil {
		return 0, false
	}
	return free, true
}

// localModelsPack: 数据目录里手动放的 litecode-models-<sha12>.tar; 算出整文件 sha256 且前 12 位与文件名一致才认。
func localModelsPack() *Release {
	ms, _ := filepath.Glob(filepath.Join(dataDir(), "litecode-models-*.tar"))
	for _, m := range ms {
		name := filepath.Base(m)
		want := strings.TrimSuffix(strings.TrimPrefix(name, "litecode-models-"), ".tar")
		if len(want) != 12 {
			continue
		}
		if _, got := sha256Is(m, ""); strings.HasPrefix(got, want) {
			st, _ := os.Stat(m)
			return &Release{Version: "local", ModelsFile: name, ModelsSHA256: got, ModelsSize: st.Size()}
		}
		logf("模型包: %s 的 sha256 与文件名不符, 忽略", name)
	}
	return nil
}
