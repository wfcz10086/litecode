#!/usr/bin/env python3
"""
browser.py — v12 增强版浏览器控制接口
======================================
新增:
  - deep_inspect:    深度元素分析
  - find_element:    多策略元素定位
  - check_state:     元素状态检查
  - wait_dom_stable: 等待 DOM 稳定
  - smart_wait:      智能等待（网络+DOM+元素）
  - extract_table:   表格数据提取
  - extract_form:    表单结构提取
  - fill_form:       批量填写表单
  - intercept:       网络请求拦截
  - hover/dblclick:  悬停/双击
  - upload/download: 文件上传/下载
  - get_attr:        获取元素属性
  - get_html:        获取元素 HTML
  - count:           计数匹配元素
"""
import asyncio, base64, json, os, subprocess, sys, time
from pathlib import Path
import httpx

PORT      = int(os.environ.get("BROWSER_AGENT_PORT", 19000))
BASE_URL  = f"http://127.0.0.1:{PORT}"
_SERVER   = Path(__file__).parent / "browser_server.py"
STATE_DIR = Path(os.environ.get("SESSIONS_DIR", "/data/sessions"))
STATE_DIR.mkdir(parents=True, exist_ok=True)


def ensure_server():
    """确保后台浏览器服务在跑"""
    try:
        httpx.get(f"{BASE_URL}/health", timeout=2)
        return True
    except Exception:
        pass
    print("[browser] 启动浏览器服务...")
    log = open("/tmp/bas.log", "a")
    subprocess.Popen([sys.executable, str(_SERVER)], stdout=log, stderr=log,
                     start_new_session=True)
    for i in range(20):
        time.sleep(1)
        try:
            httpx.get(f"{BASE_URL}/health", timeout=1)
            print(f"[browser] ✅ 服务已就绪 ({i+1}s)")
            return True
        except Exception:
            pass
    print("[browser] ❌ 启动失败，查看 /tmp/bas.log")
    return False


def _md(resp: dict, show_shot=True) -> str:
    """API 响应转 markdown"""
    icon = "✅" if resp.get("ok") else "❌"
    lines = [f"{icon} {resp.get('msg', '')}"]
    if resp.get("url"):
        lines.append(f"📍 `{resp['url']}`")
    if resp.get("data") is not None:
        d = resp["data"]
        if isinstance(d, (list, dict)):
            lines.append(f"\n```json\n{json.dumps(d, ensure_ascii=False, indent=2)[:3000]}\n```")
        elif d:
            lines.append(f"\n`{str(d)[:300]}`")
    if show_shot and resp.get("b64"):
        lines.append(f"\n![](data:image/png;base64,{resp['b64']})")
    return "\n".join(lines)


def _post(path, body):
    r = httpx.post(f"{BASE_URL}{path}", json=body, timeout=60)
    return r.json()


