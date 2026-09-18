---
name: template-engine
description: >
  FastAPI / Flask 模板渲染的正确姿势。避免 string.Template 与 Jinja2 混用
  导致的 unhashable type: 'dict' 等运行时错误。统一选一个引擎贯穿全项目。
  触发关键词: jinja2 / template / html / 模板 / render / TemplateResponse /
  string.Template / 前后端拆分 / 模板拆分 / partials / 组件。

## Keywords
jinja2 template html 模板 render TemplateResponse string.Template
前后端 拆分 partials 组件 base.html extends include
---

# 模板引擎 Skill

## 核心选型: 用 Jinja2, 别用 string.Template

| 场景 | 方案 |
|------|------|
| FastAPI + 模板 | **Jinja2Templates** (官方支持, 支持 extends/include/for/if) |
| Flask + 模板 | **render_template** (天然 Jinja2) |
| 纯字符串替换 | string.Template (只在无依赖的小脚本里) |

**绝对不要** `Template(html).substitute(coin=<dict>)` + 模板里写 `{{ coin.symbol }}` —
string.Template 只认 `$name` / `${name}`, `{{ coin.symbol }}` 会按字面量输出,
而 Python dict 当 `substitute()` 的 key 会炸 `TypeError: unhashable type: 'dict'`。

## FastAPI + Jinja2 最小模板

### app.py
```python
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    coins = [{"symbol":"BTC","price":75000}, {"symbol":"ETH","price":2300}]
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "coins": coins, "update_time": "2026-04-19 12:00"},
    )
```

### templates/base.html (公共框架)
```html
<!DOCTYPE html>
<html>
<head><title>{% block title %}监控{% endblock %}</title></head>
<body>
  <h1>{% block heading %}行情{% endblock %}</h1>
  {% block content %}{% endblock %}
  {% include "components/scripts.html" %}
</body>
</html>
```

### templates/index.html (继承 base)
```html
{% extends "base.html" %}
{% block content %}
  <table>
    {% for c in coins %}
    <tr><td>{{ c.symbol }}</td><td>{{ c.price }}</td></tr>
    {% endfor %}
  </table>
  <p>更新: {{ update_time }}</p>
{% endblock %}
```

### templates/components/scripts.html (组件)
```html
<script>
  console.log("loaded");
</script>
```

## 拆分大模板的三种方式

1. **`{% extends "base.html" %}` + `{% block xxx %}`** — 页面级继承, 最干净
2. **`{% include "components/xxx.html" %}`** — 重复片段, 传上下文变量
3. **Jinja macro** — 参数化片段, 像函数一样调用
   ```html
   {% macro coin_row(c, idx) %}<tr><td>{{idx}}</td><td>{{c.symbol}}</td></tr>{% endmacro %}
   ```

## 常见坑

1. **`TypeError: unhashable type: 'dict'`** — string.Template 塞了 dict 当 key → 换 Jinja2
2. **`UndefinedError: 'xxx' is undefined`** — 上下文没传, 给默认值: `{{ x | default('-') }}`
3. **HTML 转义把 `<br>` 吞了** — 用 `{{ content | safe }}` (确认来源可信)
4. **循环里要序号** — `{% for c in coins %}{{ loop.index }} {{ c.name }}{% endfor %}`
5. **autoescape 误伤** — `Jinja2Templates(directory=".", autoescape=select_autoescape(['html']))`
6. **改模板后不 reload** — 开发时 FastAPI `--reload` 或 Jinja `auto_reload=True`

## 清理旧模板

拆分新模板时**先把旧的 index.html 改名 `_deprecated_index.html`**, 不要留残迹等它下次被 include 进来。
