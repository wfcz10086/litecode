---
name: webapp-testing
description: >
  网页应用验收测试: 做完或改完网页、前端页面、Web 应用后, 用 Playwright 模拟真人点击、输入、提交, 断言页面结果并截图留证。
  curl 或接口返回 200 不算前端验收。也用于复现前端 bug、检查控制台报错、手机尺寸布局。只是抓网页数据用 browser-automation。
aliases: ["前端验收", "playwright", "点一遍页面"]
---

# 网页应用验收 (Playwright 真人模拟)

**铁律: 前端改动的"能用了"只能由浏览器里真实的点击/输入结果证明。** `curl` 拿到 HTML、接口返回 200、单元测试通过, 都不能证明按钮绑上了事件、弹窗能关、刷新后数据还在。

## Keywords
验收 前端验收 页面验收 网页测试 前端测试 e2e 端到端 playwright 模拟点击 点一下试试 按钮没反应 页面空白 白屏 控制台报错 console 手机端布局 回归测试 交互测试 表单提交 webapp

## 两种工具, 按场景选

| 场景 | 用法 |
| --- | --- |
| 随手点几下看看、复现一个交互 bug | `browser_snapshot(url)` 拿到带编号的元素 → `browser_act(action="click"/"fill"/"press", ref=编号)` → 看返回的新快照 → 完事 `browser_act(action="close")` |
| 正式验收、要可重复跑、要断言 | 写 Python Playwright 脚本, `execute_shell` 跑, 输出 PASS/FAIL + 截图 |

两者都要**用完立刻关浏览器**, 不要留多个浏览器进程。

## 标准流程

### 1. 把服务起起来
```
execute_shell(command="cd /path/app && python3 -m http.server 8000", background=true,
              health_url="http://127.0.0.1:8000/")
```
后端服务同理 (uvicorn / npm run dev)。用 `health_url` 等就绪, 起不来就 `list_bg` 找日志路径再 `tail_log` 看原因。测完 `kill_bg` 清理。
**端口先确认没被占用**: 被占用时你的服务起不来, 测试却会连到别人的服务上, 报出莫名其妙的 404。起完先 `web_fetch` 一下首页, 确认返回的是你的页面。

### 2. 先列验收清单
把需求翻成"用户做了 X → 应该看到 Y"的条目, 每条对应一个用例。至少覆盖: 主流程走通、一个错误输入、刷新后状态是否保留 (如果需求涉及持久化)。

### 3. 写验收脚本 (模板)
```python
# /tmp/litecode_workspace/ui_check.py
from playwright.sync_api import sync_playwright, expect
import sys
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
errors, results = [], []

def case(name, fn, page):
    try:
        fn(page); results.append((name, "PASS"))
    except Exception as e:
        results.append((name, f"FAIL: {str(e)[:200]}"))
    page.screenshot(path=f"/tmp/litecode_workspace/shot_{len(results):02d}.png", full_page=True)

def add_item(page):
    page.get_by_placeholder("输入待办").fill("买牛奶")      # 用户看得见的定位方式优先
    page.get_by_role("button", name="添加").click()       # 真点击, 不用 evaluate 调函数
    expect(page.get_by_text("买牛奶")).to_be_visible(timeout=5000)

def survives_reload(page):
    page.reload()
    expect(page.get_by_text("买牛奶")).to_be_visible(timeout=5000)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(BASE, wait_until="networkidle")
    case("添加一条", add_item, page)
    case("刷新后还在", survives_reload, page)
    browser.close()

for n, r in results: print(f"[{n}] {r}")
print("console errors:", errors or "无")
sys.exit(0 if all(r == "PASS" for _, r in results) and not errors else 1)
```
运行: `execute_shell(command="python3 /tmp/litecode_workspace/ui_check.py http://127.0.0.1:8000", timeout=300)`

要点:
- 定位优先 `get_by_role` / `get_by_text` / `get_by_placeholder` / `get_by_label`, 其次稳定的 `#id`; 别用 nth-child 这种一改版就断的选择器。
- 等待用 `expect(...).to_be_visible(timeout=...)` 或 `wait_for_selector`, 不要 `time.sleep` 硬等。
- **不要用 `page.evaluate` 直接调前端函数来"代替点击"**, 那绕过了事件绑定, 测不出真问题。需要给 evaluate 传值时用 `page.evaluate(js, arg)`, 不要拼 f-string。
- 控制台 error 和 pageerror 也算失败, 除非能说明是无关的第三方告警。
- 需要手机布局时再跑一遍 `viewport={"width": 390, "height": 844}`。

### 4. 看截图
截图不是摆设: `vision_ocr(paths=[截图1, 截图2, ...], prompt="这是按顺序的验收截图, 检查有没有错位、遮挡、空白、乱码、按钮看不见")`, 最多 8 张一批。改版前后对比也把两张放进 paths。

### 5. 汇报
贴脚本输出的 PASS/FAIL 原文 + 截图路径。有 FAIL 就说 FAIL, 并附 `tail_log` 后端日志或控制台错误; 不允许"应该可以了"。

## 不要做
- 在用户的生产数据上随手增删: 测试只删自己刚创建的数据, 用带随机后缀的名字方便识别。
- 浏览器开着不关、一次开多个; 每个脚本结尾 `browser.close()`, 交互式用完 `browser_act(action="close")`。
- 浏览器缺失时报 `Executable doesn't exist`: 跑 `python3 -m playwright install chromium` 再试。
