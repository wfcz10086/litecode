"""在交互桌面执行通用动作，窗口按完整标题唯一定位。"""
import ctypes
import os
from pathlib import Path
import time
import math
from urllib.parse import urlsplit

try:                                   # 节点上组件平铺下发, 按顶层名导入
    from windows_desktop_ocr import (_INVISIBLE, UIA_INTERACTIVE, CHROMIUM_EXES, collect_elements,
                                     choose_element, page_content_seen, clip_rect)
except ImportError:                    # 服务端/测试环境按包导入
    from lib.windows_desktop_ocr import (_INVISIBLE, UIA_INTERACTIVE, CHROMIUM_EXES, collect_elements,
                                         choose_element, page_content_seen, clip_rect)

HOTKEYS = {
    'enter': ['enter'], 'escape': ['esc'], 'tab': ['tab'], 'ctrl+l': ['ctrl', 'l'],
    'ctrl+a': ['ctrl', 'a'], 'ctrl+c': ['ctrl', 'c'], 'ctrl+s': ['ctrl', 's'], 'ctrl+w': ['ctrl', 'w'],
    'ctrl+j': ['ctrl', 'j'], 'alt+f4': ['alt', 'f4'], 'win+d': ['win', 'd'],
    'alt+left': ['alt', 'left'], 'ctrl+shift+n': ['ctrl', 'shift', 'n'],
    'ctrl+r': ['ctrl', 'r'], 'ctrl+shift+r': ['ctrl', 'shift', 'r'], 'f5': ['f5'],
    'ctrl+plus': ['ctrl', '+'], 'ctrl+minus': ['ctrl', '-'], 'ctrl+0': ['ctrl', '0'],
    'ctrl+f': ['ctrl', 'f'], 'ctrl+o': ['ctrl', 'o'], 'ctrl+n': ['ctrl', 'n'],
    'ctrl+shift+p': ['ctrl', 'shift', 'p'], 'ctrl+g': ['ctrl', 'g'],
    'f9': ['f9'], 'f10': ['f10'], 'f11': ['f11'],
    'shift+f11': ['shift', 'f11'], 'shift+f5': ['shift', 'f5'],
    'alt+tab': ['alt', 'tab'], 'ctrl+tab': ['ctrl', 'tab'], 'win+r': ['win', 'r'],
    'win+up': ['win', 'up'], 'win+space': ['win', 'space'], 'shift+tab': ['shift', 'tab'],
    'alt+shift': ['alt', 'shift'], 'home': ['home'], 'end': ['end'],
    'ctrl+home': ['ctrl', 'home'], 'ctrl+end': ['ctrl', 'end'],
    'ctrl+up': ['ctrl', 'up'], 'ctrl+down': ['ctrl', 'down'],
    'backspace': ['backspace'], 'delete': ['delete'], 'pageup': ['pageup'],
    'pagedown': ['pagedown'], 'up': ['up'], 'down': ['down'], 'left': ['left'], 'right': ['right'],
    # 常用编辑与窗口快捷键补充
    'win+e': ['win', 'e'], 'ctrl+shift+esc': ['ctrl', 'shift', 'esc'],
    'ctrl+z': ['ctrl', 'z'], 'ctrl+y': ['ctrl', 'y'], 'ctrl+x': ['ctrl', 'x'],
    'f2': ['f2'], 'alt+enter': ['alt', 'enter'],
    # 选择与格式 (真机 2026-10-17: 无 shift+home 只能整框重打)
    'ctrl+v': ['ctrl', 'v'], 'ctrl+b': ['ctrl', 'b'], 'ctrl+i': ['ctrl', 'i'], 'ctrl+u': ['ctrl', 'u'],
    'shift+home': ['shift', 'home'], 'shift+end': ['shift', 'end'],
    'shift+left': ['shift', 'left'], 'shift+right': ['shift', 'right'],
    'shift+up': ['shift', 'up'], 'shift+down': ['shift', 'down'],
    'ctrl+shift+left': ['ctrl', 'shift', 'left'], 'ctrl+shift+right': ['ctrl', 'shift', 'right'],
    'ctrl+shift+up': ['ctrl', 'shift', 'up'], 'ctrl+shift+down': ['ctrl', 'shift', 'down'],
    'ctrl+shift+home': ['ctrl', 'shift', 'home'], 'ctrl+shift+end': ['ctrl', 'shift', 'end'],
    # [2026-10-19] 生产日志里模型发 shift / ctrl+h / ctrl+shift+u / win+down 等被拒, 只能绕路空转; 补齐安全组合。
    'shift': ['shift'], 'alt': ['alt'], 'ctrl+space': ['ctrl', 'space'], 'space': ['space'],
    'ctrl+h': ['ctrl', 'h'], 'ctrl+shift+u': ['ctrl', 'shift', 'u'], 'ctrl+e': ['ctrl', 'e'], 'ctrl+k': ['ctrl', 'k'],
    'ctrl+p': ['ctrl', 'p'], 'ctrl+t': ['ctrl', 't'], 'ctrl+shift+t': ['ctrl', 'shift', 't'],
    'ctrl+shift+s': ['ctrl', 'shift', 's'], 'ctrl+pageup': ['ctrl', 'pageup'], 'ctrl+pagedown': ['ctrl', 'pagedown'],
    'ctrl+backspace': ['ctrl', 'backspace'], 'ctrl+delete': ['ctrl', 'delete'],
    'ctrl+enter': ['ctrl', 'enter'], 'shift+enter': ['shift', 'enter'],
    'win+down': ['win', 'down'], 'win+left': ['win', 'left'], 'win+right': ['win', 'right'],
    'f3': ['f3'], 'f6': ['f6'], 'f12': ['f12'],
    # 不加 win+l: 会锁屏使代理无法再操作桌面。
    # 不加单独 win / ctrl+esc / win+s / win+x: 开始菜单/搜索/高级菜单可直接启动命令行, 绕过 shell 能力开关 (同 win+r)。
    # 不加 shift+delete: 永久删除不进回收站。
}

