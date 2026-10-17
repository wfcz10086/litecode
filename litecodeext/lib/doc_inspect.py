"""服务端解析办公文件的真实内容, 供 windows_system inspect_file 回读核对用。

只用标准库 zipfile + xml.etree (容器里 openpyxl / python-docx 不保证存在):
  xlsx: 各 sheet 单元格 (地址/公式/缓存值, 共享字符串已解引用) + formulas 列表
  docx: 段落文本、段落数、字数 (中文按字计)
  pptx: 按 presentation.xml 的 sldIdLst 顺序输出每页文字
  txt/csv/md/json/log: utf-8 解码失败回退 gbk, 前 8000 字
  其他: 只给大小 / sha256 / 类型
所有入口都不抛异常: 损坏文件、zip 炸弹、XML 实体攻击一律返回 {'ok': False, 'error': ...}。
"""
import hashlib
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

MAX_MEMBER_BYTES = 20 * 1024 * 1024   # 单个压缩成员解压后上限
MAX_MEMBERS = 5000                    # 压缩包成员数上限
MAX_TOTAL_BYTES = 200 * 1024 * 1024   # 声明的解压总量上限
MAX_CELLS_PER_SHEET = 200             # 每个 sheet 输出的非空单元格上限
MAX_FORMULAS = 500
MAX_SLIDES = 200
TEXT_LIMIT = 8000

TEXT_EXTS = {'.txt', '.csv', '.tsv', '.md', '.json', '.log'}
XLSX_EXTS = {'.xlsx', '.xlsm'}

NS = {
    'm': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'rel': 'http://schemas.openxmlformats.org/package/2006/relationships',
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
}
_R_ID = '{%s}id' % NS['r']


class InspectError(ValueError):
    """可预期的解析失败 (损坏/超限/结构缺失), 消息直接给模型看。"""


def inspect(path):
    """解析本地文件, 返回 dict。永不抛异常。"""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {'ok': False, 'error': f'读取文件失败: {exc}'}
    base = {'name': path.name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    ext = path.suffix.lower()
    try:
        if ext in XLSX_EXTS:
            body = {'type': 'xlsx', **inspect_xlsx(path)}
        elif ext == '.docx':
            body = {'type': 'docx', **inspect_docx(path)}
        elif ext == '.pptx':
            body = {'type': 'pptx', **inspect_pptx(path)}
        elif ext in TEXT_EXTS:
            body = {'type': 'text', **inspect_text(data)}
        else:
            body = {'type': ext.lstrip('.') or 'unknown', 'parsed': False,
                    'note': '该类型不做内容解析, 仅返回大小与 sha256; 如需核对内容请导出为 xlsx/docx/pptx/txt/csv 再回读'}
        return {'ok': True, **base, **body}
    except InspectError as exc:
        return {'ok': False, **base, 'error': str(exc)}
    except zipfile.BadZipFile as exc:
        return {'ok': False, **base, 'error': f'文件不是有效的 Office 压缩包 (可能已损坏或未保存完成): {exc}'}
    except ET.ParseError as exc:
        return {'ok': False, **base, 'error': f'文档 XML 损坏, 无法解析: {exc}'}
    except Exception as exc:   # 兜底: 不把异常栈抛给主循环
        return {'ok': False, **base, 'error': f'解析失败 {type(exc).__name__}: {exc}'}


# ---------------- zip 安全读取 ----------------

def _open_zip(path):
    zf = zipfile.ZipFile(path)
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        zf.close()
        raise InspectError(f'压缩包成员数 {len(infos)} 超过上限 {MAX_MEMBERS}, 拒绝解析 (疑似 zip 炸弹)')
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        zf.close()
        raise InspectError('压缩包声明的解压总量过大, 拒绝解析 (疑似 zip 炸弹)')
    return zf


def _read(zf, name, required=True):
    try:
        info = zf.getinfo(name)
    except KeyError:
        if required:
            raise InspectError(f'文档缺少必需部件 {name}, 文件可能已损坏')
        return None
    if info.file_size > MAX_MEMBER_BYTES:
        raise InspectError(f'部件 {name} 解压后超过 {MAX_MEMBER_BYTES // 1024 // 1024}MB 上限, 拒绝解析 (疑似 zip 炸弹)')
    with zf.open(info) as handle:          # 头部声明的大小可以伪造, 实际读取再限一次
        data = handle.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:
        raise InspectError(f'部件 {name} 解压后超过上限, 拒绝解析 (疑似 zip 炸弹)')
    return data


def _xml(zf, name, required=True):
    data = _read(zf, name, required)
    if data is None:
        return None
    # OOXML 从不需要 DTD; 带 DOCTYPE/ENTITY 的一律拒绝, 挡实体膨胀攻击
    if b'<!DOCTYPE' in data[:4096].upper() or b'<!ENTITY' in data.upper():
        raise InspectError(f'部件 {name} 含 DTD/实体声明, 拒绝解析')
    return ET.fromstring(data)


def _rels(zf, part):
    """读取 part 的 _rels, 返回 {rId: 规范化后的包内路径}。"""
    folder, fname = posixpath.split(part)
    root = _xml(zf, posixpath.join(folder, '_rels', fname + '.rels'), required=False)
    if root is None:
        return {}
    out = {}
    for rel in root.findall('rel:Relationship', NS):
        target = rel.get('Target', '')
        if rel.get('TargetMode') == 'External' or not target:
            continue
        full = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get('Id')] = full
    return out