class Browser:
    def __init__(self, session="default"):
        self.s = session

    def _act(self, action, params=None, show_shot=True, wait=1000):
        r = _post("/act", {"action": action, "params": params or {},
                           "session": self.s, "wait": wait, "shot": show_shot})
        return _md(r, show_shot)

    # ── 原有操作（完全不变）──────────────────────────
    async def goto(self, url, **kw):      return self._act("goto", {"url": url}, **kw)
    async def reload(self, **kw):         return self._act("reload", {}, **kw)
    async def back(self, **kw):           return self._act("back", {}, **kw)
    async def click(self, sel, **kw):     return self._act("click", {"sel": sel}, **kw)
    async def click_text(self, t, **kw):  return self._act("click_text", {"text": t}, **kw)
    async def click_xy(self, x, y, **kw): return self._act("click_xy", {"x": x, "y": y}, **kw)
    async def fill(self, sel, val, **kw): return self._act("fill", {"sel": sel, "val": val}, show_shot=False, **kw)
    async def type(self, sel, val, **kw): return self._act("type", {"sel": sel, "val": val}, show_shot=False, **kw)
    async def press(self, key, **kw):     return self._act("press", {"key": key}, **kw)
    async def wait(self, ms=2000, **kw):  return self._act("wait", {"ms": ms}, show_shot=False, **kw)
    async def wait_for(self, sel, **kw):  return self._act("wait_for", {"sel": sel}, **kw)
    async def wait_url(self, pat, **kw):  return self._act("wait_url", {"pattern": pat}, **kw)
    async def shot(self, **kw):           return self._act("shot", {}, **kw)
    async def inspect(self, **kw):        return self._act("inspect", {}, **kw)
    async def scroll(self, px=300, **kw): return self._act("scroll", {"px": px}, show_shot=False, **kw)
    async def scroll_to(self, sel, **kw): return self._act("scroll_to", {"sel": sel}, **kw)
    async def save(self, **kw):           return self._act("save_session", {}, show_shot=False, **kw)
    async def text(self, sel, **kw):      return self._act("get_text", {"sel": sel}, show_shot=False, **kw)
    async def url(self, **kw):            return self._act("get_url", {}, show_shot=False, **kw)
    async def js(self, script, **kw):     return self._act("eval", {"script": script}, show_shot=False, **kw)
    async def select(self, sel, val, **kw): return self._act("select", {"sel": sel, "val": val}, **kw)
    async def scrape(self, item_sel, fields, limit=50, **kw):
        return self._act("scrape", {"item_sel": item_sel, "fields": fields, "limit": limit}, show_shot=False, **kw)

    async def _eval(self, script):
        r = _post("/act", {"action": "eval", "params": {"script": script},
                           "session": self.s, "wait": 0, "shot": False})
        return r.get("data")

    # ═══════════════════════════════════════════════════
    # v12 新增操作
    # ═══════════════════════════════════════════════════

    async def deep_inspect(self, scope="body", **kw):
        """深度元素分析: 覆盖所有可交互元素、data 属性、位置信息"""
        return self._act("deep_inspect", {"scope": scope}, **kw)

    async def find(self, query, strategy="auto", **kw):
        """多策略元素定位: CSS/XPath/文本/placeholder/aria-label/data-testid"""
        return self._act("find_element", {"query": query, "strategy": strategy}, show_shot=False, **kw)

    async def state(self, sel, **kw):
        """检查元素状态: 可见/可用/选中/值/属性"""
        return self._act("check_state", {"sel": sel}, show_shot=False, **kw)

    async def wait_stable(self, stable_ms=1000, timeout=15000, **kw):
        """等待 DOM 停止变化（AJAX 加载完成）"""
        return self._act("wait_dom_stable",
                         {"stable_ms": stable_ms, "timeout": timeout}, show_shot=False, **kw)

    async def smart_wait(self, sel=None, timeout=15000, **kw):
        """智能等待: 网络空闲 + DOM 稳定 + 目标元素出现"""
        params = {"timeout": timeout}
        if sel:
            params["sel"] = sel
        return self._act("smart_wait", params, **kw)

    async def table(self, sel="table", max_rows=100, **kw):
        """提取表格数据（自动识别表头，返回结构化数据）"""
        return self._act("extract_table", {"sel": sel, "max_rows": max_rows}, show_shot=False, **kw)

    async def form(self, sel="form", **kw):
        """提取表单结构（所有字段 + 当前值 + label）"""
        return self._act("extract_form", {"sel": sel}, show_shot=False, **kw)

    async def fill_form(self, fields: dict, **kw):
        """批量填写表单: {selector: value}"""
        return self._act("fill_form", {"fields": fields}, **kw)

    async def intercept(self, url_pattern, trigger_sel=None, timeout=10000, **kw):
        """拦截网络请求，获取 API 响应数据"""
        return self._act("intercept", {
            "url_pattern": url_pattern,
            "trigger": trigger_sel,
            "timeout": timeout,
        }, show_shot=False, **kw)

    async def hover(self, sel, **kw):
        """鼠标悬停"""
        return self._act("hover", {"sel": sel}, **kw)

    async def dblclick(self, sel, **kw):
        """双击"""
        return self._act("dblclick", {"sel": sel}, **kw)

    async def upload(self, sel, file_path, **kw):
        """上传文件"""
        return self._act("upload", {"sel": sel, "file": file_path}, **kw)

    async def download(self, sel, save_dir="/tmp/litecode_workspace/downloads", **kw):
        """点击下载"""
        return self._act("download", {"sel": sel, "save_dir": save_dir}, **kw)

    async def attr(self, sel, attr_name="href", **kw):
        """获取元素属性"""
        return self._act("get_attr", {"sel": sel, "attr": attr_name}, show_shot=False, **kw)

    async def html(self, sel="body", **kw):
        """获取元素 HTML"""
        return self._act("get_html", {"sel": sel}, show_shot=False, **kw)

    async def count(self, sel, **kw):
        """计数匹配元素"""
        return self._act("count", {"sel": sel}, show_shot=False, **kw)