# [2026-10-19] 模型常用的键名别名: 生产见 key:"Return" / "Esc" / "Control+H" 被拒。统一归一到 HOTKEYS 的键名。
_KEY_ALIASES = {
    'return': 'enter', 'esc': 'escape', 'control': 'ctrl', 'ctl': 'ctrl', 'windows': 'win', 'super': 'win',
    'meta': 'win', 'cmd': 'win', 'del': 'delete', 'bksp': 'backspace', 'pgup': 'pageup', 'page_up': 'pageup',
    'pgdn': 'pagedown', 'page_down': 'pagedown', 'arrowup': 'up', 'arrowdown': 'down', 'arrowleft': 'left',
    'arrowright': 'right', 'spacebar': 'space', 'option': 'alt', '=': 'plus', 'add': 'plus', 'subtract': 'minus', '-': 'minus',
}
_MODIFIER_ORDER = ('ctrl', 'alt', 'shift', 'win')


def normalize_hotkey(value):
    """把模型给的快捷键写法 (大小写/空格/别名/修饰键顺序) 归一成 HOTKEYS 里的键名; 不在白名单抛 ValueError。"""
    raw = str(value or '').strip().lower()
    if not raw:
        raise ValueError('hotkey 需要 keys 参数 (如 enter / ctrl+c)')
    if raw in HOTKEYS:
        return raw
    parts = [part.strip() for part in raw.replace(' ', '').split('+')]
    if raw.endswith('++'):   # ctrl++ → ctrl+plus
        parts = [p for p in parts if p] + ['plus']
    parts = [_KEY_ALIASES.get(part, part) for part in parts if part]
    if len(parts) == 1 and parts[0] in ('enter', 'escape'):
        return parts[0]
    modifiers = sorted({p for p in parts if p in _MODIFIER_ORDER}, key=_MODIFIER_ORDER.index)
    others = [p for p in parts if p not in _MODIFIER_ORDER]
    # 单独的修饰键 (如 shift 切中英文) 本身就是 HOTKEYS 里的键名; escape 在组合里写作 esc (ctrl+shift+esc)
    others = ['esc' if p == 'escape' else p for p in others]
    name = '+'.join(modifiers + others)
    if name in HOTKEYS:
        return name
    raise ValueError(f'不支持的快捷键: {value}; 可用: ' + ' '.join(sorted(HOTKEYS)))


def hotkey_arg(args):
    """[2026-10-19] 兼容模型把参数写成 key / hotkey (生产见 key:"Return"), 取第一个非空值。"""
    for name in ('keys', 'key', 'hotkey'):
        if args.get(name):
            return args[name]
    return ''

# launch_app 内置映射: 直接可执行文件名或 start 协议。cmd/powershell/终端属 shell 能力，
# 不在这里提供，需开启 shell 能力后用 run_powershell。
LAUNCH_APPS = {
    'notepad': ['notepad.exe'], 'explorer': ['explorer.exe'], 'calc': ['calc.exe'],
    'mspaint': ['mspaint.exe'], 'paint': ['mspaint.exe'],
    'edge': ['cmd', '/c', 'start', 'msedge'], 'chrome': ['cmd', '/c', 'start', 'chrome'],
    'vscode': ['Code.exe'], 'code': ['Code.exe'],
}
SHELL_APPS = {'cmd', 'powershell', 'pwsh', 'terminal', 'wt', 'cmd.exe', 'powershell.exe'}
# [2026-10-19] shell 能力关闭时 VS Code 也算命令行入口: 启动后 ctrl+shift+p → "新建终端" 或集成终端即可跑任意命令,
# 以前 launch_app vscode 在 shell 关闭时照样放行, 等于绕过开关。这两个名称仅在开启 shell 能力后可启动。
SHELL_REQUIRED_APPS = {'vscode', 'code'}
# shell 能力关闭时不可用的快捷键: win+r "运行"对话框; [2026-10-19] ctrl+shift+esc 任务管理器 "运行新任务" 同样能启动
# cmd/PowerShell, 以前在白名单里畅通无阻; win+x 高级菜单含"终端(管理员)" (不在白名单, 仍列入防止日后误加)。
RUN_DIALOG_HOTKEYS = {'win+r', 'ctrl+shift+esc', 'win+x'}
# [2026-10-19] 前台是这些进程时, shell 关闭下拒绝任何键盘输入 (hotkey/type_text/type_secret): 它们本身就是命令行,
# 或一两次按键即可开命令行 (VS Code 命令面板/集成终端、任务管理器"运行新任务"对话框)。按小写进程名匹配。
SHELL_HOST_PROCESSES = {'code.exe', 'windowsterminal.exe', 'wt.exe', 'cmd.exe', 'powershell.exe', 'pwsh.exe',
                        'conhost.exe', 'openconsole.exe', 'taskmgr.exe', 'powershell_ise.exe', 'wsl.exe', 'bash.exe'}
# 前台是这些进程时 (资源管理器地址栏 / 运行框都属 explorer.exe, 开始菜单搜索), 拒绝输入以命令行程序开头的文字。
LAUNCHER_PROCESSES = {'explorer.exe', 'searchhost.exe', 'searchapp.exe', 'startmenuexperiencehost.exe',
                      'searchui.exe', 'shellexperiencehost.exe'}
