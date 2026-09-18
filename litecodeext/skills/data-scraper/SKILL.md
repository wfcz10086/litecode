---
name: data-scraper
description: >
  网页数据抓取与结构化提取。支持：静态页面抓取(requests/httpx)、动态渲染页面
  (playwright/selenium)、API逆向、JSON/HTML/XML解析、反爬绕过(UA/Cookie/代理)、
  批量抓取、增量更新、数据清洗导出(CSV/JSON/Excel)。
  触发关键词：抓取、爬虫、爬取、scrape、crawl、spider、采集、提取数据、
  解析网页、xpath、css选择器、请求、headers、cookie、代理、反爬。
  遇到任何网页数据采集任务必须使用本技能。

## Keywords
抓取 爬虫 爬取 scrape crawl spider 采集 提取 解析 xpath css selector
requests beautifulsoup lxml playwright selenium httpx aiohttp
反爬 ua cookie proxy headers 代理 验证码 限流 rate limit
json html xml csv api 接口 逆向 数据采集 批量 增量
---

# Data Scraper Skill

## 技术选型决策树

```
需要抓取的内容？
├── 静态HTML → requests + BeautifulSoup（最轻量）
├── API/JSON接口 → httpx（支持异步）
├── 需要JS渲染 → playwright（无头浏览器）
├── 批量/高并发 → aiohttp + asyncio
└── 需要登录/Cookie → requests.Session / playwright
```

## 标准模板

### 1. 基础抓取（requests + BS4）
```python
import requests
from bs4 import BeautifulSoup

def scrape(url, headers=None):
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    resp = requests.get(url, headers=headers or default_headers, timeout=15)
    resp.encoding = resp.apparent_encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    return soup

# 提取表格
def extract_table(soup, table_selector="table"):
    table = soup.select_one(table_selector)
    if not table: return []
    rows = []
    for tr in table.select("tr"):
        cells = [td.get_text(strip=True) for td in tr.select("td, th")]
        if cells: rows.append(cells)
    return rows
```

### 2. 异步批量抓取
```python
import asyncio, httpx

async def fetch_all(urls, max_concurrent=10):
    results = {}
    sem = asyncio.Semaphore(max_concurrent)
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        async def _fetch(url):
            async with sem:
                try:
                    resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                    results[url] = {"status": resp.status_code, "text": resp.text[:5000]}
                except Exception as e:
                    results[url] = {"status": -1, "error": str(e)}
        await asyncio.gather(*[_fetch(u) for u in urls])
    return results

# 使用
urls = ["https://example.com/page/1", "https://example.com/page/2"]
data = asyncio.run(fetch_all(urls))
```

### 3. API 接口抓取（JSON）
```python
import httpx

def call_api(url, params=None, headers=None):
    resp = httpx.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()

# 分页抓取
def fetch_paginated(base_url, pages=10):
    all_data = []
    for page in range(1, pages + 1):
        data = call_api(base_url, params={"page": page, "per_page": 100})
        if not data: break
        all_data.extend(data)
    return all_data
```

### 4. 数据导出
```python
import csv, json

def to_csv(data, filepath, headers=None):
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if headers: w.writerow(headers)
        w.writerows(data)

def to_json(data, filepath):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
```

## 反爬策略
- **UA轮换**: 准备5-10个真实UA，每次随机选
- **请求间隔**: `time.sleep(random.uniform(1, 3))` 每次请求间隔1-3秒
- **代理池**: 使用免费代理或自建代理池
- **Cookie维持**: `requests.Session()` 保持登录态
- **Referer伪装**: 设置合理的 Referer 头

## 依赖安装
```bash
pip3 install requests beautifulsoup4 lxml httpx aiohttp --break-system-packages
```

## 注意事项
- 遵守 robots.txt 和网站使用条款
- 控制请求频率，不要对目标服务器造成压力
- 敏感数据（个人信息等）不得非法采集
- 优先使用公开 API，避免不必要的页面解析
