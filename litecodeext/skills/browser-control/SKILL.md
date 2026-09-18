# Browser Control Skill

> 通过 CDP (Chrome DevTools Protocol) 控制容器内持久 Chrome 浏览器。
> Chrome 已在 DISPLAY :99 上运行，VNC 可视化端口 18800，CDP 端口 9222。

## 架构

```
用户 ──> Web UI (18790) ──> LiteCode Server (18789) ──> Agent
                                                          │
                                                          ▼
                                                   Playwright CDP
                                                          │
                                                          ▼
                                                  Chrome (:99, CDP:9222)
                                                          │
                                                          ▼
                                                 VNC ──> noVNC (18800)
```

## 关键点: 不要用 playwright.launch()

容器内已有一个持久运行的 Chrome 实例（entrypoint.sh 启动）。
**必须用 `connect_over_cdp` 连接已有实例，不能 launch 新的**，否则会和已有实例冲突。

## 连接方式

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    # 连接到已有的 Chrome 实例
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
    
    # 获取默认上下文（保持 cookies/session）
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    
    # 获取已有页面或新建
    page = context.pages[0] if context.pages else context.new_page()
    
    # 操作
    page.goto("https://example.com")
    page.screenshot(path="/tmp/openclaw_workspace/screenshot.png")
    
    # 不要 close browser - 它是持久的
    # browser.close()  <-- 禁止
```

## 异步版本

```python
from playwright.async_api import async_playwright

async def browse(url):
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        title = await page.title()
        content = await page.content()
        await page.screenshot(path="/tmp/openclaw_workspace/screenshot.png")
        return title, content
```

## 常用操作

### 截图
```python
page.screenshot(path="/tmp/openclaw_workspace/screenshot.png", full_page=True)
```

### 获取页面文本
```python
text = page.inner_text("body")
```

### 填写表单
```python
page.fill("input[name='q']", "search query")
page.click("button[type='submit']")
page.wait_for_load_state("networkidle")
```

### 等待元素
```python
page.wait_for_selector(".result", timeout=10000)
```

### 多标签页
```python
# 新开标签页
new_page = context.new_page()
new_page.goto("https://another-site.com")

# 切换回第一个
page = context.pages[0]
page.bring_to_front()
```

### 下载文件
```python
with page.expect_download() as dl_info:
    page.click("a.download-link")
download = dl_info.value
download.save_as(f"/tmp/openclaw_workspace/downloads/{download.suggested_filename}")
```

## 注意事项

1. **不要 browser.close()** - Chrome 是持久的，关了 VNC 就黑屏
2. **不要 playwright.launch()** - 会启动新实例，和现有的冲突
3. **截图存到 workspace** - `/tmp/openclaw_workspace/` 下的文件用户可以在 Web UI 预览
4. **超时处理** - 网络请求加 timeout，避免 agent 卡死
5. **Chrome profile 在 `/tmp/openclaw_workspace/.chrome-profile`** - cookies/登录状态跨 session 保持
6. **VNC 查看**: 用户通过 Web UI 的 VNC tab 或直接访问 `http://<host>:18800/vnc.html` 查看浏览器画面

## 健康检查

```bash
# 检查 Chrome CDP 是否可用
curl -s http://127.0.0.1:9222/json/version

# 检查 VNC
curl -s http://127.0.0.1:18800/ | head -1
```

## 故障排除

```bash
# Chrome 挂了 - watchdog 会自动重启，也可以手动:
pkill -f chromium
# 等 30s watchdog 会重启，或手动:
chromium-browser --no-sandbox --disable-gpu --user-data-dir=/tmp/openclaw_workspace/.chrome-profile --remote-debugging-port=9222 --window-size=1280,720 &

# Xvfb 挂了
Xvfb :99 -screen 0 1280x720x24 -ac &
x11vnc -display :99 -forever -shared -rfbport 5999 -nopw -quiet &
```
