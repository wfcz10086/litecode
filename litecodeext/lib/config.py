"""lib/config.py — 集中配置 + 依赖模块懒加载（带重试，不阻塞）"""
import json, logging, os, sys, time
from pathlib import Path

# ── Logging ──────────────────────────────────────────────────
_log_fmt = "\033[2m%(asctime)s\033[0m %(message)s"
logging.basicConfig(level=logging.DEBUG, format=_log_fmt, datefmt="%H:%M:%S",
                    stream=sys.stdout, force=True)
for _n in ("uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
    logging.getLogger(_n).setLevel(logging.WARNING)

# [FIX] 过滤 uvicorn "Invalid HTTP request received" — curl -v / 端口扫描会刷屏
# 这类异常请求对 agent 服务无意义, 保留在 TRACE 级别以防 debug 需要
class _UvicornNoiseFilter(logging.Filter):
    _NOISE = (
        "Invalid HTTP request received",
        "Invalid HTTP method",
        "Protocol error",
    )
    def filter(self, record):
        try:
            _m = record.getMessage()
        except Exception:
            return True
        return not any(n in _m for n in self._NOISE)

for _un in ("uvicorn.error", "uvicorn"):
    logging.getLogger(_un).addFilter(_UvicornNoiseFilter())

log = logging.getLogger("openclaw")

# ── Config JSON ──────────────────────────────────────────────
BASE = Path(__file__).parent.parent

def load_cfg() -> dict:
    p = BASE / "config.json"
    if not p.exists():
        raise RuntimeError(f"config.json not found at {p}")
    return json.loads(p.read_text())

CFG          = load_cfg()
_SRV         = CFG["server"]
_MDL         = CFG["model"]
_AGT         = CFG.get("agent", {})
_PATHS       = CFG.get("paths", {})
SEARCH_CFG   = CFG.get("search", {})
TRACE_CFG    = CFG.get("iteration_trace", {})

TOKEN        = _SRV["token"]
PORT         = _SRV.get("port", 18789)
HOST         = _SRV.get("host", "0.0.0.0")
MODEL_ID         = _MDL["id"]
BACKEND_URL      = _MDL["backend_url"].rstrip("/")
API_KEY          = _MDL.get("api_key", "EMPTY")
MAX_TOKENS       = _MDL.get("max_tokens", 8192)
ENABLE_THINKING  = _MDL.get("enable_thinking", False)
THINKING_BUDGET  = _MDL.get("thinking_budget", 8192)
CONTEXT_WINDOW   = int(_MDL.get("context_window", 200000))
BACKEND_TYPE     = _MDL.get("backend_type", "vllm")
# [thinking-route 2026-08-26] backend_type 混淆了两件事: "线路协议"(vLLM 说 OpenAI 协议
# → 写 openai 没错) 和 "服务栈"(它是 vLLM → 需要 chat_template_kwargs)。两个都对, 但
# thinking_adapter 只能按一个字段分支, 结果 deployment="vllm" 这条正确信息被无视,
# chat_template_kwargs 和 max_tokens+=budget 补偿双双跳过。把 deployment 也带进 _LIVE,
# 由 thinking_adapter 两者取一判定服务栈。
DEPLOYMENT       = str(_MDL.get("deployment", "") or "").strip().lower()
# [wire-name 2026-08-26] config 里的 `id` 原来同时当两件事用: 界面/切换用的显示名, 和
# 发给上游 API 的 model 字段。二者不一定相同 —— 自建 vLLM 的 --served-model-name 是固定的
# (如 deepseek-v4-flash), 而同名的中转线路也要占这个 id, 于是"自建 + 中转"两条无法共存
# (实测自建端点对 deepseek-v4-flash-local 直接 404: "The model does not exist")。
# 加可选的 served_model_name 把两者解耦: 没配就退回 id, 老配置行为不变。
SERVED_MODEL_NAME = str(_MDL.get("served_model_name", "") or "").strip() or MODEL_ID
SUPPORTS_VISION  = bool(_MDL.get("supports_vision", False))
# [vision-route 2026-05-26] 主模型不支持视觉时, 临时路由到此 id 的 fallback 模型
# 必须是 config.models[] 里有定义且 supports_vision=true 的 id.
# 空字符串 = 不启用路由, 走原 _strip_vision_from_messages 剥图逻辑.
# [2026-05-29] env LITECODE_VISION_FALLBACK 优先 (运维/测试可临时切, 不动 config.json)
VISION_FALLBACK_ID = str(os.environ.get("LITECODE_VISION_FALLBACK")
                         or CFG.get("vision_fallback_id", "")).strip()
CHARS_PER_TOKEN  = 4      # 英文经验值; 中文别用它, 用下面的 est_tokens()
IMAGE_TOKEN_COST = 1600   # 单张图的近似 token 成本 (视觉模型按 patch 计, 与 base64 长度无关)
NON_CJK_CHARS_PER_TOKEN = 3   # 非中文按 3 字符/token — 历史里多是 JSON/URL/代码, 比英文散文密


def est_tokens(text) -> int:
    """中英混排感知的 token 估算。

    [FIX 2026-08-31] 之前全局用 `len(s) // 4`。4 字符/token 是英文经验值,
    而 CJK 在主流 BPE 里约 1~1.5 字符/token —— 中文内容被低估 3~4 倍。
    实测后果: 会话 wx-b3607 实际 180,828 input tokens 撑爆 204,800 上下文,
    而裁剪闸门 (阈值 153,600) 按旧口径只看到约 1/3, 从未触发,
    最终由 vLLM 返回 400, 错误原文还漏进了用户的微信回复。

    估法: CJK 按 1 token/字, 其余按 4 字符/token。宁可高估不可低估 ——
    高估只是提前裁剪, 低估会直接把请求打爆。
    """
    if not text:
        return 0
    # [FIX 2026-08-31] 多模态 content 是 list, 里面的 image_url 是 base64 data URL。
    # 直接 str() 会把 1MB 图片的 ~140 万字符算成 ~35 万 token, 实测导致
    # [TOKEN-BUDGET] removed 234 msgs —— 一张图就把整段历史清空了。
    # 视觉模型按 patch 计费, 一张图通常 1000~2000 t, 与 base64 长度无关。
    if isinstance(text, (list, tuple)):
        total = 0
        for part in text:
            if isinstance(part, dict):
                if part.get("type") == "image_url" or "image_url" in part:
                    total += IMAGE_TOKEN_COST
                else:
                    total += est_tokens(part.get("text") or "")
            else:
                total += est_tokens(part)
        return total
    s = text if isinstance(text, str) else str(text)
    # 裸 data URL (未包在 list 里) 同样不能按长度算
    if "base64," in s and len(s) > 4096:
        import re as _re
        n_img = len(_re.findall(r"data:image/[a-z]+;base64,", s))
        if n_img:
            s = _re.sub(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+", "", s)
            return n_img * IMAGE_TOKEN_COST + est_tokens(s)
    cjk = 0
    for ch in s:
        o = ord(ch)
        if (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF      # 汉字
                or 0x3000 <= o <= 0x303F                          # 中文标点
                or 0xFF00 <= o <= 0xFFEF                          # 全角
                or 0xAC00 <= o <= 0xD7AF                          # 谚文
                or 0x3040 <= o <= 0x30FF):                        # 假名
            cjk += 1
    # [FIX 2026-08-31] 非 CJK 部分原按 4 字符/token (纯英文散文的经验值)。
    # 实测对照: 估 122,212 t vs vLLM 实报 192,513 t (1.58x 偏低) —— 因为历史里
    # 大头是工具返回的 JSON / URL / 代码, 标点和符号密集, 实际约 2.5~3 字符/token。
    # 改用 3 作为保守值。宁可高估: 高估只是提前裁剪, 低估会直接把请求打爆。
    return cjk + (len(s) - cjk) // NON_CJK_CHARS_PER_TOKEN

# ──  可变配置共享 —— 解决模型切换后 transport/subagent 读到旧值的问题
# 所有运行时可变的模型参数放在一个 dict 里，transport.py 通过引用访问
_LIVE = {
    "model_id":           MODEL_ID,
    "backend_url":        BACKEND_URL,
    "api_key":            API_KEY,
    "max_tokens":         MAX_TOKENS,
    "enable_thinking":    ENABLE_THINKING,
    "thinking_budget":    THINKING_BUDGET,
    "context_window":     CONTEXT_WINDOW,
    "backend_type":       BACKEND_TYPE,
    "deployment":         DEPLOYMENT,
    "served_model_name":  SERVED_MODEL_NAME,
    "supports_vision":    SUPPORTS_VISION,
    "vision_fallback_id": VISION_FALLBACK_ID,
}


def wire_model_name(display_id: str) -> str:
    """把 config 里的显示 id 翻成上游 API 真正认的 model 名。

    没配 `served_model_name` 就原样返回 display_id (老配置零行为变化)。
    先查 _LIVE (当前激活模型), 再查 models[] —— 后者是为了覆盖 vision-fallback
    路由: 那条路径会把 _model 换成 fallback 模型的 id, 此时不能用激活模型的线路名。
    """
    if not display_id:
        return display_id
    if display_id == _LIVE.get("model_id"):
        return _LIVE.get("served_model_name") or display_id
    for m in (CFG.get("models", []) or []):
        if m.get("id") == display_id:
            return str(m.get("served_model_name", "") or "").strip() or display_id
    return display_id


def find_vision_fallback() -> dict:
    """从 config.models[] 找 vision_fallback_id 对应的完整模型 dict.
    返回 {} 表示没配 / 找不到 / 不可用."""
    fid = _LIVE.get("vision_fallback_id", "")
    if not fid:
        return {}
    models = CFG.get("models", []) or []
    for m in models:
        if m.get("id") == fid and bool(m.get("supports_vision", False)):
            return m
    return {}

def reload_model(target: dict):
    """模型切换时由 server 调用，同步更新所有共享变量"""
    global MODEL_ID, BACKEND_URL, API_KEY, MAX_TOKENS, ENABLE_THINKING, THINKING_BUDGET, CONTEXT_WINDOW, BACKEND_TYPE
    MODEL_ID        = target["id"]
    BACKEND_URL     = target.get("backend_url", BACKEND_URL).rstrip("/")
    API_KEY         = target.get("api_key", API_KEY)
    MAX_TOKENS      = target.get("max_tokens", MAX_TOKENS)
    ENABLE_THINKING = target.get("enable_thinking", ENABLE_THINKING)
    THINKING_BUDGET = target.get("thinking_budget", THINKING_BUDGET)
    CONTEXT_WINDOW  = int(target.get("context_window", CONTEXT_WINDOW))
    BACKEND_TYPE    = target.get("backend_type", BACKEND_TYPE)
    global DEPLOYMENT
    # 切模型时 deployment 必须跟着换, 不能沿用旧值 —— 从 vllm 切到 deepseek 若还留着
    # "vllm" 会给 deepseek 发 chat_template_kwargs (无害但脏) 并错误加大 max_tokens.
    DEPLOYMENT      = str(target.get("deployment", "") or "").strip().lower()
    global SERVED_MODEL_NAME
    # 同 DEPLOYMENT: 切模型时必须跟着换, 不能沿用旧值 (否则会把上一个模型的线路名发给新后端)
    SERVED_MODEL_NAME = str(target.get("served_model_name", "") or "").strip() or MODEL_ID
    global SUPPORTS_VISION
    SUPPORTS_VISION = bool(target.get("supports_vision", False))
    # 同步到 _LIVE dict — transport.py 通过引用拿到新值
    _LIVE.update({
        "model_id": MODEL_ID, "backend_url": BACKEND_URL, "api_key": API_KEY,
        "max_tokens": MAX_TOKENS, "enable_thinking": ENABLE_THINKING,
        "thinking_budget": THINKING_BUDGET, "context_window": CONTEXT_WINDOW,
        "backend_type": BACKEND_TYPE, "supports_vision": SUPPORTS_VISION,
        "deployment": DEPLOYMENT, "served_model_name": SERVED_MODEL_NAME,
    })
    # [v12.8-fix] 切换时同步 thinking_adapter._active, 避免首请求仍走旧缓存
    try:
        from .thinking_adapter import update as _ta_sync
        _ta_sync(target)
    except Exception:
        pass
    log.info(f"\033[32m[config.reload_model] → {MODEL_ID}  backend={BACKEND_URL}\033[0m")

MAX_ITER         = _AGT.get("max_iterations", 20)
MAX_ERROR_STREAK = _AGT.get("max_error_streak", 3)
TASK_TIMEOUT     = _AGT.get("task_timeout_seconds", 600)
SESSION_TTL      = _AGT.get("session_ttl", 3600)

# [FIX 2026-08-31] 单一真相源。此前 15 个文件各自写
#   _paths.get("workspace_base", "/tmp/openclaw_workspace")   ← 失效的默认值
# 而 openclaw_workspace **在容器里根本不存在** (实际是 /tmp/litecode_workspace) ——
# 一旦 config 没提供 workspace_base, 全系统会静默落到一个不存在的目录。
# 更糟的是 wechat_bridge 的发图提示和 browser 的下载默认目录直接硬编码了它,
# 文件会落在挂载卷之外, 容器重建即丢失。
DEFAULT_WORKSPACE = "/tmp/litecode_workspace"
WORKSPACE    = Path(_PATHS.get("workspace_base", _AGT.get("workspace", DEFAULT_WORKSPACE)))
SKILLS_DIR   = Path(_PATHS.get("skills_dir", _AGT.get("skills_dir", str(BASE / "skills"))))
PROMPTS_DIR  = Path(_PATHS.get("prompts_dir", str(BASE / "prompts")))
TEMPLATE_DIR = Path(_PATHS.get("template_dir", str(BASE / "workspace_template")))
LOGS_DIR     = Path(_PATHS.get("logs_dir", str(WORKSPACE / "logs")))
SESSIONS_DISK = Path(_PATHS.get("sessions_dir", str(WORKSPACE / "sessions")))
for _d in (WORKSPACE, PROMPTS_DIR, LOGS_DIR, SESSIONS_DISK):
    _d.mkdir(parents=True, exist_ok=True)

_MEM_CFG = CFG.get("memory", {})
MEM_SOFT = _MEM_CFG.get("soft_token_limit", 30000)
MEM_HARD = _MEM_CFG.get("hard_token_limit", 60000)

# ── Lazy imports with retry (non-blocking) ───────────────────
def _try_import(label, fn, retries=2, delay=0.3):
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt < retries:
                time.sleep(delay)
            else:
                log.warning(f"[{label}] unavailable: {e}")
    return None

# itrace
HAS_ITRACE = False
_noop_tracer = type("T", (), {k: (lambda *a, **kw: None) for k in
    ["start","iteration_begin","iteration_end","tool_exec","llm_raw","skill_loaded","end"]})
_noop_tracer.enabled = False
make_tracer = lambda *a, **kw: _noop_tracer()
trace_est_tokens = lambda t: max(1, len(t) // 4) if t else 0

def _load_itrace():
    global HAS_ITRACE, make_tracer, trace_est_tokens
    from itrace import make_tracer as _mt, _estimate_tokens as _et
    HAS_ITRACE, make_tracer, trace_est_tokens = True, _mt, _et
_try_import("itrace", _load_itrace)

# executor_v4
HAS_EXECUTOR = False
execute_shell_async = None
def _load_executor():
    global HAS_EXECUTOR, execute_shell_async
    from executor_v4 import execute_shell_async as _esa
    HAS_EXECUTOR, execute_shell_async = True, _esa
_try_import("executor_v4", _load_executor)

# memory
HAS_MEMORY = False
mem_get = lambda *a, **kw: None
auto_update_user = auto_update_tools = register_skill = smart_auto_memory = lambda *a, **kw: None
def _load_memory():
    global HAS_MEMORY, mem_get, auto_update_user, auto_update_tools, register_skill, smart_auto_memory
    from memory import (get_manager, auto_update_user_md, auto_update_tools_md,
                        register_dynamic_skill, smart_auto_memory as _smart_auto_memory_fn)
    HAS_MEMORY, mem_get = True, get_manager
    auto_update_user, auto_update_tools = auto_update_user_md, auto_update_tools_md
    register_skill, smart_auto_memory = register_dynamic_skill, _smart_auto_memory_fn
_try_import("memory", _load_memory)

# deep_search
HAS_DEEP_SEARCH = False
deep_search_tool = None
def _load_deep_search():
    global HAS_DEEP_SEARCH, deep_search_tool
    from deep_search import DeepSearchTool
    HAS_DEEP_SEARCH, deep_search_tool = True, DeepSearchTool
_try_import("deep_search", _load_deep_search)

# memory_index (v12)
HAS_MEMORY_INDEX = False
memory_index_get = None
def _load_memory_index():
    global HAS_MEMORY_INDEX, memory_index_get
    from memory_index import get_index
    HAS_MEMORY_INDEX, memory_index_get = True, get_index
_try_import("memory_index", _load_memory_index)

# thinking_adapter
def _load_thinking_adapter():
    from .thinking_adapter import init as _ta_init
    _ta_init(_MDL)
_try_import("thinking_adapter", _load_thinking_adapter)
