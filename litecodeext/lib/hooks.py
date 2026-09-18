"""
hooks.py — Hooks 系统 (P17)
============================
3 个生命周期事件: SessionStart / PreToolUse / PostToolUse
hook 注册后, server 在对应事件触发. hook 可阻止 (返 False) 或修改 args.

API:
  register_hook(event, fn)
  unregister_hook(event, fn)
  fire(event, **kwargs) → list of return values (None=允许, dict=修改 args, False=阻止)
  list_hooks(event=None) → dict
"""
from typing import Callable, Dict, List, Any, Optional


_VALID_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse")
_hooks: Dict[str, List[Callable]] = {e: [] for e in _VALID_EVENTS}


def register_hook(event: str, fn: Callable) -> bool:
    if event not in _VALID_EVENTS:
        return False
    if fn not in _hooks[event]:
        _hooks[event].append(fn)
        return True
    return False


def unregister_hook(event: str, fn: Callable) -> bool:
    if event in _hooks and fn in _hooks[event]:
        _hooks[event].remove(fn)
        return True
    return False


def fire(event: str, **kwargs) -> List[Any]:
    """触发 event, 调所有注册的 hook. 返回值列表."""
    if event not in _hooks:
        return []
    out = []
    for fn in list(_hooks[event]):
        try:
            r = fn(**kwargs)
        except Exception as e:
            r = {"error": str(e)}
        out.append(r)
    return out


def list_hooks(event: Optional[str] = None) -> Dict[str, int]:
    if event:
        return {event: len(_hooks.get(event, []))}
    return {e: len(v) for e, v in _hooks.items()}


def clear_hooks(event: Optional[str] = None):
    if event:
        _hooks[event] = []
    else:
        for e in _VALID_EVENTS:
            _hooks[e] = []
