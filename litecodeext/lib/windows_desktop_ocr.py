"""通用桌面 OCR：合并词级坐标，拒绝不明确的点击目标。"""
import math
import re


def normalize_lines(result, scale=1):
    """Windows OCR 的行只有 words 带坐标，需取所有词的外接矩形。"""
    lines = []
    for line in result.get('lines', []):
        boxes = [word.get('bounding_rect', {}) for word in line.get('words', [])]
        boxes = [b for b in boxes if all(isinstance(b.get(k), (int, float)) and math.isfinite(b[k])
                                        for k in ('x', 'y', 'width', 'height'))
                 and b['width'] > 0 and b['height'] > 0]
        if not boxes:
            continue
        x, y = min(b['x'] for b in boxes), min(b['y'] for b in boxes)
        right = max(b['x'] + b['width'] for b in boxes)
        bottom = max(b['y'] + b['height'] for b in boxes)
        lines.append({'text': line.get('text', ''),
                      'rect': {'x': x * scale, 'y': y * scale,
                               'width': (right - x) * scale, 'height': (bottom - y) * scale}})
    return lines


def select_text(lines, target, width, height):
    """只允许唯一完整文本命中，不把多个相似按钮任取其一。"""
    norm = lambda s: re.sub(r'\s+', '', s).casefold()
    if not norm(target):
        raise ValueError('必须指定目标文字')
    matches = [line for line in lines if norm(line['text']) == norm(target)]
    if len(matches) != 1:
        raise ValueError(f'目标文字匹配 {len(matches)} 个，请根据最新截图指定唯一完整文字')
    r = matches[0]['rect']
    x, y = r['x'] + r['width'] / 2, r['y'] + r['height'] / 2
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError('OCR 坐标不在当前屏幕内')
    return matches[0], x, y


def capture_ocr(folder, name='screen.png'):
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
    language = next((s for s in languages if s.lower().startswith('zh')), languages[0] if languages else 'en')
    scale = max(1, max(image.size) / OcrEngine.max_image_dimension)
    ocr_image = image if scale == 1 else image.resize((int(image.width / scale), int(image.height / scale)))
    result = recognize_pil_sync(ocr_image, lang=language)
    return {'lines': normalize_lines(result, scale), 'width': image.width, 'height': image.height,
            'language': language, 'ocr_scale': scale,
            'origin': [monitor['left'], monitor['top']]}


def perform(action, folder, args):
    import ctypes
    import time
    import pyautogui
    ctypes.windll.user32.SetProcessDPIAware()
    before = capture_ocr(folder, 'before.png')
    result = {'ok': True, 'state': 'ocr_ready', **before, 'image': 'before.png'}
    if action == 'click_text':
        try:
            target, x, y = select_text(before['lines'], args.get('text', ''), before['width'], before['height'])
        except ValueError as exc:
            return {**result, 'ok': False, 'state': 'target_not_unique', 'error': str(exc)}
        pyautogui.click(x + before['origin'][0], y + before['origin'][1])
        time.sleep(1.5)
        after = capture_ocr(folder)
        result.update(state='clicked', target=target, click=[x, y], before_image='before.png',
                      image='screen.png', after_lines=after['lines'])
    # OCR 识别用的是全分辨率原图; 仅把回传给模型的图片按需压缩, 坐标换算仍以原始屏幕为准。
    if args.get('compress'):
        from windows_desktop_actions import compress_screenshot_file
        meta = compress_screenshot_file(folder / result['image'])
        result.update(screen_size=meta['screen_size'], image_size=meta['image_size'],
                      orig_bytes=meta['orig_bytes'], bytes=meta['bytes'])
    return result
