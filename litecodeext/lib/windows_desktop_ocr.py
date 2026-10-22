"""桌面感知: OCR (合并词级坐标, 拒绝不明确的点击目标) 与 UI Automation 控件树的纯逻辑 (遍历/过滤/唯一定位)。
与 Windows 相关的取窗口/点击在 windows_desktop_actions.py; 这里不依赖 Windows 专属库, 可在 Linux 上测。"""
import math
import re
import time


def _valid_box(b):
    return all(isinstance(b.get(k), (int, float)) and math.isfinite(b[k]) for k in ('x', 'y', 'width', 'height')) \
        and b['width'] > 0 and b['height'] > 0


def _union(boxes, scale=1):
    x, y = min(b['x'] for b in boxes), min(b['y'] for b in boxes)
    right = max(b['x'] + b['width'] for b in boxes)
    bottom = max(b['y'] + b['height'] for b in boxes)
    return {'x': x * scale, 'y': y * scale, 'width': (right - x) * scale, 'height': (bottom - y) * scale}


def normalize_lines(result, scale=1, keep_words=False):
    """Windows OCR 的行只有 words 带坐标，需取所有词的外接矩形。
    keep_words=True 时在 '_words' 里保留词级框 (仅节点内部做子串定位用, 回传前由 perform 剥掉)。"""
    lines = []
    for line in result.get('lines', []):
        words = [word for word in line.get('words', []) if _valid_box(word.get('bounding_rect', {}))]
        if not words:
            continue
        item = {'text': line.get('text', ''), 'rect': _union([w['bounding_rect'] for w in words], scale)}
        if keep_words:
            item['_words'] = [{'text': str(w.get('text', '')), 'rect': _union([w['bounding_rect']], scale)} for w in words]
        lines.append(item)
    return lines


class TargetNotFound(ValueError):
    """OCR 里找不到目标文字 (0 个命中)。"""


class TargetNotUnique(ValueError):
    """目标文字命中多处, candidates 列出候选行。"""

    def __init__(self, message, candidates=()):
        super().__init__(message)
        self.candidates = list(candidates)


_norm = lambda s: re.sub(r'\s+', '', str(s)).casefold()


def _substring_rect(line, target):
    """子串命中时尽量只框住命中的那几个词, 长行 (工具栏/菜单行) 点行中心会点歪; 对不上词时退回整行框。"""
    words = line.get('_words') or []
    joined, spans = '', []
    for word in words:
        start = len(joined)
        joined += _norm(word['text'])
        spans.append((start, len(joined), word['rect']))
    begin = joined.find(target)
    if not words or begin < 0:
        return line['rect']
    end = begin + len(target)
    boxes = [rect for start, stop, rect in spans if start < end and stop > begin]
    return _union(boxes) if boxes else line['rect']


def select_text(lines, target, width, height):
    """只允许唯一命中，不把多个相似按钮任取其一。
    [2026-10-19] 以前只认整行完全相等, 0 个命中也报 target_not_unique, 模型以为"有多个"反复换写法空转
    (生产: 目标 "下 载"/"确 定", OCR 行是 "0 仪 表 盘 》 云 游")。现在: 先整行精确 (忽略空白, OCR 常在
    中文字间插空格), 无命中再按行内子串匹配; 0 个报 TargetNotFound, 多个报 TargetNotUnique 并列出候选。"""
    wanted = _norm(target)
    if not wanted:
        raise TargetNotFound('必须指定目标文字')
    matches = [(line, line['rect']) for line in lines if _norm(line['text']) == wanted]
    how = 'exact'
    if not matches and len(wanted) >= 2:   # 单字子串 (如 "保") 太容易误中, 不做子串猜测
        how = 'substring'
        for line in lines:
            count = _norm(line['text']).count(wanted)
            if count == 1:
                matches.append((line, _substring_rect(line, wanted)))
            elif count > 1:
                matches.extend([(line, line['rect'])] * count)   # 同一行出现多次同样算不唯一
    if not matches:
        sample = ' | '.join(line['text'] for line in lines[:15])
        raise TargetNotFound(f'目标文字匹配 0 个: 当前 OCR 里没有「{target}」; 请换成截图/OCR 中实际出现的文字, '
                             f'或改用 click 坐标点击。OCR 行示例: {sample[:300]}')
    if len(matches) > 1:
        candidates = []
        for line, _ in matches:
            if line['text'] not in candidates:
                candidates.append(line['text'])
        raise TargetNotUnique(f'目标文字匹配 {len(matches)} 个 ({how}), 请指定唯一完整文字或改用 click 坐标; 候选: '
                              + ' | '.join(candidates[:10]), candidates[:10])
    line, r = matches[0]
    x, y = r['x'] + r['width'] / 2, r['y'] + r['height'] / 2
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError('OCR 坐标不在当前屏幕内')
    return {k: v for k, v in line.items() if k != '_words'}, x, y


