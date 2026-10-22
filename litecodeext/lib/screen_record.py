"""lib/screen_record.py — 录屏模式 (Web 与 Windows 桌面版通用, 跑在 web_ui 进程里)。

A. 录 LiteCode 干活: 会话开了「⏺ 录屏」后, 每一轮自动录一段 mp4 —— 只录 LiteCode 这个对话窗口
   (用户要求: 只录它自己的窗口, 不录桌面)。单独一块虚拟屏 :98 上开一个浏览器窗口打开该会话, 消息/步骤实时滚动;
   不碰 agent 自己用的 :99 屏和浏览器。叠字: 时间 + 当前步骤 (从 SSE 的 task_exec 帧取)。
   结束后 remux 成 mp4 (+faststart), 放 WORKSPACE/recordings/<sid>/, 回复里写出路径 → 前端出视频卡片, 可播放/下载。
B. 用户手动录屏: 浏览器只录当前这个 LiteCode 标签页 (preferCurrentTab), MediaRecorder 录的 webm → transcode_to_mp4() 转成 mp4 (见 routers/record_router.py)。

一轮最长录 RECORD_MAX_S, 超时自动收尾; 浏览器用完即关 (单例约定)。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

UI_DISPLAY = ":98"
UI_SIZE = (1280, 900)          # 虚拟屏 = 浏览器窗口; 录制时裁掉浏览器顶栏, 只留页面
FPS = 10
RECORD_MAX_S = 2 * 3600
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
WEB_BASE = "http://127.0.0.1:18790"


def workspace() -> Path:
    try:
        from lib.config import WORKSPACE
        return Path(WORKSPACE)
    except Exception:
        return Path("/tmp/litecode_workspace")


def rec_dir(sid: str) -> Path:
    d = workspace() / "recordings" / sid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _display_up(disp: str) -> bool:
    """真连一下 X 套接字: 只看文件在不在不行 —— 重启后 /tmp 里会留下死套接字和锁
    (WSL 桌面版 2026-10-21 实机: 判成"已开", ffmpeg 打不开 :98, 整轮没录上)。"""
    import socket
    n = disp.lstrip(":")
    path = f"/tmp/.X11-unix/X{n}"
    if not Path(path).exists():
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def _ensure_ui_display() -> bool:
    """录屏专用虚拟屏 :98; 尺寸和当前设置不一致 (旧版本留下的) 就重建。"""
    size = f"{UI_SIZE[0]}x{UI_SIZE[1]}"
    mark = Path("/tmp/.lc_rec_display")
    if _display_up(UI_DISPLAY):
        if mark.exists() and mark.read_text().strip() == size:
            return True
        subprocess.run(["pkill", "-f", f"Xvfb {UI_DISPLAY} -screen"], stdout=subprocess.DEVNULL)
        for _ in range(30):
            if not _display_up(UI_DISPLAY):
                break
            time.sleep(0.1)
        Path(f"/tmp/.X11-unix/X{UI_DISPLAY.lstrip(':')}").unlink(missing_ok=True)
        Path(f"/tmp/.X{UI_DISPLAY.lstrip(':')}-lock").unlink(missing_ok=True)
    else:   # 死套接字 / 锁文件残留会让新的 Xvfb 起不来
        Path(f"/tmp/.X11-unix/X{UI_DISPLAY.lstrip(':')}").unlink(missing_ok=True)
        Path(f"/tmp/.X{UI_DISPLAY.lstrip(':')}-lock").unlink(missing_ok=True)
    if not shutil.which("Xvfb"):
        return False
    subprocess.Popen(["Xvfb", UI_DISPLAY, "-screen", "0", f"{size}x24", "-nolisten", "tcp"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        if _display_up(UI_DISPLAY):
            mark.write_text(size)
            return True
        time.sleep(0.1)
    return False


def _esc(s: str) -> str:
    # ffmpeg drawtext textfile 内容不需要转义; 这里只去掉换行和过长
    # Noto CJK 里没有 ⚙ 等符号 (会显示成方框), 换成有的
    return " ".join(s.replace("⚙", "▶").split())[:90]


class Recorder:
    def __init__(self, sid: str):
        self.sid = sid
        self.t0 = time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = rec_dir(sid)
        self.raw = self.dir / f".{stamp}.mkv"
        self.mp4 = self.dir / f"{stamp}.mp4"
        self.caption = self.dir / f".{stamp}.caption.txt"
        self.caption.write_text("LiteCode")
        self.proc: Optional[subprocess.Popen] = None
        self._pw = None
        self._browser = None
        self.error = ""
        self.top = 0          # 浏览器顶栏 (标签栏 + 地址栏) 高度, 录制时裁掉

    async def start(self, cookie_token: str):
        if not shutil.which("ffmpeg"):
            self.error = "没有 ffmpeg"
            return
        if not _ensure_ui_display():
            self.error = "虚拟屏 :98 起不来"
            return
        await self._open_viewer(cookie_token)
        self._start_ffmpeg()

    async def _open_viewer(self, token: str):
        """虚拟屏 :98 上开一个浏览器窗口跟随这个会话 (不是 headless: 要被 x11grab 录到)。"""
        try:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            env = dict(os.environ, DISPLAY=UI_DISPLAY)
            self._browser = await self._pw.chromium.launch(
                headless=False, env=env,
                args=["--no-sandbox", f"--window-size={UI_SIZE[0]},{UI_SIZE[1]}", "--window-position=0,0",
                      "--disable-infobars", "--force-device-scale-factor=1"])
            ctx = await self._browser.new_context(no_viewport=True)   # 页面铺满窗口
            await ctx.add_cookies([{"name": "lc_auth", "value": token, "url": WEB_BASE}])
            await ctx.add_init_script(f"try{{localStorage.setItem('lc.cur','{self.sid}')}}catch(e){{}}")
            page = await ctx.new_page()
            await page.goto(WEB_BASE + "/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
            self.top = int(await page.evaluate("window.outerHeight - window.innerHeight")) or 0
            try:   # 确保切到本会话并接上进行中的轮次
                await page.evaluate("sid => { if (typeof swS === 'function' && window.cur !== sid) swS(sid); }", self.sid)
            except Exception:
                pass
        except Exception as e:
            self.error = f"录屏浏览器启动失败: {e}"

    def _start_ffmpeg(self):
        w = UI_SIZE[0]
        top = max(0, min(self.top, 200))
        h = (UI_SIZE[1] - top) // 2 * 2
        args = ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-draw_mouse", "0", "-framerate", str(FPS),
                "-video_size", f"{w}x{h}", "-i", f"{UI_DISPLAY}.0+0,{top}"]
        text = (f"drawtext=fontfile={FONT}:textfile={self.caption}:reload=1:x=16:y=h-th-16:fontsize=22:"
                f"fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8,"
                f"drawtext=fontfile={FONT}:text='%{{pts\\:hms}}':x=w-tw-16:y=h-th-16:fontsize=20:"
                f"fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=6")
        fc = f"[0:v]{text}[v]"
        args += ["-filter_complex", fc, "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "26", "-pix_fmt", "yuv420p", "-t", str(RECORD_MAX_S), str(self.raw)]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=open(self.dir / ".ffmpeg.log", "ab"))

    def set_caption(self, text: str):
        try:
            tmp = self.caption.with_suffix(".tmp")
            tmp.write_text(_esc(text) or "LiteCode")
            os.replace(tmp, self.caption)      # ffmpeg reload 读整文件, 原子替换防读到半截
        except Exception:
            pass

    async def stop(self) -> Optional[Path]:
        """收尾: ffmpeg 正常退出 → remux 成 mp4 (+faststart); 关浏览器。返回 mp4 路径 (失败 None)。"""
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
            except Exception:
                pass
            for _ in range(100):
                if self.proc.poll() is not None:
                    break
                await asyncio.sleep(0.1)
            if self.proc.poll() is None:
                self.proc.terminate()
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass
        out = None
        if self.raw.exists() and self.raw.stat().st_size > 0:
            r = await asyncio.to_thread(subprocess.run, ["ffmpeg", "-y", "-loglevel", "error", "-i", str(self.raw),
                                                         "-c", "copy", "-movflags", "+faststart", str(self.mp4)],
                                        capture_output=True)
            if r.returncode == 0 and self.mp4.exists():
                out = self.mp4
        for p in (self.raw, self.caption):
            try:
                p.unlink()
            except Exception:
                pass
        return out


# ── 会话级开关 + 进行中的录制 ──────────────────────────────────
_active: dict[str, Recorder] = {}


def is_on(session: dict) -> bool:
    return bool(session.get("record"))


async def begin(sid: str) -> Optional[Recorder]:
    """轮次开始时调用 (会话已开录屏)。"""
    if sid in _active:
        return _active[sid]
    try:
        from plugins.auth.core import _issue_token
        token = _issue_token()
    except Exception:
        token = ""
    rec = Recorder(sid)
    _active[sid] = rec
    await rec.start(token)
    return rec


def caption(sid: str, text: str):
    rec = _active.get(sid)
    if rec:
        rec.set_caption(text)


async def end(sid: str) -> Optional[Path]:
    rec = _active.pop(sid, None)
    if not rec:
        return None
    return await rec.stop()


def transcode_to_mp4(src: Path) -> Path:
    """用户录屏 (webm) → mp4 (H.264 + AAC, 网页/微信/剪辑软件都能直接用)。"""
    dst = src.with_suffix(".mp4")
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "23", "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)],
                       capture_output=True, timeout=1800)
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError(r.stderr.decode(errors="replace")[-300:])
    return dst