SHELL_COMMANDS = {'cmd', 'powershell', 'pwsh', 'wt', 'conhost', 'wsl', 'bash', 'powershell_ise', 'mshta', 'wscript',
                  'cscript', 'rundll32', 'regsvr32', 'msiexec', 'certutil', 'bitsadmin', 'schtasks', 'python', 'pythonw',
                  'py', 'node', 'ftp', 'forfiles', 'runas', 'taskmgr', 'code', 'mmc', 'control'}


def shell_guard(action, args, shell_allowed):
    """shell 能力关闭时, 桌面动作不得成为启动命令行解释器的旁路。服务端派发前与节点执行前各校验一次。
    shell_allowed 由节点代理按已认证轮询下发的能力写入请求 (服务端组装请求时从不带此字段)。"""
    if shell_allowed:
        return
    if action == 'launch_app':
        key = str(args.get('name', '')).strip().lower()
        if key in SHELL_REQUIRED_APPS:
            raise ValueError('该节点未开启 shell 能力，VS Code 自带终端/命令面板属命令行入口，需开启 shell 能力后再启动')
        if key not in LAUNCH_APPS:
            raise ValueError('该节点未开启 shell 能力，launch_app 只接受内置应用：'
                             + '/'.join(sorted(set(LAUNCH_APPS) - SHELL_REQUIRED_APPS)) + '；其他应用请用户开启 shell 能力后再启动')
    elif action == 'hotkey' and _guard_key(hotkey_arg(args)) in RUN_DIALOG_HOTKEYS:
        raise ValueError('该节点未开启 shell 能力，不可使用打开"运行"对话框/任务管理器的快捷键')


def looks_like_shell_command(text):
    """文字首个词 (去引号/路径/扩展名) 是命令行程序名即视为启动命令; 例如 'powershell -c ...'、'C:\\Windows\\cmd.exe'。"""
    stripped = str(text or '').strip().lstrip('"\'').strip()
    if not stripped:
        return False
    first = stripped.split()[0].strip('"\'')
    base = first.replace('/', '\\').rsplit('\\', 1)[-1].lower()
    for ext in ('.exe', '.com', '.bat', '.cmd', '.msc', '.lnk'):
        if base.endswith(ext):
            base = base[:-len(ext)]
    # %ComSpec% 等环境变量在地址栏/运行框会被展开成 cmd.exe 路径
    return base in SHELL_COMMANDS or base.startswith(('cmd', 'powershell', 'pwsh', '%comspec%'))


def input_guard(action, text, foreground, shell_allowed):
    """节点端键盘输入前的前台窗口校验 (shell 关闭时)。foreground 为前台进程名 (取不到为空)。

    [2026-10-19] 以前 shell 关闭时仍可: 资源管理器地址栏 (ctrl+l) / 运行框输入 "powershell" 回车,
    任务管理器"运行新任务"里输入 cmd, 已打开的 VS Code 里 ctrl+shift+p 开终端, 都等于绕过 shell 开关。
    限制: 只看前台进程, 鼠标点击菜单 (如 VS Code 的"终端"菜单) 本身拦不住, 但之后往终端里打字会被这里拒绝。"""
    if shell_allowed or action not in ('hotkey', 'type_text', 'type_secret'):
        return
    process = str(foreground or '').strip().lower()
    if process in SHELL_HOST_PROCESSES:
        raise ValueError(f'该节点未开启 shell 能力，前台是 {process}（命令行/可直接开命令行的程序），拒绝键盘输入；'
                         '请先切换到其他窗口，或请用户开启 shell 能力')
    if action != 'hotkey' and process in LAUNCHER_PROCESSES and looks_like_shell_command(text):
        raise ValueError('该节点未开启 shell 能力，不可在资源管理器地址栏/运行框/开始菜单输入命令行程序名')


def foreground_process():
    """当前前台窗口所属进程名 (小写); 取不到返回空串。"""
    try:
        from ctypes import wintypes
        import psutil
        user = ctypes.windll.user32
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(pid))
        return psutil.Process(pid.value).name().lower() if pid.value else ''
    except Exception:
        return ''


def _guard_key(value):
    # 先归一再比对: 否则 'R+Win' 之类写法归一后能执行, 却绕过 win+r 的 shell 能力校验
    try:
        return normalize_hotkey(value)
    except ValueError:
        return str(value or '').strip().lower()


WINDOW_STATES = {'maximize': 3, 'minimize': 6, 'restore': 9}


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('只接受不含账户密码的 HTTP/HTTPS 地址')
    return url


def normalize_title(value):
    return ' '.join(str(value or '').translate(_INVISIBLE).split())


def choose_window(windows, title, handle=None):
    if handle is not None and (isinstance(handle, bool) or not isinstance(handle, int) or handle <= 0):
        raise ValueError('窗口句柄必须来自最新窗口列表')
    wanted = normalize_title(title)
    matches = [window for window in windows if (not wanted or normalize_title(window['title']) == wanted)
               and (handle is None or window['handle'] == handle) and (wanted or handle is not None)]
    if len(matches) != 1:
        near = [w['title'] for w in windows if wanted and wanted.casefold() in normalize_title(w['title']).casefold()]
        hint = ('；包含该文字的窗口: ' + ' | '.join(near[:5]) + '（用完整标题或 window_handle）') if near else ''
        raise ValueError(f'完整窗口标题匹配 {len(matches)} 个，请先列出窗口再选择{hint}')
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
            rect = wintypes.RECT()
            user.GetWindowRect(hwnd, ctypes.byref(rect))
            items.append({'handle': int(hwnd), 'title': title.value, 'process': process,
                          'foreground': hwnd == user.GetForegroundWindow(),
                          'minimized': bool(user.IsIconic(hwnd)),
                          'rect': [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top]})
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
             '+': 0xbb, '-': 0xbd, 'f2': 0x71, 'f4': 0x73, 'f5': 0x74,
             'f9': 0x78, 'f10': 0x79, 'f11': 0x7a, 'up': 0x26, 'down': 0x28,
             'right': 0x27, 'home': 0x24, 'end': 0x23, 'backspace': 8, 'delete': 0x2e,
             'pageup': 0x21, 'pagedown': 0x22, 'space': 0x20, 'f3': 0x72, 'f6': 0x75, 'f12': 0x7b}
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