def _strip_words(lines):
    return [{k: v for k, v in line.items() if k != '_words'} for line in lines]


def verify_lines(lines, expected):
    """验收要求完整 OCR 行匹配；仅忽略空白，不把相似数字/字符当作正确。"""
    if not isinstance(expected, list) or not 1 <= len(expected) <= 10 or any(
        not isinstance(text, str) or not text.strip() or len(text) > 200 for text in expected
    ):
        raise ValueError('expected_texts 需要 1 到 10 个非空完整行，每行最多 200 字符')
    norm = lambda text: re.sub(r'\s+', '', text)
    observed = {norm(str(line.get('text') or '')) for line in lines}
    return [{'text': text, 'matched': norm(text) in observed} for text in expected]


UPSCALE_BELOW = 2000   # 长边不超过这个像素的屏幕先放大 2 倍再识别


def ocr_scale(size, max_dimension):
    """识别图相对原图的缩放系数 (坐标 = 识别坐标 × 系数)。
    [2026-10-21] 真机 1280x800、100% 缩放下 Windows OCR 对 9-12px 小字很差 ("新建文件夹"→"新 过 立 仁 荚"),
    放大 2 倍后同屏明显改善 (新建/WPS云盘/图库/下载/1个项目 均认对), 耗时 0.16s→0.2s。
    大屏仍按引擎上限缩小。"""
    longest = max(size)
    if longest > max_dimension:
        return longest / max_dimension
    if longest <= UPSCALE_BELOW and longest * 2 <= max_dimension:
        return 0.5
    return 1


def capture_ocr(folder, name='screen.png', language=None):
    import mss
    from PIL import Image
    from winocr import recognize_pil_sync
    from winrt.windows.media.ocr import OcrEngine
    with mss.MSS() as screen:
        monitor = screen.monitors[1]
        shot = screen.grab(monitor)
        image = Image.frombytes('RGB', shot.size, shot.rgb)
    image.save(folder / name)
    languages = [language.language_tag for language in OcrEngine.available_recognizer_languages]
    if language and language.lower() != 'auto':
        requested = language.lower()
        language = next((s for s in languages if s.lower() == requested), None) or next(
            (s for s in languages if s.lower().startswith(requested + '-')), None)
        if language is None:
            raise ValueError('未安装指定 OCR 语言，可用语言：' + ', '.join(languages))
    else:
        language = next((s for s in languages if s.lower().startswith('zh')), languages[0] if languages else 'en')
    scale = ocr_scale(image.size, OcrEngine.max_image_dimension)
    ocr_image = image if scale == 1 else image.resize((int(image.width / scale), int(image.height / scale)),
                                                      Image.LANCZOS if scale < 1 else Image.BICUBIC)
    result = recognize_pil_sync(ocr_image, lang=language)
    return {'lines': normalize_lines(result, scale, keep_words=True), 'width': image.width, 'height': image.height,
            'language': language, 'available_languages': languages, 'ocr_scale': scale,
            'origin': [monitor['left'], monitor['top']]}


