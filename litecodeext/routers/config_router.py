"""config_router.py — 读 / 编辑 config.json 非敏感字段.

跟原 web_ui.py 实现完全兼容 (前端依赖现有 schema):
- GET 返回 {ok, config, editable_sections}
- PATCH 接受 {section1: {...}, ...} 顶级, 跳过 •••• 脱敏占位符
"""
import copy as _copy
import json

from fastapi import APIRouter, HTTPException, Request

from routers.auth_router import require_auth
from routers.state import S

router = APIRouter()
ORDER = 30  # 注册顺序 (routers.discover 按此升序 include, 路径重叠时先注册者生效)


_SENSITIVE_FIELDS = {
    ("server", "token"),
    ("web_ui", "auth", "password"),
}
_EDITABLE_SECTIONS = {"agent", "memory", "wechat", "search", "iteration_trace", "web_ui",
                      # [2026-05-22] 加 model: 让用户能开关 enable_thinking / 改 max_tokens / context_window 等
                      # api_key 敏感字段已被 _mask_sensitive 脱敏, PATCH 时占位符 ••••xxx 不会覆盖真值
                      "model"}


try:  # [sec 2026-10-19] 与工具参数脱敏同一套敏感键规则
    from plugins.tools.guard import _is_secret_key as _guard_is_secret_key
except Exception:  # pragma: no cover
    _guard_is_secret_key = None

_MASK = "••••"
_MASK_EXTRA_SUFFIX = ("_key", "_secret", "_token", "_password", "_passwd", "_authorization")
# 名字像密钥但其实是普通配置 (存的是环境变量名 / 文件路径), 不打码, 否则前端没法编辑
_MASK_EXEMPT = {"api_key_env", "key_file", "max_tokens"}


def _is_secret_cfg_key(k) -> bool:
    k = str(k).strip().lower().replace("-", "_")
    if k in _MASK_EXEMPT:
        return False
    if _guard_is_secret_key is not None and _guard_is_secret_key(k):
        return True
    return k in {"password", "passwd", "secret", "token", "api_key", "apikey", "key",
                 "authorization", "private_key"} or k.endswith(_MASK_EXTRA_SUFFIX)


def _mask_value(v):
    """[sec 2026-10-19] 短密钥 (<16 字符) 不露任何尾巴; 长密钥只露后 4 位便于辨认。"""
    s = str(v)
    if s in ("", "EMPTY"):
        return v
    return _MASK + s[-4:] if len(s) >= 16 else _MASK


def _mask_tree(v, depth: int = 0):
    if depth > 20:
        return v
    if isinstance(v, dict):
        return {k: (_mask_value(x) if _is_secret_cfg_key(k) and isinstance(x, (str, int, float))
                    and not isinstance(x, bool) and x not in (None, "")
                    else _mask_tree(x, depth + 1))
                for k, x in v.items()}
    if isinstance(v, list):
        return [_mask_tree(x, depth + 1) for x in v]
    return v


def _mask_sensitive(cfg: dict) -> dict:
    """递归脱敏: 任意层级里名字像 password/secret/token/api_key/key/authorization 的值
    (含 llm_watchdog.ssh.password、models[].api_key) 一律换成 ••••[后4位]。"""
    return _mask_tree(_copy.deepcopy(cfg))


def _is_placeholder(v) -> bool:
    return isinstance(v, str) and v.startswith(_MASK)


def _restore_placeholders(new, old):
    """PATCH 时把任意层级的脱敏占位符换回原值 (原值不存在则丢弃该键), 防止整段回写时把真密钥覆盖成 ••••。"""
    if isinstance(new, dict):
        old_d = old if isinstance(old, dict) else {}
        out = {}
        for k, v in new.items():
            if _is_placeholder(v):
                if k in old_d:
                    out[k] = old_d[k]
                continue
            out[k] = _restore_placeholders(v, old_d.get(k))
        return out
    if isinstance(new, list):
        old_l = old if isinstance(old, list) else []
        return [_restore_placeholders(x, old_l[i] if i < len(old_l) else None) for i, x in enumerate(new)]
    return new