# ---------------- xlsx ----------------

def _num(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return text
    if value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value


def _si_text(si):
    """共享字符串项: 直接 <t> 或富文本 <r><t>; 跳过拼音 <rPh>。"""
    parts = []
    for child in si:
        tag = child.tag.rsplit('}', 1)[-1]
        if tag == 't':
            parts.append(child.text or '')
        elif tag == 'r':
            parts.extend(t.text or '' for t in child.findall('m:t', NS))
    return ''.join(parts)


def _col_letters(index):
    letters = ''
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def inspect_xlsx(path):
    with _open_zip(path) as zf:
        workbook = _xml(zf, 'xl/workbook.xml')
        rels = _rels(zf, 'xl/workbook.xml')
        shared_root = _xml(zf, 'xl/sharedStrings.xml', required=False)
        shared = [_si_text(si) for si in shared_root.findall('m:si', NS)] if shared_root is not None else []
        sheets, formulas, warnings = [], [], []
        for index, sheet in enumerate(workbook.findall('m:sheets/m:sheet', NS)):
            name = sheet.get('name', f'Sheet{index + 1}')
            part = rels.get(sheet.get(_R_ID)) or f'xl/worksheets/sheet{index + 1}.xml'
            root = _xml(zf, part, required=False)
            if root is None:
                warnings.append(f'sheet「{name}」的部件 {part} 缺失')
                continue
            cells, total, shared_master = [], 0, {}
            for row_pos, row in enumerate(root.iter('{%s}row' % NS['m'])):
                row_no = row.get('r') or str(row_pos + 1)
                for col_pos, cell in enumerate(row.findall('m:c', NS)):
                    addr = cell.get('r') or f'{_col_letters(col_pos)}{row_no}'
                    kind = cell.get('t')
                    f_el = cell.find('m:f', NS)
                    v_el = cell.find('m:v', NS)
                    raw = v_el.text if v_el is not None else None
                    if kind == 's' and raw is not None:
                        try:
                            value = shared[int(raw)]
                        except (ValueError, IndexError):
                            value = None
                            warnings.append(f'{name}!{addr} 共享字符串索引 {raw} 越界')
                    elif kind == 'inlineStr':
                        is_el = cell.find('m:is', NS)
                        value = _si_text(is_el) if is_el is not None else None
                    elif kind == 'b':
                        value = raw == '1' if raw is not None else None
                    elif kind in ('str', 'e'):
                        value = raw
                    else:
                        value = _num(raw) if raw is not None else None
                    formula = None
                    if f_el is not None:
                        si = f_el.get('si')
                        if f_el.text:
                            formula = '=' + f_el.text
                            if f_el.get('t') == 'shared' and si is not None:
                                shared_master[si] = (addr, formula)
                        elif si is not None and si in shared_master:
                            formula = f'(共享公式, 同 {shared_master[si][0]}: {shared_master[si][1]})'
                        else:
                            formula = '(共享公式)'
                    if value is None and formula is None:
                        continue
                    total += 1
                    item = {'addr': addr, 'value': value}
                    if formula is not None:
                        item['formula'] = formula
                        if raw is None:
                            item['note'] = '公式无缓存值 (文件未重算保存)'
                        if len(formulas) < MAX_FORMULAS:
                            formulas.append({'sheet': name, 'addr': addr, 'formula': formula, 'value': value})
                    if len(cells) < MAX_CELLS_PER_SHEET:
                        cells.append(item)
            sheets.append({'name': name, 'non_empty_cells': total, 'cells': cells,
                           'truncated': total > len(cells)})
        if not sheets and not warnings:
            raise InspectError('工作簿里没有任何 sheet, 文件可能已损坏')
        out = {'sheet_count': len(sheets), 'sheets': sheets, 'formulas': formulas}
        if warnings:
            out['warnings'] = warnings[:20]
        return out


# ---------------- docx ----------------

_CJK = re.compile(r'[㐀-䶿一-鿿豈-﫿　-〿＀-￯]')
_WORD = re.compile(r"[A-Za-z0-9]+(?:['.\-][A-Za-z0-9]+)*")


def count_words(text):
    """字数: 中日文字符与全角标点各算 1, 连续的英文/数字算 1 个词 (与 WPS/Word 口径接近)。"""
    return len(_CJK.findall(text)) + len(_WORD.findall(_CJK.sub(' ', text)))


def _w_paragraph_text(p):
    parts = []
    for el in p.iter():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 't':
            parts.append(el.text or '')
        elif tag == 'tab':
            parts.append('\t')
        elif tag in ('br', 'cr'):
            parts.append('\n')
    return ''.join(parts)


def inspect_docx(path):
    with _open_zip(path) as zf:
        root = _xml(zf, 'word/document.xml')
    texts = [_w_paragraph_text(p) for p in root.iter('{%s}p' % NS['w'])]
    non_empty = [t for t in texts if t.strip()]
    full = '\n'.join(non_empty)
    shown, used = [], 0
    for text in non_empty:
        if used + len(text) > TEXT_LIMIT:
            shown.append(text[:max(0, TEXT_LIMIT - used)])
            break
        shown.append(text)
        used += len(text)
    return {'paragraph_count': len(texts), 'non_empty_paragraphs': len(non_empty),
            'word_count': count_words(full), 'char_count': len(re.sub(r'\s', '', full)),
            'paragraphs': shown, 'truncated': len(full) > TEXT_LIMIT}


# ---------------- pptx ----------------

def inspect_pptx(path):
    with _open_zip(path) as zf:
        pres = _xml(zf, 'ppt/presentation.xml')
        rels = _rels(zf, 'ppt/presentation.xml')
        slides = []
        ids = pres.findall('p:sldIdLst/p:sldId', NS)
        for order, sld in enumerate(ids[:MAX_SLIDES]):
            part = rels.get(sld.get(_R_ID))
            if not part:
                slides.append({'index': order + 1, 'error': '幻灯片关系缺失'})
                continue
            root = _xml(zf, part, required=False)
            if root is None:
                slides.append({'index': order + 1, 'part': part, 'error': '幻灯片部件缺失'})
                continue
            texts = []
            for para in root.iter('{%s}p' % NS['a']):
                text = ''.join(t.text or '' for t in para.iter('{%s}t' % NS['a']))
                if text.strip():
                    texts.append(text)
            slides.append({'index': order + 1, 'part': posixpath.basename(part), 'texts': texts})
    return {'slide_count': len(ids), 'slides': slides, 'truncated': len(ids) > MAX_SLIDES}


# ---------------- 纯文本 ----------------

def inspect_text(data):
    for encoding in ('utf-8-sig', 'gbk'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        encoding, text = 'utf-8(替换非法字节)', data.decode('utf-8', errors='replace')
    return {'encoding': 'utf-8' if encoding == 'utf-8-sig' else encoding,
            'chars': len(text), 'lines': text.count('\n') + (1 if text and not text.endswith('\n') else 0),
            'text': text[:TEXT_LIMIT], 'truncated': len(text) > TEXT_LIMIT}