def perform(action, folder, args):
    import ctypes
    import time
    import pyautogui
    # 常驻代理无人值守: 关闭 pyautogui 的屏幕角落急停 (鼠标停在角落时每个动作都抛 FailSafeException),
    # 否则上一次动作把鼠标留在角落后, 之后的点击会持续失败。急停改用撤销节点或停用计划任务。
    pyautogui.FAILSAFE = False
    ctypes.windll.user32.SetProcessDPIAware()
    options = {'language': args['ocr_language']} if args.get('ocr_language') else {}
    before = capture_ocr(folder, 'before.png', **options)
    result = {'ok': True, 'state': 'ocr_ready', **before, 'lines': _strip_words(before['lines']), 'image': 'before.png'}
    if action == 'ocr' and args.get('expected_texts') is not None:
        result['assertions'] = verify_lines(before['lines'], args['expected_texts'])
        result['ok'] = all(item['matched'] for item in result['assertions'])
        result['state'] = 'ocr_verified' if result['ok'] else 'ocr_mismatch'
        if not result['ok']:
            result['error'] = 'OCR 未精确识别所有验收文字，不能报告识别通过'
    if action == 'click_text':
        try:
            target, x, y = select_text(before['lines'], args.get('text', ''), before['width'], before['height'])
        except TargetNotFound as exc:
            return {**result, 'ok': False, 'state': 'target_not_found', 'error': str(exc)}
        except TargetNotUnique as exc:
            return {**result, 'ok': False, 'state': 'target_not_unique', 'error': str(exc),
                    'candidates': exc.candidates}
        except ValueError as exc:
            return {**result, 'ok': False, 'state': 'target_offscreen', 'error': str(exc)}
        pyautogui.click(x + before['origin'][0], y + before['origin'][1])
        time.sleep(1.5)
        after = capture_ocr(folder, **options)
        result.update(state='clicked', target=target, click=[x, y], before_image='before.png',
                      image='screen.png', after_lines=_strip_words(after['lines']))
    # OCR 识别用的是全分辨率原图; 仅把回传给模型的图片按需压缩, 坐标换算仍以原始屏幕为准。
    if args.get('compress'):
        from windows_desktop_actions import compress_screenshot_file
        meta = compress_screenshot_file(folder / result['image'])
        result.update(screen_size=meta['screen_size'], image_size=meta['image_size'],
                      orig_bytes=meta['orig_bytes'], bytes=meta['bytes'])
    return result


# [2026-10-21] 真机: Edge 标题是 "Example Domain - 用户配置 1 - Microsoft\u200b Edge" (含零宽空格),
# 模型照抄显示出来的标题永远匹配 0 个。比较前去掉零宽/方向控制字符, 各类空白统一成单个空格。
_INVISIBLE = dict.fromkeys(map(ord, '\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u202a\u202b\u202c\u202d\u202e'))


# ---- 可访问性树 (UI Automation): 按控件名操作, 不靠截图猜坐标 / OCR 认字 ----
# [2026-10-21] 真机 1280x800 下 Windows OCR 把"新建文件夹"认成"新 过 立 仁 荚", "notepad_zh.txt" 认成
# "notepad zh.txt", 不能看图的模型只能靠 OCR, 点不准。UIA 直接给出控件类型/名称/真实矩形, 对标
# Operator/Computer Use 的 accessibility tree 做法。
UIA_LIMIT = 200          # 返回给模型的控件条数上限
UIA_VISIT_LIMIT = 4000   # 遍历节点上限 (浏览器页面的树可能非常大)
UIA_TIME_LIMIT = 8.0
UIA_INTERACTIVE = {'Button', 'Edit', 'ComboBox', 'CheckBox', 'RadioButton', 'MenuItem', 'ListItem', 'TreeItem',
                   'TabItem', 'Hyperlink', 'SplitButton', 'Document', 'Slider', 'Spinner', 'DataItem', 'MenuBar'}


def _short_type(name):
    name = str(name or '')
    return name[:-7] if name.endswith('Control') else name


def _visible_text(value):
    """去掉私用区图标字符 (Segoe MDL2 字体图标)、零宽/方向控制字符 (真机 "新建\u200b\u200b文件夹") 和首尾空白。"""
    return ''.join(ch for ch in str(value or '').translate(_INVISIBLE) if not 0xE000 <= ord(ch) <= 0xF8FF).strip()


def clip_rect(rect, bounds):
    """控件矩形裁到可见范围 (窗口 ∩ 屏幕), 返回 [x, y, w, h]; 完全不可见返回 None。"""
    left, top = max(rect[0], bounds[0]), max(rect[1], bounds[1])
    right = min(rect[0] + rect[2], bounds[0] + bounds[2])
    bottom = min(rect[1] + rect[3], bounds[1] + bounds[3])
    if right - left < 2 or bottom - top < 2:
        return None
    return [left, top, right - left, bottom - top]


