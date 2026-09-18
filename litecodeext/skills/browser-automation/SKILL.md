---
name: browser-automation
description: >
  浏览器自动化控制。操作 Playwright 浏览器：元素定位→状态判断→精确操作→等待DOM刷新→数据提取。
  支持：登录保存session、表单批量填写、表格数据抓取、AJAX等待、网络拦截获取API数据、文件上传下载。
  触发词：打开网站、登录、自动操作浏览器、截图、爬取数据、提交表单、playwright、抓取、填写。
---

# Browser Automation Skill v12

> 浏览器自动化控制。操作 Playwright 浏览器：元素定位 → 状态判断 → 精确操作 → 等待 DOM 刷新 → 数据提取。
> 支持：登录保存 session、表单批量填写、表格数据抓取、AJAX 等待、网络拦截获取 API 数据、文件上传/下载。
> 触发词：打开网站、登录、自动操作浏览器、截图、爬取数据、提交表单、playwright、抓取、填写。

---

## 核心工作流（必须遵守）

**每次浏览器操作都遵循这个 5 步循环：**

```
1. 分析页面   →  deep_inspect() 获取所有可交互元素
2. 定位目标   →  find() 或用 inspect 返回的精确 selector
3. 判断状态   →  state() 检查元素是否可见、可用、当前值
4. 执行操作   →  click/fill/select/submit
5. 等待结果   →  smart_wait() 或 wait_stable() 确认 DOM 刷新完成
```

**绝对禁止**: 不经 inspect/deep_inspect 就直接猜选择器操作。

---

## 快速启动

```python
import asyncio, sys
sys.path.insert(0, '/opt/litecode/skills/browser-automation')
from browser import Browser, ensure_server

ensure_server()   # 自动启动/复用后台浏览器服务

async def main():
    b = Browser("mysite")   # session 名，相同名复用登录状态
    print(await b.goto("https://example.com"))
    print(await b.deep_inspect())   # 先看清楚页面有什么
    # 根据 inspect 结果精确操作...

asyncio.run(main())
```

---

## 完整 API

### 导航
```python
await b.goto("https://...")                # 打开网址
await b.reload()                           # 刷新
await b.back()                             # 后退
```

### 元素分析（操作前必调）
```python
await b.inspect()                          # 基础分析: 输入框/按钮/链接
await b.deep_inspect()                     # 深度分析: 全部可交互元素 + data属性 + 位置
await b.deep_inspect(scope="#main-form")   # 限定范围分析
await b.find("搜索")                        # 多策略定位: 自动尝试 CSS/文本/placeholder/label
await b.find("//div[@class='result']", strategy="xpath")  # XPath 定位
await b.state("#submit-btn")               # 检查状态: 可见？可用？当前值？
await b.count(".item")                     # 计数匹配元素
```

### 点击
```python
await b.click("#selector")                 # CSS 选择器点击
await b.click_text("登录")                 # 按文字点击
await b.click_xy(640, 360)                 # 坐标点击
await b.hover("#menu")                     # 悬停（触发下拉菜单）
await b.dblclick("#cell")                  # 双击
```

### 输入
```python
await b.fill("#input", "内容")             # 清空后填入
await b.type("#input", "内容")             # 逐字打字（更真实，躲检测）
await b.press("Enter")                     # 按键
await b.select("#dropdown", "option_value")# 下拉选择
await b.fill_form({                        # 批量填写表单
    "#name": "张三",
    "#email": "test@test.com",
    "#age": "25",
    "select#city": "beijing",
})
```

### 等待（关键！）
```python
await b.wait(2000)                         # 固定等待 ms
await b.wait_for("#element")               # 等元素出现
await b.wait_url("**/dashboard**")         # 等 URL 变化
await b.wait_stable()                      # 等 DOM 停止变化（AJAX 完成）
await b.smart_wait(sel="#result-table")    # 智能等待: 网络+DOM+元素 三合一
```

### 数据提取
```python
await b.text("#selector")                  # 获取文本
await b.attr("#link", "href")              # 获取属性
await b.html("#container")                 # 获取 HTML
await b.url()                              # 当前 URL
await b.js("document.title")              # 执行 JS
await b.table("table.data")               # 提取表格 → 结构化数据
await b.form("form#login")                # 提取表单结构
await b.scrape(".item", {                  # 列表爬取
    "title": "h3", "link": "a@href", "time": ".date"
}, limit=50)
await b.intercept("/api/data", trigger_sel="#load-btn")  # 拦截 API 响应
```

### 文件操作
```python
await b.upload("input[type=file]", "/path/to/file.pdf")
await b.download("#download-btn")
```

### 截图与滚动
```python
await b.shot()                             # 截图
await b.scroll(500)                        # 向下滚
await b.scroll_to("#footer")              # 滚到元素
```

### Session 管理
```python
await b.save()                             # 保存登录状态，下次自动跳过登录
```

---

## 实战模式

### 模式 1: 登录并保存 session

```python
async def login_flow():
    b = Browser("weibo")
    print(await b.goto("https://weibo.com/login"))

    # 第1步: 分析页面元素
    print(await b.deep_inspect())

    # 第2步: 根据 inspect 结果找到真实的输入框选择器
    # (不要猜! inspect 会告诉你精确的 selector)
    print(await b.fill("#loginName", "手机号"))
    print(await b.fill("#loginPassword", "密码"))

    # 第3步: 检查提交按钮状态
    print(await b.state(".login-btn"))  # 确认按钮可用

    # 第4步: 点击提交
    print(await b.click(".login-btn"))

    # 第5步: 智能等待登录完成
    print(await b.smart_wait(sel=".user-avatar"))  # 等待登录后的标志元素

    # 第6步: 保存 session
    print(await b.save())
```

