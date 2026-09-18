"""
desktop_control.py — 桌面操控 (LITE 版, 2026-05)
================================================
让 LiteCode AI 能在 noVNC 桌面里执行命令 / 截图 / 按键 / 输入.
依赖容器内已装: Xvfb (DISPLAY=:99) + fluxbox + chromium-browser + xdotool + scrot.

5 个函数都接受同步调用, 返回 dict {ok, msg, ...}.
- exec(cmd, bg)        : DISPLAY=:99 跑 shell, bg=True 立即返回
- screenshot(path)     : scrot 截屏到 path, 返回 {ok, path, bytes}
- key(keys)            : xdotool key  ctrl+t / Return / Tab
- type_text(text)      : xdotool type "..."
- find_image_on_screen : 保留旧接口 (未实现)
"""
from __future__ import annotations
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

_DISPLAY = os.environ.get("DISPLAY", ":99")


def _env() -> dict:
    """带 DISPLAY 的 env, 供 subprocess 用."""
    e = dict(os.environ)
    e["DISPLAY"] = _DISPLAY
    e.setdefault("XAUTHORITY", "/root/.Xauthority")
    return e


def exec(cmd: str, bg: bool = True, timeout: int = 20) -> dict:
    """在 noVNC 桌面 (DISPLAY=:99) 跑 shell 命令.
    bg=True : 立即返回 (chromium / firefox / xdg-open 这种长进程必须 bg)
    bg=False: 等待结束, 返回 stdout/stderr
    """
    if not cmd or not cmd.strip():
        return {"ok": False, "msg": "ERROR: empty command"}
    try:
        if bg:
            # 后台运行 — chromium / xdg-open 这种持续进程必须用
            full = f"nohup bash -lc {subprocess.list2cmdline([cmd])} > /tmp/desktop_exec.log 2>&1 &"
            subprocess.Popen(full, shell=True, env=_env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return {"ok": True, "msg": f"已在桌面后台启动: {cmd[:120]}", "bg": True}
        else:
            r = subprocess.run(["bash", "-lc", cmd], env=_env(),
                               capture_output=True, text=True, timeout=timeout)
            return {"ok": r.returncode == 0, "msg": (r.stdout or r.stderr or "").strip()[:2000],
                    "exit": r.returncode, "bg": False}
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": f"ERROR: 超时 {timeout}s"}
    except Exception as e:
        return {"ok": False, "msg": f"ERROR: {e}"}


def screenshot(path: Optional[str] = None) -> dict:
    """截当前 :99 桌面到 path. 默认存 /tmp/litecode_workspace/screenshots/desktop-<ts>.png"""
    try:
        if not path:
            d = Path("/tmp/litecode_workspace/screenshots")
            d.mkdir(parents=True, exist_ok=True)
            path = str(d / f"desktop-{int(time.time()*1000)}.png")
        # 优先 scrot (轻量, 容器已装)
        r = subprocess.run(["scrot", "-z", path], env=_env(),
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            # fallback: import (ImageMagick)
            r2 = subprocess.run(["import", "-window", "root", path], env=_env(),
                                capture_output=True, text=True, timeout=10)
            if r2.returncode != 0:
                return {"ok": False, "msg": f"截图失败: scrot={r.stderr[:200]} import={r2.stderr[:200]}"}
        p = Path(path)
        if not p.exists() or p.stat().st_size == 0:
            return {"ok": False, "msg": f"截图生成失败: {path} 不存在或为空"}
        return {"ok": True, "msg": f"截图已存: {path}", "path": path, "bytes": p.stat().st_size}
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "ERROR: 截图超时 10s"}
    except Exception as e:
        return {"ok": False, "msg": f"ERROR: {e}"}


def key(keys: str) -> dict:
    """按键组合, xdotool 语法: ctrl+t / Return / Tab / alt+F4 / shift+End"""
    if not keys:
        return {"ok": False, "msg": "ERROR: keys 不能为空"}
    try:
        r = subprocess.run(["xdotool", "key", "--clearmodifiers", keys],
                           env=_env(), capture_output=True, text=True, timeout=5)
        return {"ok": r.returncode == 0, "msg": (r.stderr or f"按键: {keys}").strip()[:500]}
    except Exception as e:
        return {"ok": False, "msg": f"ERROR: {e}"}


def type_text(text: str, delay_ms: int = 12) -> dict:
    """模拟键盘输入文本. delay_ms 是每个字符间隔 (ms), 默认 12ms 偏稳定."""
    if not text:
        return {"ok": False, "msg": "ERROR: text 不能为空"}
    try:
        r = subprocess.run(["xdotool", "type", "--clearmodifiers",
                            "--delay", str(int(delay_ms)), "--", text],
                           env=_env(), capture_output=True, text=True, timeout=30)
        return {"ok": r.returncode == 0, "msg": (r.stderr or f"输入: {text[:60]}").strip()[:500]}
    except Exception as e:
        return {"ok": False, "msg": f"ERROR: {e}"}


# ── 旧接口保留 (不删, 防其他模块 import 报错) ──
def click(x: int, y: int, button: str = "left") -> dict:
    """鼠标点击 — LITE 版用 xdotool, 不依赖 pyautogui."""
    try:
        btn_map = {"left": 1, "middle": 2, "right": 3}
        b = btn_map.get(button, 1)
        # 移动 → 点击
        subprocess.run(["xdotool", "mousemove", str(int(x)), str(int(y))],
                       env=_env(), capture_output=True, timeout=3)
        r = subprocess.run(["xdotool", "click", str(b)],
                           env=_env(), capture_output=True, text=True, timeout=3)
        return {"ok": r.returncode == 0, "msg": f"click ({x},{y}) {button}"}
    except Exception as e:
        return {"ok": False, "msg": f"ERROR: {e}"}


def find_image_on_screen(template_path: str) -> dict:
    """暂不实现 (需要 opencv-python). 模型应改用 vision LLM 看 screenshot 决策."""
    return {"found": False, "x": 0, "y": 0, "confidence": 0.0,
            "msg": "find_image_on_screen 未实现, 请用 screenshot + vision LLM"}
