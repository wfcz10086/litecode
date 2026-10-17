"""DeepSearch 公共底座: 模块级常量 / httpx 可选依赖 / 垃圾域名过滤 / wire-name 翻译 (从 deep_search.py 拆出)。"""


# [wire-name 2026-08-28] config 的 `id` 是显示名, 上游认的可能是别的名字
# (自建 vLLM 的 --served-model-name)。本模块直接打上游 /chat/completions,
# 不经过 lib.transport, 所以要自己翻译 —— 否则会 404
# ("The model `xxx-local` does not exist", 实测撞过)。
def _wire(mid: str) -> str:
    try:
        from lib.config import wire_model_name
        return wire_model_name(mid)
    except Exception:
        return mid

try:
    import httpx
except ImportError:
    httpx = None  # [v1.0] 降级处理，后续使用时检查

EFFORT = {"low": 3, "mid": 8, "high": 15}

# [v1.0] 垃圾域名黑名单 — 搜索结果里的 weixin/广告/备案页全部过滤掉
_SPAM_DOMAINS = frozenset({
    # sogou 常返回的微信公众号 (都是转发/软文/SEO垃圾)
    "mp.weixin.qq.com", "weixin.qq.com",
    # 政府备案/许可证查询页, 几乎不是用户想要的
    "beian.gov.cn", "beian.miit.gov.cn", "miit.gov.cn",
    "creditchina.gov.cn", "gsxt.gov.cn", "samr.gov.cn",
    "icp.gov.cn", "mca.gov.cn", "nxiang.gov.cn",
    # 国内图床 / 淘宝联盟 / 广告聚合
    "img.alicdn.com", "click.taobao.com", "union.click.jd.com",
    # 搜索引擎自身的广告/跳转页
    "ad.doubleclick.net", "baidu.com/link?", "www.sogou.com/link?",
})

def _is_spam_url(url: str) -> bool:
    if not url:
        return True
    u = url.lower()
    return any(d in u for d in _SPAM_DOMAINS)

# ── 默认搜索源（可被 config.json 覆盖）────────────────
_DEFAULT_SEARCH_SOURCES = [
    {"name": "duckduckgo", "url_template": "https://html.duckduckgo.com/html/?q={q}"},
    {"name": "sogou",      "url_template": "https://www.sogou.com/web?query={q}"},
    {"name": "so360",      "url_template": "https://www.so.com/s?q={q}"},
    {"name": "baidu",      "url_template": "https://www.baidu.com/s?wd={q}"},
]
