"""在交互桌面执行通用动作，窗口按完整标题唯一定位。"""
import ctypes
from pathlib import Path
import time
import math
from urllib.parse import urlsplit

HOTKEYS = {
    'enter': ['enter'], 'escape': ['esc'], 'tab': ['tab'], 'ctrl+l': ['ctrl', 'l'],
    'ctrl+a': ['ctrl', 'a'], 'ctrl+c': ['ctrl', 'c'], 'ctrl+s': ['ctrl', 's'], 'ctrl+w': ['ctrl', 'w'],
    'ctrl+j': ['ctrl', 'j'], 'alt+f4': ['alt', 'f4'], 'win+d': ['win', 'd'],
    'alt+left': ['alt', 'left'], 'ctrl+shift+n': ['ctrl', 'shift', 'n'],
    'ctrl+r': ['ctrl', 'r'], 'ctrl+shift+r': ['ctrl', 'shift', 'r'], 'f5': ['f5'],
    'ctrl+plus': ['ctrl', '+'], 'ctrl+minus': ['ctrl', '-'], 'ctrl+0': ['ctrl', '0'],
    'ctrl+f': ['ctrl', 'f'], 'ctrl+o': ['ctrl', 'o'], 'ctrl+n': ['ctrl', 'n'],
    'alt+tab': ['alt', 'tab'], 'ctrl+tab': ['ctrl', 'tab'], 'win+r': ['win', 'r'],
    'win+up': ['win', 'up'], 'win+space': ['win', 'space'], 'shift+tab': ['shift', 'tab'],
    'alt+shift': ['alt', 'shift'], 'home': ['home'], 'end': ['end'],
    'ctrl+home': ['ctrl', 'home'], 'ctrl+end': ['ctrl', 'end'],
    'backspace': ['backspace'], 'delete': ['delete'], 'pageup': ['pageup'],
    'pagedown': ['pagedown'], 'up': ['up'], 'down': ['down'], 'left': ['left'], 'right': ['right'],
}


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('只接受不含账户密码的 HTTP/HTTPS 地址')
    return url


def choose_window(windows, title, handle=None):
    if handle is not None and (isinstance(handle, bool) or not isinstance(handle, int) or handle <= 0):
        raise ValueError('窗口句柄必须来自最新窗口列表')
    matches = [window for window in windows if (not title or window['title'] == title)
               and (handle is None or window['handle'] == handle) and (title or handle is not None)]
    if len(matches) != 1:
        raise ValueError(f'完整窗口标题匹配 {len(matches)} 个，请先列出窗口再选择')
    return matches[0]


def image_point(args, monitor, end=False):
    """将模型所见图片坐标换算为物理像素，拒绝分辨率变化后的旧定位。"""
    if list(args.get('screen_size', [])) != [monitor['width'], monitor['height']]:
        raise ValueError('屏幕尺寸已变化，请重新截图定位')
    width, height = args.get('image_width'), args.get('image_height')
    x, y = args.get('end_x' if end else 'x'), args.get('end_y' if end else 'y')
    values = (width, height, x, y)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('必须提供有效的图片尺寸和坐标')
    if width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
        raise ValueError('坐标必须位于图片内')
    if abs(width / height - monitor['width'] / monitor['height']) > 0.02:
        raise ValueError('图片宽高比不符，请使用完整截图定位')
    return (monitor['left'] + min(monitor['width'] - 1, int(x * monitor['width'] / width)),
            monitor['top'] + min(monitor['height'] - 1, int(y * monitor['height'] / height)))


def list_windows():
    from ctypes import wintypes
    import psutil
    user = ctypes.windll.user32
    items = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if user.IsWindowVisible(hwnd) and user.GetWindowTextLengthW(hwnd):
            title = ctypes.create_unicode_buffer(user.GetWindowTextLengthW(hwnd) + 1)
            user.GetWindowTextW(hwnd, title, len(title))
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                process = psutil.Process(pid.value).name()
            except psutil.Error:
                process = ''
            items.append({'handle': int(hwnd), 'title': title.value, 'process': process,
                          'foreground': hwnd == user.GetForegroundWindow()})
        return True
    user.EnumWindows(callback_type(visit), 0)
    return items


def unicode_events(text):
    """UTF-16 按下/抬起序列，直接输入文字，不经过拼音候选或剪贴板。"""
    encoded = text.encode('utf-16-le')
    return [(int.from_bytes(encoded[i:i + 2], 'little'), flags)
            for i in range(0, len(encoded), 2) for flags in (0x0004, 0x0006)]