def _guard_auth_patch(old_auth, new_auth) -> dict:
    """web_ui.auth 走 PATCH 时不许碰密码 (否则绕过旧密码校验): 改密码只能走 POST /api/auth/password.
    - 带了与现值不同的明文 password → 400
    - 没带 / 带脱敏占位符 / 与现值相同 → 保留原密码; auth 其余字段按键合并 (不整块覆盖)
    """
    old_auth = old_auth if isinstance(old_auth, dict) else {}
    if not isinstance(new_auth, dict):
        raise HTTPException(400, "web_ui.auth must be object")
    merged = dict(old_auth)
    for k, v in new_auth.items():
        if k == "password":
            if isinstance(v, str) and (v.startswith("••••") or v == old_auth.get("password")):
                continue
            raise HTTPException(400, "web_ui.auth.password 不能通过 /api/config 修改, "
                                     "请用 POST /api/auth/password (需验证当前密码)")
        merged[k] = v
    if "password" in old_auth:
        merged["password"] = old_auth["password"]
    return merged


@router.get("/api/config")
async def api_config_get(request: Request):
    """读当前 config.json (敏感字段脱敏)."""
    require_auth(request)
    try:
        cfg = json.loads(S.cfg_path.read_text())
        return {
            "ok": True,
            "config": _mask_sensitive(cfg),
            "editable_sections": sorted(_EDITABLE_SECTIONS),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.patch("/api/config")
async def api_config_patch(request: Request):
    """浅合并更新 config.json 的白名单字段.
    请求体: {"agent": {"task_timeout_seconds": 1800}, "memory": {...}, ...}
    - 只接受 _EDITABLE_SECTIONS 里的顶层段
    - 脱敏占位符 (••••xxx) 会被保留为原值, 不覆盖
    - 写入后不热加载 agent/memory 配置 (需要重启服务生效, 前端提示)
    """
    require_auth(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be object")
    try:
        cfg = json.loads(S.cfg_path.read_text())
    except Exception as e:
        raise HTTPException(500, f"read config failed: {e}")

    applied = {}
    rejected = []
    for section, new_val in body.items():
        if section not in _EDITABLE_SECTIONS:
            rejected.append(section)
            continue
        if not isinstance(new_val, dict):
            rejected.append(section)
            continue
        old = cfg.get(section, {}) or {}
        merged = dict(old)
        if section == "web_ui" and "auth" in new_val:
            new_val = dict(new_val)
            new_val["auth"] = _guard_auth_patch(old.get("auth"), new_val["auth"])
        for k, v in new_val.items():
            # 跳过脱敏占位符
            if isinstance(v, str) and v.startswith("••••"):
                continue
            merged[k] = _restore_placeholders(v, old.get(k))  # [sec 2026-10-19] 嵌套占位符也还原
        cfg[section] = merged
        applied[section] = merged

    # [migrate-2026-05] bind-mount inode 友好: 用 truncate-then-write, 不 rename
    # 避免容器内 /opt/litecode/config.json 跟 host /opt/litecode/config.json 失联
    try:
        S.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=4))
    except Exception as e:
        raise HTTPException(500, f"write config failed: {e}")

    # [hot-reload 2026-05-22] model section 改了 → 立即调 reload_model 同步 _LIVE
    # 否则 transport 还读旧 enable_thinking, 用户 toggle 后实测无效, 误导"已立即生效"提示
    model_reloaded = False
    if "model" in applied:
        try:
            from lib.config import reload_model
            reload_model(cfg["model"])
            model_reloaded = True
        except Exception as _re:
            import logging; logging.getLogger("openclaw").warning(f"  [config.reload_model] fail: {_re}")

    # agent/memory/iteration_trace 这些没有热 reload 路径, 仍需 server 重启
    needs_restart = any(s in applied for s in ("agent", "memory", "iteration_trace"))
    if "model" in applied and not model_reloaded:
        needs_restart = True   # 热 reload 失败, 退化成 "需重启"
    return {
        "ok": True,
        "applied": applied,
        "rejected": rejected,
        "needs_restart": needs_restart,
        "model_reloaded": model_reloaded,
        "hint": (
            "部分字段需要重启 server 才生效" if needs_restart else
            ("model 已热 reload (推理参数立即生效)" if model_reloaded else "已立即生效")
        ),
    }