### 模式 2: 表单填写 → 提交 → 等待结果

```python
async def submit_form():
    b = Browser("erp")
    await b.goto("http://erp.company.com/order/new", show_shot=False)

    # 分析表单结构
    print(await b.form("form#order-form"))  # 返回所有字段 + label + 当前值

    # 批量填写
    print(await b.fill_form({
        "#customer-name": "客户A",
        "#product": "产品X",
        "#quantity": "100",
        "select#warehouse": "上海仓",
        "#notes": "加急处理",
    }))

    # 截图确认
    print(await b.shot())

    # 提交
    print(await b.click("button[type=submit]"))

    # 等待 DOM 刷新（关键! 提交后要等服务器返回）
    print(await b.wait_stable(stable_ms=2000))

    # 验证提交结果
    print(await b.text(".alert-success"))  # 读取成功提示
```

### 模式 3: 表格数据抓取

```python
async def scrape_table():
    b = Browser("data")
    await b.goto("http://dashboard.com/reports", show_shot=False)

    # 等待表格加载完成
    print(await b.smart_wait(sel="table.report-data"))

    # 提取表格（自动识别表头，返回 dict 格式）
    print(await b.table("table.report-data", max_rows=200))

    # 如果有分页，翻页继续
    while True:
        next_btn = await b._eval(
            "document.querySelector('.pagination .next:not(.disabled)')?.offsetParent !== null"
        )
        if not next_btn:
            break
        print(await b.click(".pagination .next"))
        print(await b.wait_stable())  # 等待新数据加载
        print(await b.table("table.report-data"))
```

### 模式 4: AJAX 数据拦截

```python
async def intercept_api():
    b = Browser("api")
    await b.goto("http://app.com/dashboard", show_shot=False)

    # 点击按钮触发 AJAX，同时拦截 API 响应
    result = await b.intercept(
        url_pattern="/api/v1/metrics",   # 要拦截的 URL 模式
        trigger_sel="#refresh-btn",       # 触发请求的按钮
        timeout=10000,
    )
    print(result)  # 直接拿到 JSON 数据，不用从 DOM 里抠
```

### 模式 5: 复杂页面分步操作

```python
async def complex_flow():
    b = Browser("admin")
    await b.goto("http://admin.site.com/users", show_shot=False)

    # 阶段1: 搜索用户
    print(await b.deep_inspect(scope=".search-bar"))
    print(await b.fill("input[name=search]", "张三"))
    print(await b.click("button.search-btn"))
    print(await b.wait_stable())

    # 阶段2: 点击搜索结果中的第一个用户
    count = await b._eval("document.querySelectorAll('.user-row').length")
    if count > 0:
        print(await b.click(".user-row:first-child"))
        print(await b.smart_wait(sel=".user-detail"))

        # 阶段3: 在详情页修改数据
        print(await b.form("form.user-edit"))  # 看表单结构
        print(await b.fill("#role", "admin"))
        print(await b.click("#save-btn"))
        print(await b.wait_stable(stable_ms=2000))
        print(await b.text(".toast-message"))  # 读取操作结果
```

### 模式 6: 无限滚动页面数据采集

```python
async def infinite_scroll():
    b = Browser("feed")
    await b.goto("https://feed.example.com", show_shot=False)

    all_items = []
    for _ in range(20):  # 最多滚 20 次
        # 当前可见项
        items = await b.scrape(".feed-item", {
            "title": ".title", "content": ".content", "time": ".timestamp"
        })
        all_items.extend(items)

        # 记住滚动前的元素数
        before_count = await b._eval("document.querySelectorAll('.feed-item').length")

        # 滚到底部
        await b.scroll(3000)
        await b.wait_stable(stable_ms=1500)

        # 检查是否加载了新内容
        after_count = await b._eval("document.querySelectorAll('.feed-item').length")
        if after_count == before_count:
            break  # 没有新内容了

    print(f"共采集 {len(all_items)} 条")
```

---

## 关键注意事项

1. **永远先 inspect/deep_inspect**: 不要凭记忆或猜测写选择器
2. **操作后必须等待**: 任何会触发 AJAX/页面变化的操作后，都要 `wait_stable()` 或 `smart_wait()`
3. **用 state() 检查再操作**: 按钮可能是 disabled 的，输入框可能是 hidden 的
4. **Session 复用**: 同一网站用同一个 session 名，避免重复登录
5. **选择器优先级**: `#id` > `[name=x]` > `[data-testid=x]` > `.class` > 文本定位
6. **表格数据用 table()**: 不要手动用 JS 提取表格，`table()` 自动处理表头
7. **API 数据用 intercept()**: 页面 AJAX 加载的数据，拦截比 DOM 提取更准确
8. **验证码/人工步骤**: 截图发给用户，用轮询检测用户输入完成

## 服务管理

```bash
# 启动
nohup python3 /opt/litecode/skills/browser-automation/browser_server.py > /tmp/bas.log 2>&1 &

# 健康检查
curl -s http://127.0.0.1:19000/health

# 查看日志
tail -f /tmp/bas.log
```