def hotkey_events(keys):
    """组合键使用同一 SendInput 通道，避免旧 keybd_event 在部分界面失效。"""
    named = {'ctrl': 0x11, 'alt': 0x12, 'shift': 0x10, 'win': 0x5b,
             'enter': 0x0d, 'esc': 0x1b, 'tab': 9, 'left': 0x25,
             '+': 0xbb, '-': 0xbd, 'f4': 0x73, 'f5': 0x74, 'up': 0x26, 'down': 0x28,
             'right': 0x27, 'home': 0x24, 'end': 0x23, 'backspace': 8, 'delete': 0x2e,
             'pageup': 0x21, 'pagedown': 0x22, 'space': 0x20}
    codes = []
    for key in keys:
        if key == '+':
            codes.append(0x10)
        codes.append(named[key] if key in named else ord(key.upper()))
    return [(code, 0, 0) for code in codes] + [(code, 0, 2) for code in reversed(codes)]


def keyboard_packet(vk, unit, flags, map_scan):
    if vk:
        # Flutter 等界面用扫描码识别物理按键，scan=0 会导致 Ctrl+A 等失效。
        unit = map_scan(vk, 0)
        flags |= 0x0008
        if vk in (0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2d, 0x2e, 0x5b):
            flags |= 1
        vk = 0
    return vk, unit, flags


def send_keyboard(events, interval=0):
    from ctypes import wintypes
    class Keyboard(ctypes.Structure):
        _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class Mouse(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('data', wintypes.DWORD),
                    ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class Payload(ctypes.Union):
        _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]
    class Input(ctypes.Structure):
        _fields_ = [('kind', wintypes.DWORD), ('payload', Payload)]
    inputs = (Input * len(events))()
    for item, (vk, unit, flags) in zip(inputs, events):
        item.kind = 1
        vk, unit, flags = keyboard_packet(vk, unit, flags, ctypes.windll.user32.MapVirtualKeyW)
        item.payload.keyboard = Keyboard(vk, unit, flags, 0, 0)
    if interval:
        sent = 0
        try:
            for item in inputs:
                count = ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(Input))
                sent += count
                if count != 1:
                    break
                time.sleep(interval)
        finally:
            # 即便中途失败，也释放本批修饰键，避免影响用户后续输入。
            for item in inputs:
                if item.payload.keyboard.flags & 2:
                    ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(Input))
    else:
        sent = ctypes.windll.user32.SendInput(len(inputs), inputs, ctypes.sizeof(Input))
    if sent != len(inputs):
        raise RuntimeError('键盘输入未完整发送；请检查目标窗口焦点和权限，勿直接重发避免重复输入')


def wheel_events(amount):
    """每格一个 WHEEL_DELTA(120)。pyautogui.scroll 在 Windows 把刻度原样当增量 (10 = 1/12 格),
    浏览器会累加小增量, 但微信 4.x 等自绘界面不足一格直接忽略, 表现为"滚不动"。"""
    step = 120 if amount > 0 else -120
    return [step] * abs(amount)


def send_wheel(amount, interval=0.03):
    from ctypes import wintypes
    class Mouse(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('data', wintypes.DWORD),
                    ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class Keyboard(ctypes.Structure):
        _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]
    class Payload(ctypes.Union):
        _fields_ = [('mouse', Mouse), ('keyboard', Keyboard)]
    class Input(ctypes.Structure):
        _fields_ = [('kind', wintypes.DWORD), ('payload', Payload)]
    for delta in wheel_events(amount):
        item = Input(0, Payload(mouse=Mouse(0, 0, delta & 0xFFFFFFFF, 0x0800, 0, 0)))   # MOUSEEVENTF_WHEEL
        if ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(Input)) != 1:
            raise RuntimeError('滚轮输入未发送；请检查目标窗口和权限')
        time.sleep(interval)


def type_unicode(text):
    send_keyboard([(0, unit, flags) for unit, flags in unicode_events(text)])