def compress_screenshot_file(path, long_edge=1600, quality=70):
    """把截图按长边 ≤ long_edge 缩放并转 JPEG 原地重写，坐标仍以原始屏幕为准。

    返回原始/压缩后的尺寸与字节数。screen_size 记录原始屏幕像素，供点击坐标换算；
    模型所见图片尺寸是 image_size，image_point 用两者比例映射，不受缩放影响。
    """
    from PIL import Image
    path = Path(path)
    orig_bytes = path.stat().st_size
    with Image.open(path) as image:
        image = image.convert('RGB')
        orig_w, orig_h = image.size
        longest = max(orig_w, orig_h)
        if longest > long_edge:
            ratio = long_edge / longest
            image = image.resize((max(1, int(orig_w * ratio)), max(1, int(orig_h * ratio))))
        new_w, new_h = image.size
        image.save(path, 'JPEG', quality=quality, optimize=True)
    return {'screen_size': [orig_w, orig_h], 'image_size': [new_w, new_h],
            'orig_bytes': orig_bytes, 'bytes': path.stat().st_size,
            'compressed': True}


def pyautogui_module():
    """导入 pyautogui 并关闭 FAILSAFE: 它在鼠标位于屏幕角落时对每个动作抛 FailSafeException,
    是给坐在电脑前的人用的急停。常驻代理无人值守, 上一个动作把鼠标停在角落 (如点了角落的关闭按钮)
    就会让之后所有动作持续失败, 所以关掉; 急停改用撤销节点或停用计划任务。"""
    import pyautogui
    pyautogui.FAILSAFE = False
    return pyautogui


def documents_dir():
    """当前用户的"文档"目录 (支持 OneDrive/组策略重定向); 取不到或不存在则回退用户主目录。"""
    home = Path.home()
    try:
        buffer = ctypes.create_unicode_buffer(260)
        # CSIDL_PERSONAL = 5: 已重定向时返回真实位置
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:
            candidate = Path(buffer.value)
            if candidate.is_dir():
                return candidate
    except Exception:
        pass
    candidate = home / 'Documents'
    return candidate if candidate.is_dir() else home


# Windows CreateProcess 标志 (数值写死, 非 Windows 的 subprocess 模块没有这些常量)
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def _elevated():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def launch_limited(command, cwd=None, run=None):
    """以当前桌面用户的**普通权限**启动 GUI 程序 (节点代理自身是管理员计划任务)。

    [2026-10-22 真机] 代理用 RunLevel Highest 运行 (静默装软件需要), 它直接 Popen 的 VS Code 等窗口带
    [Administrator]: 普通窗口拖不进文件、新建文件归管理员。做法: 注册一次性计划任务 (同一用户、交互式、
    RunLevel Limited) 跑一个 UTF-8 带 BOM 的 .ps1 (中文路径不乱码) 执行 Start-Process, 跑完立即删任务。
    成功返回 True; 任何一步失败返回 False, 由调用方退回原来的直接启动。"""
    import subprocess
    import tempfile
    import uuid
    run = run or subprocess.run
    name = 'LiteCode-Open-' + uuid.uuid4().hex[:12]
    tmp = Path(tempfile.gettempdir())
    script = tmp / (name + '.ps1')
    exe, rest = str(command[0]), [str(x) for x in command[1:]]
    body = ['$ErrorActionPreference = "Stop"', f'$args2 = @({", ".join(_ps_quote(x) for x in rest)})',
            f'$p = @{{ FilePath = {_ps_quote(exe)} }}',
            'if ($args2.Count) { $p.ArgumentList = $args2 }']
    if cwd:
        body.append(f'$p.WorkingDirectory = {_ps_quote(cwd)}')
    body += ['Start-Process @p', f'Remove-Item -LiteralPath {_ps_quote(script)} -Force -ErrorAction SilentlyContinue']
    try:
        for old in tmp.glob('LiteCode-Open-*.ps1'):   # 上次没删掉的残留
            if time.time() - old.stat().st_mtime > 600:
                old.unlink()
        script.write_text('\r\n'.join(body) + '\r\n', encoding='utf-8-sig')
        reg = ('$ErrorActionPreference = "Stop"; '
               '$u = [Security.Principal.WindowsIdentity]::GetCurrent().Name; '
               f'$a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ("-NoProfile -WindowStyle Hidden '
               f'-ExecutionPolicy Bypass -File `"" + {_ps_quote(script)} + "`""); '
               '$pr = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited; '
               f'Register-ScheduledTask -TaskName {_ps_quote(name)} -Action $a -Principal $pr -Force | Out-Null; '
               f'Start-ScheduledTask -TaskName {_ps_quote(name)}; '
               f'$t0 = Get-Date; while (((Get-Date) - $t0).TotalSeconds -lt 10 -and '
               f'(Get-ScheduledTask -TaskName {_ps_quote(name)}).State -eq "Running") {{ Start-Sleep -Milliseconds 200 }}; '
               f'$r = (Get-ScheduledTaskInfo -TaskName {_ps_quote(name)}).LastTaskResult; '
               f'Unregister-ScheduledTask -TaskName {_ps_quote(name)} -Confirm:$false; '
               'if ($r -ne 0) { exit 3 }')
        r = run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', reg],
                capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        try:
            if script.exists() and time.time() - script.stat().st_mtime > 15:
                script.unlink()
        except OSError:
            pass


