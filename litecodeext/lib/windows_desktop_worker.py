"""在 Windows 交互桌面执行一次任务；由控制端经计划任务启动。"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

# pythonw 从计划任务启动时，显式补齐 pywin32 的 DLL 搜索路径。
_dll_handles = []
if os.name == 'nt':
    # 在截图及鼠标库加载前统一到物理像素，适配 RDP 分辨率和显示缩放变化。
    if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        ctypes.windll.user32.SetProcessDPIAware()
    import site
    for package in site.getsitepackages() + [site.getusersitepackages()]:
        for relative in ('pywin32_system32', 'win32', 'Pythonwin'):
            directory = Path(package) / relative
            if directory.is_dir():
                _dll_handles.append(os.add_dll_directory(str(directory)))



def _launch_detached(command, cwd):
    """[2026-10-19] 微信也要脱离节点计划任务的作业对象, 否则代理自更新后旧任务一直 Running、节点离线 (同 launch_app)"""
    try:
        from windows_desktop_actions import detached_popen
    except ImportError:
        return subprocess.Popen(command, cwd=str(cwd))
    return detached_popen(command, cwd=str(cwd))

def desktop_ready():
    """锁屏或安全桌面不允许误报为可操作桌面。"""
    user = ctypes.windll.user32
    user.OpenInputDesktop.restype = ctypes.c_void_p
    handle = user.OpenInputDesktop(0, False, 0x0100)
    if not handle:
        return False
    try:
        return bool(user.SwitchDesktop(ctypes.c_void_p(handle)))
    finally:
        user.CloseDesktop(ctypes.c_void_p(handle))


def classify(texts):
    """只按明确界面证据报告登录状态，未知界面不猜成功。"""
    text = "\n".join(texts)
    if any(x in text for x in ("二维码已失效", "二维码已过期", "刷新二维码")):
        return "qr_expired"
    if any(x in text for x in ("手机上确认", "手机确认", "请在手机")):
        return "awaiting_confirmation"
    if any(x in text for x in ("扫码登录", "扫描二维码", "扫一扫", "二维码登录")):
        return "awaiting_scan"
    if "通讯录" in text and any(x in text for x in ("聊天", "会话")):
        return "logged_in"
    return "unknown"


WECHAT_EXES = ('weixin.exe', 'wechat.exe')
NOT_WECHAT = ('企业微信', 'wxwork', 'wecom')


def is_wechat_title(name):
    """个人微信窗口; "企业微信" 也含"微信"二字, 必须排除。"""
    lower = name.lower()
    return any(x in lower for x in ('微信', 'wechat', 'weixin')) and not any(x in lower for x in NOT_WECHAT)


def open_wechat():
    """从快捷方式及安装目录发现个人微信，不依赖固定用户名; 按目标程序名认定, 排除企业微信。
    微信已在托盘运行时再次启动程序会唤出已有窗口。"""
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    roots = [Path.home() / "Desktop", Path(os.environ['PUBLIC']) / "Desktop",
             Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs',
             Path(os.environ['PROGRAMDATA']) / 'Microsoft/Windows/Start Menu/Programs']
    for root in roots:
        for link in root.rglob('*.lnk'):
            if is_wechat_title(link.stem):
                shortcut = shell.CreateShortcut(str(link))
                if Path(shortcut.TargetPath).is_file() and Path(shortcut.TargetPath).name.lower() in WECHAT_EXES:
                    _launch_detached([shortcut.TargetPath], Path(shortcut.TargetPath).parent)
                    return
    for env in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        root = Path(os.environ.get(env, 'C:/'))
        for relative in ('Tencent/Weixin/Weixin.exe', 'Tencent/WeChat/WeChat.exe'):
            exe = root / relative
            if exe.is_file():
                _launch_detached([str(exe)], exe.parent)
                return
    raise RuntimeError('未发现微信安装路径或快捷方式')


# 这些动作不操作交互桌面（系统信息/进程/服务/文件/受控 shell），锁屏时也应可执行。
SYSTEM_ACTIONS = ('system_info', 'processes', 'kill_process', 'services', 'run_powershell')
FILE_ACTIONS = ('list_dir', 'download', 'upload')
DESKTOP_GENERIC = ('open_url', 'hotkey', 'type_text', 'windows', 'focus_window', 'close_window',
                   'scroll', 'click', 'drag', 'clipboard', 'right_click', 'hover',
                   'launch_app', 'window_state', 'double_click')


def run(action, folder, args=None):
    args = args or {}
    if action in SYSTEM_ACTIONS:
        from windows_system_actions import perform
        return perform(action, folder, args)
    if action in FILE_ACTIONS:
        from windows_file_actions import perform
        return perform(action, folder, args)
    if not desktop_ready():
        return {'ok': False, 'state': 'desktop_unavailable', 'error': '桌面锁定或未登录，请先解锁 Windows'}
    if action in ('ocr', 'click_text'):
        from windows_desktop_ocr import perform
        return perform(action, folder, args)
    if action in DESKTOP_GENERIC:
        from windows_desktop_actions import perform
        return perform(action, folder, args)
    if action == 'type_secret':
        from windows_desktop_actions import perform
        return perform('type_text', folder, args)
    if action == 'wechat_login':
        open_wechat()
        time.sleep(5)
    texts = []
    if action in ('wechat_login', 'wechat_status'):
        import uiautomation as auto
        auto.SetGlobalSearchTimeout(2)
        for window in auto.GetRootControl().GetChildren():
            if is_wechat_title(window.Name):
                try:
                    window.SetFocus()
                    pending = [(window, 0)]
                    while pending and len(texts) < 300:
                        control, depth = pending.pop()
                        texts.append(control.Name)
                        if depth < 8:
                            pending.extend((child, depth + 1) for child in control.GetChildren())
                except Exception:
                    texts.append(window.Name)
    import mss
    image = folder / 'screen.png'
    with mss.MSS() as screen:
        screen.shot(output=str(image))
    result = {'ok': True, 'state': classify(texts) if action.startswith('wechat') else 'desktop_ready',
              'texts': texts[:100], 'image': 'screen.png'}
    if args.get('compress'):
        from windows_desktop_actions import compress_screenshot_file
        result.update(compress_screenshot_file(image))
    return result


if __name__ == '__main__':
    folder = Path(sys.argv[2])
    try:
        request = json.loads((folder / 'request.json').read_text(encoding='utf-8')) if (folder / 'request.json').exists() else {}
        result = run(sys.argv[1], folder, request)
    except Exception as exc:
        result = {'ok': False, 'state': 'error', 'error': str(exc)}
    finally:
        (folder / 'request.json').unlink(missing_ok=True)
    temp = folder / 'result.tmp'
    temp.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    temp.replace(folder / 'result.json')