def perform(action, folder, args):
    import pyautogui
    import mss
    ctypes.windll.user32.SetProcessDPIAware()
    info = {}
    if action in ('click', 'right_click', 'hover', 'drag'):
        with mss.MSS() as screen:
            monitor = screen.monitors[1]
            point = image_point(args, monitor)
            endpoint = image_point(args, monitor, end=True) if action == 'drag' else None
            screen.shot(output=str(folder / 'before.png'))
        pyautogui.moveTo(*point, duration=0.2)
        if endpoint:
            pyautogui.dragTo(*endpoint, duration=0.8, button='left')
        elif action == 'hover':
            time.sleep(0.6)   # 停留让悬浮菜单/提示出现, 返回截图里可见
        else:
            pyautogui.click(button='right' if action == 'right_click' else 'left')
        info.update(before_image='before.png', point=point)
    elif action == 'open_url':
        import webbrowser
        if not webbrowser.open(validate_url(args.get('url', ''))):
            raise RuntimeError('系统浏览器未接受打开请求')
    elif action == 'hotkey':
        keys = HOTKEYS.get(args.get('keys', '').lower())
        if not keys:
            raise ValueError('不支持的快捷键')
        send_keyboard(hotkey_events(keys), interval=0.08)
    elif action == 'clipboard':
        # 读剪贴板文字: 配合选中 + ctrl+c 拿到原文, 不靠截图转写
        import pyperclip
        text = pyperclip.paste() or ''
        info.update(text=text[:8000], truncated=len(text) > 8000)
    elif action == 'type_text':
        text = args.get('text', '')
        if not text or len(text) > 10000:
            raise ValueError('输入文字长度应为 1 到 10000 字符')
        method = args.get('input_method', 'clipboard')
        if method == 'unicode':
            type_unicode(text)
        elif method == 'clipboard':
            import pyperclip
            pyperclip.copy(text)
            try:
                send_keyboard(hotkey_events(['ctrl', 'v']), interval=0.08)
                time.sleep(0.3)
            finally:
                if args.get('sensitive'):
                    pyperclip.copy('')
        else:
            raise ValueError('不支持的输入方式')
    elif action == 'scroll':
        amount = int(args.get('amount', -5))
        if not -20 <= amount <= 20 or amount == 0:
            raise ValueError('滚动量应为 -20 到 20 之间的非零整数')
        from ctypes import wintypes
        rectangle = wintypes.RECT()
        handle = ctypes.windll.user32.GetForegroundWindow()
        if not ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rectangle)):
            raise RuntimeError('无法确定当前窗口滚动区域')
        if args.get('x') is not None or args.get('y') is not None:
            with mss.MSS() as screen:
                point = image_point(args, screen.monitors[1])
        else:
            point = ((rectangle.left + rectangle.right) // 2, (rectangle.top + rectangle.bottom) // 2)
        pyautogui.moveTo(*point)
        send_wheel(amount)
    elif action in ('focus_window', 'close_window'):
        window = choose_window(list_windows(), args.get('title', ''), args.get('window_handle'))
        handle = ctypes.c_void_p(window['handle'])
        if action == 'focus_window':
            # 解除前台切换限制，并在当前交互桌面附接前台输入线程。
            pyautogui.press('alt')
            ctypes.windll.user32.ShowWindow(handle, 9)
            foreground = ctypes.windll.user32.GetForegroundWindow()
            foreground_thread = ctypes.windll.user32.GetWindowThreadProcessId(foreground, None)
            thread = ctypes.windll.kernel32.GetCurrentThreadId()
            attached = ctypes.windll.user32.AttachThreadInput(thread, foreground_thread, True)
            try:
                ctypes.windll.user32.BringWindowToTop(handle)
                ctypes.windll.user32.SetForegroundWindow(handle)
            finally:
                if attached:
                    ctypes.windll.user32.AttachThreadInput(thread, foreground_thread, False)
        else:
            if not ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0):
                error = ctypes.windll.kernel32.GetLastError()
                raise RuntimeError(f'Windows 拒绝关闭窗口，错误码 {error}；目标可能权限更高，可在已授权时使用 elevated=true')
        info['target'] = window
    elif action != 'windows':
        raise ValueError('不支持的桌面动作')
    time.sleep(1.5 if action != 'open_url' else 4)
    with mss.MSS() as screen:
        screen.shot(output=str(folder / 'screen.png'))
    windows = list_windows()
    focused = action != 'focus_window' or any(w['handle'] == info['target']['handle'] and w['foreground'] for w in windows)
    pending = action == 'close_window' and any(w['handle'] == info['target']['handle'] for w in windows)
    state = 'close_pending' if pending else ('closed' if action == 'close_window' else ('action_executed' if focused else 'focus_failed'))
    return {'ok': focused and not pending, 'state': state, 'image': 'screen.png',
            'windows': windows, **info}
