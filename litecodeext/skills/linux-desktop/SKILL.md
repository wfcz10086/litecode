---
name: linux-desktop
description: >
  本容器 Linux 图形桌面 (Xvfb :99 + fluxbox + google-chrome, 用户在 Web UI /vnc/ 实时看) 的操作:
  desktop_exec 起程序、desktop_screenshot 截图、desktop_key/desktop_type/desktop_click;
  打开网页/PDF/K 线 (tradingview 周期参数), chrome 必带参数, 桌面任务的 ✓ 回复格式。
  远程 Windows 电脑不用本技能, 用 remote-desktop-control。

## Keywords
桌面 desktop 远程桌面 vnc novnc xvfb 截图 截屏 屏幕 打开浏览器 打开网页 chrome chromium
tradingview K线 k线 周线 日线 合约 行情图 desktop_exec desktop_screenshot desktop_key desktop_type desktop_click
---

# 本容器 Linux 桌面操作

> 2026-10-21 从会话模板 SOUL.md 移出 (原文每轮都发, 只有桌面任务用得到)。内容未删减规则。

容器内 Xvfb (:99) + fluxbox + google-chrome 已就绪。用户登录 Web UI 后能在远程桌面 (`/vnc/`) 实时看到桌面。
这些工具属于 `desktop` 工具组: 不在工具列表里时先 `enable_tools(groups=["desktop"])`。

## 工具

- `desktop_exec(cmd, bg=true)` — 在桌面跑 shell。浏览器/文件管理器这种长进程 **必须 bg=true**
- `desktop_screenshot()` — 截图，返回 PNG 路径，前端会自动渲染
- `desktop_key(keys)` — 按键，xdotool 语法 (`ctrl+t` / `Return` / `alt+F4`)
- `desktop_type(text)` — 键入文本（英文/URL/数字 OK，中文目前无输入法不可靠）
- `desktop_click(x, y)` — **坐标不要靠想**: 除非看了 `desktop_screenshot()` 并能读出坐标，不要凭空点。
  优先用 **快捷键**（`ctrl+l` 聚焦地址栏 / `ctrl+t` 新标签 / `ctrl+w` 关标签）和 **URL 直跳**。

## 浏览器注意事项

- 容器里浏览器命令是 **`google-chrome`** (不是 chromium —— Ubuntu 24.04 把 apt 的 chromium/firefox 全变 snap shim, 容器跑不起来)
- 容器内是 root 用户 + 无用户命名空间, **必须加 `--no-sandbox`** 否则秒退
- **必须加 `--no-first-run --no-default-browser-check`** 否则首次跑会弹"欢迎使用/设为默认浏览器"对话框挡住页面
- **完整推荐参数模板**（缺一个就可能挡页面）:
  ```
  google-chrome --no-sandbox --no-first-run --no-default-browser-check \
    --disable-default-apps --disable-dev-shm-usage \
    --disable-features=Translate,TranslateUI \
    --window-size=1280,720 '<URL>'
  ```
- desktop_exec 起 chrome 后, **必须 sleep 6-10 秒**再 desktop_screenshot, 让 K 线/JS 渲染完

## 回复格式（强制 · 桌面任务必须遵守）

调完 `desktop_screenshot` 后，**最终消息**必须以 `✓ ` 开头，**首行 ≤ 30 字**：

```
✓ 已<动作>。<一句话状态>。
[可选: 数字/补充信息]
```

✅ 正确：
```
✓ 已在桌面打开 ETH/USDT 永续合约，截图已回。
当前 2127.04 USDT，24h +0.76%。
```

❌ 错误（实际发生过 — 把工具结果拼一长段，没简洁 ✓ 头）：
```
桌面 Xvfb 环境就绪，网页版数据已抓取到：
ETH/USDT 永续合约 (Binance)
- 1月: -7.22%
...
```
这种"小作文式"汇报**禁止**。详细数据放第二段，不能淹没 `✓ ` 头。
原因：用户要的是"事做完了"+ 截图，详细数字工具调用折叠区已有，不用复述。

## 行情站映射规则（重要）

- 加密货币合约/现货 K 线：**走 tradingview.com**，不要走 `binance.com`（境内 IP 被 AWS WAF 拦）
- 只要数据不要图（价格/24h/资金费率/持仓量）→ 直接 `web_fetch` binance API JSON，不开浏览器

**Tradingview URL 模板**（用户提到时间周期一定要带 `interval=` 参数）：

| 用户说 | URL |
|---|---|
| ETH 现货 (默认日线) | `tradingview.com/symbols/ETHUSDT/?exchange=BINANCE` |
| ETH 永续 (默认日线) | `tradingview.com/symbols/ETHUSDT.P/?exchange=BINANCE` |
| ETH 现货 **周线** | `tradingview.com/chart/?symbol=BINANCE%3AETHUSDT&interval=W` |
| ETH 永续 **周线** | `tradingview.com/chart/?symbol=BINANCE%3AETHUSDT.P&interval=W` |
| BTC / SOL / 其他 | 把 `ETH` 换掉即可 |
| 美股 NVDA / A 股 / 黄金 | `tradingview.com/symbols/<TICKER>` 或 `tradingview.com/chart/?symbol=<EXCHANGE>%3A<TICKER>&interval=<I>` |

**interval 参数**: 1 分钟=`1` / 5 分钟=`5` / 15 分钟=`15` / 1 小时=`60` / 4 小时=`240` / 日线=`D` / **周线=`W`** / 月线=`M`

**关键判断**：用户提到具体时间周期时，**优先用 `/chart/` URL + interval 参数直跳**，不要打开 `/symbols/` 默认页再让用户自己点
（AI 无法点击图表上的时间切换按钮，靠快捷键也不通）。

典型对话：
```
用户: 打开 ETH 现货看周线
你:   desktop_exec(cmd="google-chrome --no-sandbox --no-first-run \
        --no-default-browser-check --disable-default-apps \
        --disable-features=Translate,TranslateUI \
        --window-size=1280,720 \
        'https://www.tradingview.com/chart/?symbol=BINANCE%3AETHUSDT&interval=W'")
     等 8 秒 (K 线 + 周数据加载较慢)
     desktop_screenshot()
     回复: ✓ 已打开 ETH/USDT 现货周线，截图回。
```

## 速查表

| 指令类型 | 怎么做 |
|---|---|
| 看 PDF | `desktop_exec("google-chrome --no-sandbox '/path/to/file.pdf'")` 或 `xdg-open /path/file.pdf` |
| 打开本地文件夹 | `desktop_exec("xterm -e 'cd /path && bash'")`（容器没装 GUI 文件管理器） |
| 看桌面在干啥 | `desktop_screenshot()` |
| 现在桌面有几个窗口 | `desktop_screenshot()` 看截图 + `list_bg` 看 PID |
| 关掉桌面上起的程序 | `list_bg` → 拿 pid → `kill_bg(pid=<pid>)`（desktop_exec 起的进程也进 bg pool） |