def collect_elements(root, bounds, text='', control_type='', limit=UIA_LIMIT,
                     visit_limit=UIA_VISIT_LIMIT, time_limit=UIA_TIME_LIMIT, clock=None):
    """深度优先遍历 UIA 树, 返回有名字或可交互、且在可见范围内的控件。
    root/子节点只需提供 ControlTypeName/Name/BoundingRectangle/GetChildren (便于离线测试)。"""
    clock = clock or time.monotonic
    start = clock()
    wanted = ' '.join(str(text or '').split()).casefold()
    wanted_type = _short_type(control_type).casefold()
    items, visited, truncated = [], 0, False
    stack = [(root, 0)]
    while stack:
        if visited >= visit_limit or clock() - start > time_limit:
            truncated = True
            break
        control, depth = stack.pop()
        visited += 1
        try:
            kind = _short_type(control.ControlTypeName)
            name = _visible_text(control.Name)
            r = control.BoundingRectangle
            rect = [r.left, r.top, r.right - r.left, r.bottom - r.top]
        except Exception:
            continue
        visible = clip_rect(rect, bounds) if rect[2] > 0 and rect[3] > 0 else None
        value = ''
        if kind in ('Edit', 'ComboBox', 'Document'):
            try:
                value = _visible_text(control.GetValuePattern().Value)[:120]
            except Exception:
                value = ''
        if visible and (name or kind in UIA_INTERACTIVE):
            haystack = ' '.join((name + ' ' + value).split()).casefold()
            if (not wanted or wanted in haystack) and (not wanted_type or kind.casefold() == wanted_type):
                item = {'type': kind, 'name': name[:120], 'rect': visible,
                        'center': [visible[0] + visible[2] // 2, visible[1] + visible[3] // 2]}
                if value:
                    item['value'] = value
                try:
                    if not control.IsEnabled:
                        item['enabled'] = False
                except Exception:
                    pass
                if len(items) >= limit:
                    truncated = True
                    break
                items.append(item)
        if depth < 40:
            try:
                children = control.GetChildren()
            except Exception:
                children = []
            stack.extend((child, depth + 1) for child in reversed(children))
    return items, truncated, visited


CHROMIUM_EXES = ('msedge.exe', 'chrome.exe')


def page_content_seen(items):
    """网页区域 (Document) 里是否已经出现了具体内容控件。"""
    docs = [i['rect'] for i in items if i['type'] == 'Document']
    inside = lambda c, r: r[0] <= c[0] < r[0] + r[2] and r[1] <= c[1] < r[1] + r[3]
    return not docs or any(i['type'] not in ('Document', 'Pane', 'Window', 'Group') and
                           any(inside(i['center'], r) for r in docs) for i in items)


def choose_element(items, text, control_type=''):
    """按控件名唯一定位: 先整名 (忽略空白/大小写) 精确, 再唯一子串; 多个命中列候选, 不替模型挑。"""
    norm = lambda v: ''.join(str(v or '').split()).casefold()
    wanted = norm(text)
    if not wanted:
        raise ValueError('click_element 需要 text (控件名称, 取自 ui_tree)')
    pool = [i for i in items if i.get('enabled', True)] or items
    exact = [i for i in pool if norm(i['name']) == wanted or (i.get('value') and norm(i['value']) == wanted)]
    # 子串只在 ≥2 个字符时用: 单个 "+" 会命中 "Bold (Ctrl+B)" 等一串 (真机 E2E)
    matches = exact or ([i for i in pool if wanted in norm(i['name'])] if len(wanted) >= 2 else [])
    # 同类型同名同矩形的嵌套控件 (真机: 资源管理器"地址栏" Edit 里套同名 Edit) 是同一个目标, 取最内层
    unique = {}
    for item in matches:
        unique[(item['type'], norm(item['name']), tuple(item.get('rect') or item.get('center') or ()))] = item
    matches = list(unique.values())
    if len(matches) == 1:
        return matches[0]
    if not matches:
        clickable = [i for i in items if i['name'] and i['type'] in UIA_INTERACTIVE] or [i for i in items if i['name']]
        sample = ' | '.join(dict.fromkeys(f"{i['type']}:{i['name']}" for i in clickable))
        raise ValueError(f'控件「{text}」匹配 0 个；控件名以 ui_tree 为准 (图标按钮通常是英文/中文全名, 如 Add New Tab)。'
                         f'可点控件: {sample[:600]}')
    listing = ' | '.join(f"{i['type']}:{i['name']} @{i['center']}" for i in matches[:8])
    raise ValueError(f'控件「{text}」匹配 {len(matches)} 个，请用 control_type 或更完整的名称区分，'
                     f'或按 center 坐标 click: {listing}')
