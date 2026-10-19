"""通用桌面 OCR：合并词级坐标，拒绝不明确的点击目标。"""
import math
import re


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
    scale = max(1, max(image.size) / OcrEngine.max_image_dimension)
    ocr_image = image if scale == 1 else image.resize((int(image.width / scale), int(image.height / scale)))
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
