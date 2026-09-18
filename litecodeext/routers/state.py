"""routers/state.py — Router 共享运行时 state

设计:
- web_ui.py import 后立即用 `init(...)` 把 CFG / SERVER_URL / HEADERS / WORKSPACE / etc.
  注入到 module-level 变量
- 各 router 用 `from .state import S` 拿到全部 state
- 这是个简易 DI: 避免循环 import, 也避免每个 router 都重新读 config.json

不要在这里放业务逻辑, 只放 const + state 引用.
"""
from pathlib import Path
from typing import Any, Callable, Optional


class _State:
    """运行时共享 state — 由 web_ui.py 在 import 时填充."""
    # 配置
    cfg: dict = {}
    # litecode_server (上游 API)
    server_url: str = ""
    token: str = ""
    headers: dict = {}
    # 工作区路径
    base_dir: Path = Path("/opt/litecode")
    workspace: Path = Path("/tmp/litecode_workspace")
    sessions_dir: Path = Path.home() / ".litecode" / "web_sessions"
    cfg_path: Path = Path("/opt/litecode/config.json")
    # 模型 (默认值, 切换后由 model_router 同步更新)
    model: str = ""
    ctx_window: int = 65000
    web_port: int = 18790
    # 共享 helper 函数 (由 web_ui 注入, 避免循环 import)
    aproxy: Optional[Callable[..., Any]] = None      # 上游 server 代理调用
    calc_cost: Optional[Callable[..., Any]] = None   # 价格计算
    # sessions
    list_sessions: Optional[Callable[..., Any]] = None
    save_session: Optional[Callable[..., Any]] = None
    load_session: Optional[Callable[..., Any]] = None
    create_session: Optional[Callable[..., Any]] = None
    sf: Optional[Callable[..., Any]] = None  # _sf(sid) -> Path
    load_tombstones: Optional[Callable[..., Any]] = None
    add_tombstone: Optional[Callable[..., Any]] = None
    tombstone_file: Optional[Any] = None
    # wechat
    wx_available: bool = False
    wx_manager: Optional[Callable[..., Any]] = None
    # workspace preview consts
    preview_img_exts: set = set()
    preview_text_exts: set = set()
    preview_pdf_exts: set = set()
    wx_safe_dirs: list = []
    # html
    get_html: Optional[Callable[..., Any]] = None
    # DAG
    start_dag_internal: Optional[Callable[..., Any]] = None


S = _State()


def init(**kwargs):
    """web_ui.py 在启动时调用, 把全局变量注入到 S."""
    for k, v in kwargs.items():
        setattr(S, k, v)