def launch_gui(command, cwd=None):
    """启动给用户用的 GUI 程序: 代理是管理员时先降权 (launch_limited), 失败再直接启动。返回是否以管理员身份打开。"""
    if _elevated():
        if launch_limited(command, cwd):
            return False
        detached_popen(command, cwd=cwd)
        return True
    detached_popen(command, cwd=cwd)
    return False


def detached_popen(command, cwd=None, popen=None):
    """启动与节点代理脱离的 GUI 程序。

    [2026-10-19] 以前 Popen 只带 CREATE_NO_WINDOW: 启动的应用留在节点计划任务的作业对象 (job) 里,
    代理自更新后旧计划任务因仍有进程而保持 Running, 新实例起不来, 节点显示离线。现在加
    CREATE_BREAKAWAY_FROM_JOB 脱离作业 + 新进程组; 作业不允许脱离 (ERROR_ACCESS_DENIED=5) 时去掉该标志重试。"""
    import subprocess
    popen = popen or subprocess.Popen
    base = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        return popen(command, creationflags=base | CREATE_BREAKAWAY_FROM_JOB, shell=False, cwd=cwd)
    except OSError as exc:
        if getattr(exc, 'winerror', None) != 5 and exc.errno not in (13,):
            raise
        return popen(command, creationflags=base, shell=False, cwd=cwd)


def launch_app(args):
    """按名称启动应用。内置常用映射；shell 能力开启时未知名称才回退到开始菜单搜索（Win→输入→回车），
    关闭时只接受内置映射 (开始菜单搜索可打开任意程序, 含命令行)。结果靠截图验收。"""
    import subprocess
    name = str(args.get('name', '')).strip()
    if not name:
        raise ValueError('必须提供应用名称 name')
    key = name.lower()
    if key in SHELL_APPS:
        raise ValueError('cmd/PowerShell/终端窗口属于 shell 能力，请开启 shell 能力后用 run_powershell')
    shell_guard('launch_app', args, args.get('shell_allowed') is True)
    if key in LAUNCH_APPS:
        command = LAUNCH_APPS[key]
        if key in ('vscode', 'code'):
            # User Setup 只把 bin/code.cmd 加进 PATH，直接 Popen('Code.exe') 通常找不到。
            # 使用默认安装目录中的真正可执行文件，路径作为一个参数传递。
            candidates = []
            for variable, parts in (('LOCALAPPDATA', ('Programs', 'Microsoft VS Code', 'Code.exe')),
                                    ('ProgramFiles', ('Microsoft VS Code', 'Code.exe')),
                                    ('ProgramFiles(x86)', ('Microsoft VS Code', 'Code.exe'))):
                if os.environ.get(variable):
                    candidates.append(Path(os.environ[variable]).joinpath(*parts))
            executable = next((path for path in candidates if path.is_file()), None)
            if executable is None:
                raise ValueError('未发现 VS Code 默认安装目录，请先安装 VS Code，或使用已授权的系统通道检查自定义安装路径')
            command = [str(executable)]
        extra = args.get('args') or []
        if not isinstance(extra, list) or len(extra) > 8 or any(not isinstance(x, str) or len(x) > 400
                                                                or any(c in x for c in '\r\n') for x in extra):
            raise ValueError('args 须为字符串列表 (≤8 项, 每项 ≤400 字, 不含换行)')
        command = list(command) + extra
        # 工作目录设为"文档": 代理 cwd 曾是 System32, 记事本/WPS 的"另存为"会默认跳到那里
        elevated = launch_gui(command, cwd=str(documents_dir()))
        return {'method': 'mapping', 'launched': key, 'as_admin': elevated}
    # 兜底：开始菜单搜索。只输入应用名，不执行任意命令。
    if len(name) > 80 or any(c in name for c in '\r\n\t'):
        raise ValueError('应用名称不合法')
    send_keyboard(hotkey_events(['win']), interval=0.05)
    time.sleep(0.8)
    type_unicode(name)
    time.sleep(0.8)
    send_keyboard(hotkey_events(['enter']), interval=0.05)
    return {'method': 'start_menu', 'query': name}


def window_at(point):
    """坐标处的顶层窗口 (点之前取): 让模型核对点到的是不是目标窗口。
    [2026-10-21] 真机 E2E: 模型按截图坐标点"+"新建标签页, 实际点在记事本外的用户 LiteCode 窗口上。"""
    try:
        from ctypes import wintypes
        user = ctypes.windll.user32
        handle = user.WindowFromPoint(wintypes.POINT(int(point[0]), int(point[1])))
        root = user.GetAncestor(handle, 2) if handle else 0   # GA_ROOT
        found = next((w for w in list_windows() if w['handle'] == int(root or 0)), None)
        return {k: found[k] for k in ('handle', 'title', 'process')} if found else None
    except Exception:
        return None


def bring_to_front(handle):
    """把窗口切到前台并核对, 返回是否确实成为前台窗口。后台计划任务里起的进程受前台锁限制,
    SetForegroundWindow 常被拒: 先附接前台线程输入队列再切; 仍不行再按一下 alt 解除前台锁重试
    (alt 会落到原前台窗口, 可能点亮它的菜单栏, 所以只在第一次失败后才用)。"""
    user = ctypes.windll.user32
    target = ctypes.c_void_p(handle)
    user.ShowWindow(target, 9 if user.IsIconic(target) else 5)
    for attempt in range(2):
        if attempt:
            try:
                pyautogui_module().press('alt')
            except Exception:
                pass
        foreground = user.GetForegroundWindow()
        foreground_thread = user.GetWindowThreadProcessId(foreground, None)
        thread = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = user.AttachThreadInput(thread, foreground_thread, True)
        try:
            user.BringWindowToTop(target)
            user.SetForegroundWindow(target)
        finally:
            if attached:
                user.AttachThreadInput(thread, foreground_thread, False)
        time.sleep(0.2)
        if int(user.GetForegroundWindow() or 0) == int(handle):
            return True
    return False


