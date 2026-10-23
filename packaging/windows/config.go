package main

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image"
	"image/png"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ncruces/zenity"
)

func randHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// ensureConfig: 从子系统里取默认配置 (公开版 config.example.json), 填登录密码 / 内部 token / 模型,
// 写回 /opt/litecode/config.json 并打标记 /etc/litecode-configured (代码更新、系统包升级都不覆盖)。
func ensureConfig() error {
	raw := distroFile("/opt/litecode/config.default.json")
	var cfg map[string]any
	if err := json.Unmarshal([]byte(raw), &cfg); err != nil {
		return fmt.Errorf("读取默认配置失败: %v", err)
	}
	pw, err := askPassword()
	if err != nil {
		return err
	}
	sub(cfg, "web_ui", "auth")["password"] = pw
	sub(cfg, "web_ui", "auth")["enabled"] = true
	sub(cfg, "server")["token"] = randHex(24)
	// 桌面版不带微信 / 企业微信机器人 (否则启动后一直等扫码)
	sub(cfg, "wechat")["enabled"] = false
	sub(cfg, "wecom")["enabled"] = false
	// 桌面版只保留用户自己的模型: 示例配置里那十几个模型 key 都是占位符, 用户用不了, 设置页里一大串
	// 反而找不到自己的 (2026-10-21 实机反馈)。没填模型就清空列表, 到「设置」里添加。
	if applyPreset(cfg) {
		// 定制包 (如 mycompany-litecode): 模型 / 识图模型 / 能力接入 / 搜索按预置配置, 不再问模型
	} else if m := askModel(); m != nil {
		m["label"] = fmt.Sprintf("%s（我的模型）", m["id"])
		cfg["model"] = m
		cfg["default_model_id"] = m["id"]
		cfg["models"] = []any{m}
		if v, _ := m["supports_vision"].(bool); v {
			cfg["vision_fallback_id"] = m["id"] // 主模型能看图, 识图就用它自己
		} else {
			delete(cfg, "vision_fallback_id") // 示例配置里的识图模型 key 是占位符, 用不了
		}
	} else {
		cfg["models"] = []any{}
	}
	for k := range cfg {
		if strings.HasPrefix(k, "_") { // 内部备注 / 备份字段
			delete(cfg, k)
		}
	}
	b, _ := json.MarshalIndent(cfg, "", "  ")
	if err := inDistroIO(time.Minute, bytes.NewReader(b), io.Discard,
		"sh", "-c", "umask 077; cat > /opt/litecode/config.json && date -Is > /etc/litecode-configured"); err != nil {
		return fmt.Errorf("写入配置失败: %v", err)
	}
	return nil
}

// presetKeys: 定制安装包 payload\preset-config.json 里允许带进来的配置段 (登录密码 / server.token 照常本机生成)
var presetKeys = []string{"model", "models", "default_model_id", "vision_fallback_id", "vision", "providers",
	"search", "tts", "kb"}

// applyPreset: payload\preset-config.json 存在就合并进 cfg, 返回 true; 不存在或解析失败返回 false (走正常询问)。
func applyPreset(cfg map[string]any) bool {
	raw, err := os.ReadFile(filepath.Join(dataDir(), "payload", "preset-config.json"))
	if err != nil {
		return false
	}
	var pre map[string]any
	if err := json.Unmarshal(raw, &pre); err != nil {
		logf("预置配置解析失败, 忽略: %v", err)
		return false
	}
	n := 0
	for _, k := range presetKeys {
		if v, ok := pre[k]; ok {
			cfg[k] = v
			n++
		}
	}
	logf("已应用预置配置 (%d 项)", n)
	return n > 0
}

func sub(m map[string]any, keys ...string) map[string]any {
	cur := m
	for _, k := range keys {
		nx, ok := cur[k].(map[string]any)
		if !ok {
			nx = map[string]any{}
			cur[k] = nx
		}
		cur = nx
	}
	return cur
}

