"""litecli 公共层: 配置常量 + cookie/HTTP 请求 + 输出渲染 + 模型引用解析 (从 litecli.py 拆出)."""
import json
import os
import sys
from pathlib import Path

import click
import requests


# ── 配置 ─────────────────────────────────────────────────────
# BASE (litecli.py 所在目录) 留在 litecli.py; WEB_URL 被 --url 覆盖时由 litecli._sync_web_url 同步
LITECODE_DIR = Path.home() / ".litecode"
LITECODE_DIR.mkdir(parents=True, exist_ok=True)
COOKIE_FILE = LITECODE_DIR / "cli_cookie.txt"

WEB_URL = os.environ.get("LITECODE_WEB_URL", "http://localhost:18790")
SERVER_URL = os.environ.get("OPENCLAW_URL", "http://localhost:18789")

_IS_TTY = sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    if not _IS_TTY:
        return text
    codes = {"red": "31", "green": "32", "yellow": "33", "blue": "34",
             "cyan": "36", "magenta": "35", "gray": "90", "bold": "1"}
    return f"\033[{codes.get(color, '0')}m{text}\033[0m"


def _err(msg: str, exit_code: int = 1):
    click.echo(_c(f"✗ {msg}", "red"), err=True)
    sys.exit(exit_code)


def _ok(msg: str):
    click.echo(_c(f"✓ {msg}", "green"))


def _info(msg: str):
    click.echo(_c(msg, "cyan"))


def _save_cookie(cookie: str):
    COOKIE_FILE.write_text(cookie)
    try:
        COOKIE_FILE.chmod(0o600)
    except Exception:
        pass


def _load_cookie() -> str:
    return COOKIE_FILE.read_text().strip() if COOKIE_FILE.exists() else ""


def _session() -> requests.Session:
    s = requests.Session()
    cookie = _load_cookie()
    if cookie:
        s.cookies.set("lc_auth", cookie)
    return s


def _req(method: str, path: str, json_body=None, params=None, timeout: int = 30,
         stream: bool = False) -> requests.Response:
    url = f"{WEB_URL}{path}"
    s = _session()
    try:
        r = s.request(method, url, json=json_body, params=params, timeout=timeout, stream=stream)
    except requests.RequestException as e:
        _err(f"网络错误: {e}")
    if r.status_code == 401:
        _err("未登录或 cookie 失效, 跑: litecli login")
    return r


def _r_json(method: str, path: str, **kw) -> dict:
    r = _req(method, path, **kw)
    if r.status_code >= 400:
        try:
            d = r.json()
            _err(f"{path} → HTTP {r.status_code}: {d.get('detail') or d.get('error') or d}")
        except Exception:
            _err(f"{path} → HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text}


def _emit(obj, fmt: str = "auto"):
    """根据 --json 标志或对象类型决定输出格式."""
    if fmt == "json" or os.environ.get("LITECLI_OUTPUT") == "json":
        click.echo(json.dumps(obj, ensure_ascii=False, indent=2))
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                click.echo(f"{_c(k, 'cyan')}: {json.dumps(v, ensure_ascii=False)[:200]}")
            else:
                click.echo(f"{_c(k, 'cyan')}: {v}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, dict):
                click.echo(_c(f"--- [{i}] ---", "gray"))
                for k, v in item.items():
                    click.echo(f"  {k}: {str(v)[:100]}")
            else:
                click.echo(f"[{i}] {item}")
    else:
        click.echo(obj)


def _json_mode() -> bool:
    return os.environ.get("LITECLI_OUTPUT") == "json"


def _json_out(obj) -> bool:
    """--json 时原样输出接口响应并返回 True, 读取类命令据此直接 return (不再走人读格式)。"""
    if _json_mode():
        click.echo(json.dumps(obj, ensure_ascii=False, indent=2))
        return True
    return False


def _none_if_empty(items):
    """列表为空时给一句"(无)", 避免命令静默无输出; 原样返回 items 供循环。"""
    if not items:
        click.echo(_c("  （无）", "gray"))
    return items


def _resolve_model_id(target: str) -> str:
    """支持 #1..#6 简写, 自动查 /api/models 找 id."""
    mid, _ = _resolve_model_ref(target)
    return mid


def _resolve_model_ref(target: str) -> tuple[str, str]:
    """返回 (model_id, backend_url). 同名双 entry 必须靠 backend_url 区分.
    - "#N" 简写 → 取列表第 N 条, 同时取它的 backend_url
    - 直接 id 字符串 → backend_url 留空, 让 server 走 id-only 兜底
    """
    if not target.startswith("#"):
        return target, ""
    try:
        n = int(target[1:])
    except ValueError:
        return target, ""
    d = _r_json("GET", "/api/models")
    models = d.get("models", [])
    if 1 <= n <= len(models):
        m = models[n - 1]
        return m["id"], m.get("backend_url", "")
    _err(f"模型 #{n} 不存在 (共 {len(models)} 个)")
    return "", ""