def pick_new_window(before, windows, process_hint=''):
    """启动后新出现的可见顶层窗口; 优先进程名吻合的。before 是启动前的句柄集合。
    浏览器/资源管理器常复用已有进程开新窗口, 所以按句柄而不是按进程判断"新"。"""
    hint = str(process_hint or '').lower()
    fresh = [w for w in windows if w['handle'] not in before and w.get('title')
             and w.get('process', '').lower() not in ('textinputhost.exe', 'shellexperiencehost.exe')]
    preferred = [w for w in fresh if hint and w.get('process', '').lower().startswith(hint)]
    # 同时冒出主窗口和小浮窗时 (真机: Edge 的"翻译 俄语 页面?"浮窗排在主窗口前面) 取面积最大的
    area = lambda w: (w.get('rect') or [0, 0, 0, 0])[2] * (w.get('rect') or [0, 0, 0, 0])[3]
    return max(preferred or fresh, key=area) if (preferred or fresh) else None


def follow_new_window(before, process_hint='', wait=8.0, poll=0.4, lister=None):
    """[2026-10-21] 真机: 代理在后台计划任务里起的记事本落在 LiteCode 窗口后面, 结果却报 ok,
    模型接着 type_text 就把字打进了别的窗口。启动后等新窗口出现并切到前台, 把结果如实回报。"""
    lister = lister or list_windows
    deadline = time.monotonic() + wait
    window = None
    while time.monotonic() < deadline:
        window = pick_new_window(before, lister(), process_hint)
        if window:
            break
        time.sleep(poll)
    if not window:
        return {'new_window': None, 'focused': False,
                'warning': '未发现新窗口（程序可能复用了已有窗口、仍在启动或被拦截）；先 windows/截图确认，再 focus_window 后输入'}
    focused = bring_to_front(window['handle'])
    return {'new_window': window, 'focused': focused}


BROWSER_EXES = (('msedge', 'msedge.exe'), ('chrome', 'chrome.exe'), ('firefox', 'firefox.exe'))


def _app_path(exe):
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths' + '\\' + exe) as key:
                value = winreg.QueryValue(key, None)
                if value and Path(value.strip('"')).is_file():
                    return value.strip('"')
        except OSError:
            continue
    return None