// askPassword: 桌面版默认密码 admin (只对本机 127.0.0.1 开放), 不再弹窗; 首次打开会提示到「设置 → 修改密码」改掉。
// 命令行 --password 可指定。
func askPassword() (string, error) {
	if opt.Password != "" {
		return opt.Password, nil
	}
	return "admin", nil
}

// askModel: OpenAI 兼容接口 (DeepSeek / 通义 / Kimi / 自建 vLLM 都行)。任意一项留空 = 以后在 LiteCode 设置里配。
func askModel() map[string]any {
	if opt.Unattended || opt.ModelURL != "" {
		if opt.ModelURL == "" || opt.ModelName == "" {
			return nil
		}
		return modelEntry(opt.ModelURL, opt.ModelName, opt.ModelKey)
	}
	url, err := zenity.Entry("模型 API 地址（OpenAI 兼容，例如 https://api.deepseek.com/v1）\n留空则以后在 LiteCode「设置」里配置：",
		zenity.Title("LiteCode 首次设置 · 模型"), zenity.EntryText("https://api.deepseek.com/v1"))
	if err != nil || strings.TrimSpace(url) == "" {
		return nil
	}
	name, err := zenity.Entry("模型名称（接口里的 model 字段，例如 deepseek-chat）：",
		zenity.Title("LiteCode 首次设置 · 模型"), zenity.EntryText("deepseek-chat"))
	if err != nil || strings.TrimSpace(name) == "" {
		return nil
	}
	key, err := zenity.Entry("API Key：", zenity.Title("LiteCode 首次设置 · 模型"), zenity.HideText())
	if err != nil {
		return nil
	}
	return modelEntry(url, name, key)
}

func modelEntry(url, name, key string) map[string]any {
	name = strings.TrimSpace(name)
	return map[string]any{
		"id": name, "provider": "openai-compatible", "deployment": "api",
		"served_model_name": name, "backend_type": "openai",
		"backend_url": strings.TrimRight(strings.TrimSpace(url), "/"), "api_key": keyOrEmpty(key),
		"api_format": "openai-chat", "context_window": 128000, "max_tokens": 8192,
		"enable_thinking": false, "supports_vision": probeVision(url, name, key),
	}
}

// probeVision 发一张小图试一下, 模型能看图就标 supports_vision (识图/OCR/截图核对都靠它)。
// 只等 20 秒; 失败一律当不支持, 以后可在「设置」里改。
func probeVision(url, name, key string) bool {
	img := image.NewRGBA(image.Rect(0, 0, 64, 32))
	for i := range img.Pix {
		img.Pix[i] = 255
	}
	var buf bytes.Buffer
	png.Encode(&buf, img)
	body, _ := json.Marshal(map[string]any{
		"model": strings.TrimSpace(name), "max_tokens": 8,
		"messages": []any{map[string]any{"role": "user", "content": []any{
			map[string]any{"type": "text", "text": "图片是什么颜色？一个词"},
			map[string]any{"type": "image_url", "image_url": map[string]any{
				"url": "data:image/png;base64," + base64.StdEncoding.EncodeToString(buf.Bytes())}},
		}}},
	})
	req, err := http.NewRequest("POST", strings.TrimRight(strings.TrimSpace(url), "/")+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return false
	}
	req.Header.Set("Content-Type", "application/json")
	if k := strings.TrimSpace(key); k != "" {
		req.Header.Set("Authorization", "Bearer "+k)
	}
	resp, err := (&http.Client{Timeout: 20 * time.Second}).Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	logf("识图探测 %s: HTTP %d", name, resp.StatusCode)
	return resp.StatusCode == 200
}

// keyOrEmpty: 用户没填 API Key (本地 vLLM/Ollama 常见) 写 "EMPTY", 不留空串 (留空会发出非法的 "Bearer " 请求头)
func keyOrEmpty(k string) string {
	if k = strings.TrimSpace(k); k != "" {
		return k
	}
	return "EMPTY"
}