def has_default_browser():
    """https 是否已有用户选定的默认程序。没有时 webbrowser.open 会弹"选择一个应用以打开此链接"对话框
    (真机 2026-10-21 复现), 动作却报成功。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\Shell\Associations'
                            r'\UrlAssociations\https\UserChoice') as key:
            return bool(winreg.QueryValueEx(key, 'ProgId')[0])
    except OSError:
        return False


def open_url(url, browser=''):
    """打开网址: 指定 browser 或没有默认浏览器关联时直接启动浏览器程序, 否则交给系统默认浏览器。
    返回 (方式, 进程名提示)。"""
    url = validate_url(url)
    wanted = str(browser or '').strip().lower()
    candidates = [item for item in BROWSER_EXES if not wanted or item[0] == wanted]
    if wanted and not candidates:
        raise ValueError('browser 只支持 msedge / chrome / firefox')
    if wanted or not has_default_browser():
        for name, exe in candidates:
            path = _app_path(exe)
            if path:
                launch_gui([path, url], cwd=str(Path(path).parent))
                return name, exe
        if wanted:
            raise ValueError(f'未发现 {wanted} 安装路径')
    import webbrowser
    if not webbrowser.open(url):
        raise RuntimeError('系统浏览器未接受打开请求')
    # 有默认关联时 Windows 仍可能因为"装了新浏览器"弹 OpenWith 选择框 (真机 2026-10-21: "Google Chrome 新建")。
    # 发现就关掉它改为直接启动已安装的浏览器, 不把选择框留给模型去猜。
    time.sleep(1.2)
    choosers = openwith_windows()
    if choosers:
        for handle in choosers:
            ctypes.windll.user32.PostMessageW(ctypes.c_void_p(handle), 0x0010, 0, 0)
        for name, exe in BROWSER_EXES:
            path = _app_path(exe)
            if path:
                launch_gui([path, url], cwd=str(Path(path).parent))
                return name + '(已关闭系统"选择应用"弹框改为直接启动)', exe
    return 'default', ''


def openwith_windows():
    """可见的"选择一个应用以打开此链接"弹框 (OpenWith.exe) 句柄; 它可能没有标题, windows 列表里看不到。"""
    from ctypes import wintypes
    try:
        import psutil
    except ImportError:
        return []
    user = ctypes.windll.user32
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        if user.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            try:
                if psutil.Process(pid.value).name().lower() == 'openwith.exe':
                    found.append(int(hwnd))
            except psutil.Error:
                pass
        return True
    user.EnumWindows(callback_type(visit), 0)
    return found


def screen_fingerprint(screen, monitor, size=(160, 100)):
    from PIL import Image
    shot = screen.grab(monitor)
    return Image.frombytes('RGB', shot.size, shot.rgb).convert('L').resize(size)


def frame_delta(a, b):
    """两帧缩略灰度图的平均像素差 (0-255)。光标闪烁等只改动几个像素, 远低于阈值。"""
    pa, pb = a.tobytes(), b.tobytes()
    return sum(abs(x - y) for x, y in zip(pa, pb)) / max(1, len(pa))


def wait_settled(min_wait=0.5, max_wait=3.0, interval=0.3, threshold=0.6, clock=None, grab=None, stable_needed=2):
    """等界面稳定再截图: 连续 stable_needed 个间隔画面几乎不变就返回, 最多等 max_wait 秒。
    以前固定 sleep 1.5 秒 (open_url/launch_app 4 秒): 慢的页面没加载完就截图, 快的白等。
    要求连续两个间隔都不变: 只比一次时, 点"保存"后对话框 0.8 秒才关, 0.6 秒就会误判已稳定。
    返回 (是否稳定, 实际等待毫秒)。抓帧失败时退化为固定等待。"""
    clock = clock or time.monotonic
    start = clock()
    time.sleep(min_wait)
    try:
        if grab is None:
            import mss
            screen_ctx = mss.MSS()
            screen = screen_ctx.__enter__()
            monitor = screen.monitors[1]
            grab = lambda: screen_fingerprint(screen, monitor)
        else:
            screen_ctx = None
        try:
            previous, calm = grab(), 0
            while clock() - start < max_wait:
                time.sleep(interval)
                current = grab()
                calm = calm + 1 if frame_delta(previous, current) <= threshold else 0
                if calm >= stable_needed:
                    return True, int((clock() - start) * 1000)
                previous = current
            return False, int((clock() - start) * 1000)
        finally:
            if screen_ctx is not None:
                screen_ctx.__exit__(None, None, None)
    except Exception:
        time.sleep(max(0.0, min(max_wait, 1.5) - (clock() - start)))
        return None, int((clock() - start) * 1000)


def set_window_state(args):
    state = args.get('window_state_mode')
    code = WINDOW_STATES.get(state)
    if code is None:
        raise ValueError('window_state 需要 maximize / minimize / restore')
    window = choose_window(list_windows(), args.get('title', ''), args.get('window_handle'))
    ctypes.windll.user32.ShowWindow(ctypes.c_void_p(window['handle']), code)
    return {'target': window, 'window_state_mode': state}


# ---- 可访问性树 (UI Automation): 纯逻辑 (遍历/过滤/定位) 在 windows_desktop_ocr.py 的感知部分 ----


def _uia_root(args):
    """目标窗口: 传了 title/window_handle 就按窗口列表唯一定位, 否则取当前前台窗口。"""
    import uiautomation as auto
    if args.get('title') or args.get('window_handle'):
        window = choose_window(list_windows(), args.get('title', ''), args.get('window_handle'))
        handle = window['handle']
    else:
        handle = int(ctypes.windll.user32.GetForegroundWindow() or 0)
        window = next((w for w in list_windows() if w['handle'] == handle), {'handle': handle, 'title': ''})
    if not handle:
        raise ValueError('没有前台窗口；先 windows 列出窗口再指定 title / window_handle')
    root = auto.ControlFromHandle(handle)
    if root is None:
        raise ValueError('该窗口不支持 UI Automation')
    return root, window


def _bounds(window):
    import mss
    with mss.MSS() as screen:
        monitor = screen.monitors[0]   # 整个虚拟桌面 (多显示器时含副屏)
        primary = screen.monitors[1]
    rect = window.get('rect') or [monitor['left'], monitor['top'], monitor['width'], monitor['height']]
    desk = [monitor['left'], monitor['top'], monitor['width'], monitor['height']]
    return clip_rect(rect, desk) or desk, primary


def ui_tree(args):
    root, window = _uia_root(args)
    bounds, primary = _bounds(window)
    items, truncated, visited = collect_elements(root, bounds, args.get('text', ''), args.get('control_type', ''))
    if str(window.get('process', '')).lower() in CHROMIUM_EXES and not page_content_seen(items):
        # Chromium 只在发现有 UIA 客户端后才懒加载网页可访问性树 (真机: 第一次只有浏览器外壳), 稍等再取一次
        time.sleep(0.8)
        items, truncated, visited = collect_elements(root, bounds, args.get('text', ''), args.get('control_type', ''))
    elements = compact_elements(items, primary)
    return {'target': {k: window.get(k) for k in ('handle', 'title', 'process') if k in window},
            'elements': elements, 'element_count': len(elements), 'truncated': truncated, 'visited': visited,
            'screen_size': [primary['width'], primary['height']],
            'coordinates': 'center 为屏幕物理像素 (与未压缩截图像素一致), size 为 [宽, 高]'}


def compact_elements(items, primary):
    """给模型看的控件列表: rect 换成 center+size, 去掉与上一项同名同中心的重复项 (按钮里再套的同名文字)。"""
    out, last = [], None
    for item in items:
        key = (item['name'], tuple(item['center']))
        if item['name'] and key == last:
            continue
        last = key
        element = {'type': item['type'], 'name': item['name'], 'center': item['center'], 'size': item['rect'][2:]}
        for extra in ('value', 'enabled'):
            if extra in item:
                element[extra] = item[extra]
        x, y = item['center']
        if not (primary['left'] <= x < primary['left'] + primary['width']
                and primary['top'] <= y < primary['top'] + primary['height']):
            element['offscreen'] = True   # 在副屏上, 主屏截图里看不到; click_element 仍可点
        out.append(element)
    return out


def click_element(args, pyautogui):
    root, window = _uia_root(args)
    bounds, _ = _bounds(window)
    items, _, _ = collect_elements(root, bounds, '', args.get('control_type', ''), limit=2000)
    element = choose_element(items, args.get('text', ''), args.get('control_type', ''))
    if window.get('handle') and not window.get('foreground'):
        bring_to_front(window['handle'])
    x, y = element['center']
    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.click()
    return {'target': {k: window.get(k) for k in ('handle', 'title', 'process') if k in window},
            'element': element, 'point': [x, y]}


def perform(action, folder, args):
    pyautogui = pyautogui_module()   # 已关闭 FAILSAFE, 原因见 pyautogui_module
    import mss
    ctypes.windll.user32.SetProcessDPIAware()
    info = {}
    if action in ('click', 'double_click', 'right_click', 'hover', 'drag'):
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
            pyautogui.click(button='right' if action == 'right_click' else 'left',
                            clicks=2 if action == 'double_click' else 1, interval=0.08)
        info.update(before_image='before.png', point=point, hit_window=window_at(point))
    elif action == 'open_url':
        before_titles = {w['handle']: w['title'] for w in list_windows()}
        method, exe = open_url(args.get('url', ''), args.get('browser', ''))
        info['browser'] = method
        followed = follow_new_window(set(before_titles), exe[:-4] if exe else '', wait=4.0)
        if not followed.get('new_window') and openwith_windows():
            info['warning'] = '系统弹出了"选择一个应用"对话框且未找到可直接启动的浏览器；请截图后在对话框里选择浏览器'
        if followed.get('new_window'):
            info.update(followed)
        else:
            # 浏览器已开着时多半在已有窗口开了新标签页: 只认标题变了的浏览器窗口 (同为 msedge 的
            # LiteCode 应用窗口等标题不变, 不会被误切到前台)
            names = {e for _, e in BROWSER_EXES}
            changed = [w for w in list_windows() if w.get('process', '').lower() in names
                       and w['handle'] in before_titles and before_titles[w['handle']] != w['title']]
            if changed:
                info.update(new_window=changed[0], focused=bring_to_front(changed[0]['handle']), reused_window=True)
            else:
                info['warning'] = followed['warning']
    elif action == 'hotkey':
        keys = HOTKEYS[normalize_hotkey(hotkey_arg(args))]
        shell_guard('hotkey', args, args.get('shell_allowed') is True)
        input_guard('hotkey', '', foreground_process(), args.get('shell_allowed') is True)
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
        input_guard('type_text', text, foreground_process(), args.get('shell_allowed') is True)
        method = args.get('input_method', 'clipboard')
        if args.get('sensitive'):
            # [2026-10-19] 凭据 (type_secret) 以前走剪贴板: 进 Win+V 历史/云剪贴板且覆盖用户剪贴板。
            # 用户决定一律直接 Unicode 键入, 即使旧服务端/请求仍写 clipboard。
            method = 'unicode'
        if method == 'unicode':
            type_unicode(text)
        elif method == 'clipboard':
            import pyperclip
            pyperclip.copy(text)
            send_keyboard(hotkey_events(['ctrl', 'v']), interval=0.08)
            time.sleep(0.3)
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
            bring_to_front(window['handle'])
        else:
            if not ctypes.windll.user32.PostMessageW(handle, 0x0010, 0, 0):
                error = ctypes.windll.kernel32.GetLastError()
                raise RuntimeError(f'Windows 拒绝关闭窗口，错误码 {error}；目标可能权限更高 (SSH 通道仅管理员授权的别名可用 elevated=true)')
        info['target'] = window
    elif action == 'launch_app':
        before = {w['handle'] for w in list_windows()}
        info.update(launch_app(args))
        hint = str(args.get('name', '')).strip().lower()
        hint = {'paint': 'mspaint', 'edge': 'msedge', 'vscode': 'code'}.get(hint, hint)
        info.update(follow_new_window(before, hint))
        title = normalize_title((info.get('new_window') or {}).get('title', ''))
        if hint == 'notepad' and title and not title.lower().startswith(('untitled', '无标题', 'notepad')):
            info['note'] = (f'Win11 记事本恢复了上次打开的标签页（{title}），当前不是空白文档；'
                            '要新建先 hotkey ctrl+n，别在旧文件里打字')
    elif action == 'ui_tree':
        info.update(ui_tree(args))
    elif action == 'click_element':
        info.update(click_element(args, pyautogui))
    elif action == 'window_state':
        info.update(set_window_state(args))
    elif action != 'windows':
        raise ValueError('不支持的桌面动作')
    if action not in ('windows', 'ui_tree', 'clipboard'):
        slow = action in ('open_url', 'launch_app')
        settled, waited = wait_settled(min_wait=1.0 if slow else 0.5, max_wait=8.0 if slow else 4.0)
        info.update(settled=settled, settle_ms=waited)
    with mss.MSS() as screen:
        screen.shot(output=str(folder / 'screen.png'))
    if args.get('compress'):
        info.update(compress_screenshot_file(folder / 'screen.png'))
    windows = list_windows()
    focused = action not in ('focus_window', 'window_state') or any(
        w['handle'] == info['target']['handle'] and (action == 'window_state' or w['foreground']) for w in windows)
    pending = action == 'close_window' and any(w['handle'] == info['target']['handle'] for w in windows)
    state = ('close_pending' if pending else 'closed' if action == 'close_window'
             else 'action_executed' if focused else 'focus_failed')
    if action in ('launch_app', 'open_url') and info.get('new_window') and not info.get('focused'):
        state = 'focus_failed'   # 程序起来了但没到前台: ok 仍为 true, 但先 focus_window 再输入
        info['warning'] = '新窗口未能切到前台，键盘输入会进别的窗口；先 focus_window 再操作'
    return {'ok': focused and not pending, 'state': state, 'image': 'screen.png',
            'windows': windows if action == 'windows' else compact_windows(windows), **info}


def compact_windows(windows):
    """动作结果里附带的窗口列表只留核对前台所需字段 (位置/大小只在 windows 动作里给)。
    [2026-10-21] 真机 E2E: 每步都带完整窗口列表 + 控件树, 56 步后上下文溢出。"""
    out = []
    for w in windows:
        item = {k: w[k] for k in ('handle', 'title', 'process', 'foreground') if k in w}
        if w.get('minimized'):
            item['minimized'] = True
        out.append(item)
    return out
