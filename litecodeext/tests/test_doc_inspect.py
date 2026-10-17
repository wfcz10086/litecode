"""lib/doc_inspect.py 的离线回归 + windows_system inspect_file 服务端编排。

xlsx/docx/pptx 全部在 tmp_path 用 zipfile 手写最小 OOXML 现场构造 (不依赖 openpyxl/python-docx/python-pptx)。
真机案例: WPS 销售表 D5=SUM(D2:D4) 真实值 1964, 模型按截图汇报 4163 —— 回读必须给出 1964。
"""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]

from lib import doc_inspect as di  # noqa: E402

M = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
HDR = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _zip(path, members):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text if isinstance(text, bytes) else text.encode('utf-8'))
    return path


def _rels(pairs):
    body = ''.join(f'<Relationship Id="{rid}" Type="x" Target="{target}"/>' for rid, target in pairs)
    return f'{HDR}<Relationships xmlns="{PR}">{body}</Relationships>'


def make_xlsx(path):
    shared = ['产品', '数量', '单价', '金额', '苹果', '香蕉', '橙子']
    sst = ''.join(f'<si><t>{s}</t></si>' for s in shared)
    sst += '<si><r><t>合</t></r><r><t>计</t></r><rPh><t>ごうけい</t></rPh></si>'   # 富文本 + 拼音, idx 7
    sheet1_rows = [
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>',
        '<row r="2"><c r="A2" t="s"><v>4</v></c><c r="B2"><v>10</v></c><c r="C2"><v>58.5</v></c>'
        '<c r="D2"><f t="shared" ref="D2:D4" si="0">B2*C2</f><v>585</v></c></row>',
        '<row r="3"><c r="A3" t="s"><v>5</v></c><c r="B3"><v>20</v></c><c r="C3"><v>35.95</v></c>'
        '<c r="D3"><f t="shared" si="0"/><v>719</v></c></row>',
        '<row r="4"><c r="A4" t="s"><v>6</v></c><c r="B4"><v>12</v></c><c r="C4"><v>55</v></c>'
        '<c r="D4"><f t="shared" si="0"/><v>660</v></c></row>',
        '<row r="5"><c r="A5" t="s"><v>7</v></c><c r="B5"/><c r="D5"><f>SUM(D2:D4)</f><v>1964</v></c></row>',
    ]
    sheet2 = ('<row r="1"><c r="A1" t="inlineStr"><is><t>总计</t></is></c>'
              '<c r="B1"><f>销售!D5</f><v>1964</v></c><c r="C1" t="b"><v>1</v></c>'
              '<c r="D1"><f>B1*2</f></c></row>')
    return _zip(path, {
        '[Content_Types].xml': f'{HDR}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        'xl/workbook.xml': (f'{HDR}<workbook xmlns="{M}" xmlns:r="{R}"><sheets>'
                            '<sheet name="销售" sheetId="1" r:id="rId1"/><sheet name="汇总" sheetId="2" r:id="rId2"/>'
                            '</sheets></workbook>'),
        'xl/_rels/workbook.xml.rels': _rels([('rId2', '/xl/worksheets/sheet2.xml'), ('rId1', 'worksheets/sheet1.xml'),
                                             ('rId3', 'sharedStrings.xml')]),
        'xl/sharedStrings.xml': f'{HDR}<sst xmlns="{M}">{sst}</sst>',
        'xl/worksheets/sheet1.xml': f'{HDR}<worksheet xmlns="{M}"><sheetData>{"".join(sheet1_rows)}</sheetData></worksheet>',
        'xl/worksheets/sheet2.xml': f'{HDR}<worksheet xmlns="{M}"><sheetData>{sheet2}</sheetData></worksheet>',
    })


def make_docx(path, paragraphs):
    body = ''
    for para in paragraphs:
        runs = ''.join(f'<w:r><w:t xml:space="preserve">{t}</w:t></w:r>' for t in para)
        body += f'<w:p>{runs}</w:p>'
    return _zip(path, {'word/document.xml': f'{HDR}<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'})


def make_pptx(path, order):
    """order: sldIdLst 里依次引用的 slide 文件编号; 每个 slideN.xml 的文字是 "第N号文件" 标记。"""
    ids = ''.join(f'<p:sldId id="{256 + i}" r:id="rId{n}"/>' for i, n in enumerate(order))
    members = {
        'ppt/presentation.xml': f'{HDR}<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>',
        'ppt/_rels/presentation.xml.rels': _rels([(f'rId{n}', f'slides/slide{n}.xml') for n in sorted(order)]),
    }
    for n in order:
        members[f'ppt/slides/slide{n}.xml'] = (
            f'{HDR}<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
            f'<p:sp><p:txBody><a:p><a:r><a:t>标题{n}</a:t></a:r></a:p></p:txBody></p:sp>'
            f'<p:sp><p:txBody><a:p><a:r><a:t>正文</a:t></a:r><a:r><a:t>{n}号文件</a:t></a:r></a:p><a:p/></p:txBody></p:sp>'
            '</p:spTree></p:cSld></p:sld>')
    return _zip(path, members)


# ---------------- xlsx ----------------

def test_xlsx_formula_cached_value_shared_strings_multi_sheet(tmp_path):
    out = di.inspect(make_xlsx(tmp_path / '销售表.xlsx'))
    assert out['ok'] and out['type'] == 'xlsx' and out['sheet_count'] == 2
    sales, summary = out['sheets']
    assert [sales['name'], summary['name']] == ['销售', '汇总']
    cells = {c['addr']: c for c in sales['cells']}
    assert cells['A1']['value'] == '产品' and cells['A4']['value'] == '橙子'
    assert cells['A5']['value'] == '合计'                       # 富文本拼接, 拼音 <rPh> 被跳过
    assert cells['C2']['value'] == 58.5 and cells['B2']['value'] == 10
    assert cells['D5'] == {'addr': 'D5', 'value': 1964, 'formula': '=SUM(D2:D4)'}   # 真值, 不是 4163
    assert cells['D2']['formula'] == '=B2*C2'
    assert 'D2' in cells['D3']['formula'] and cells['D3']['value'] == 719        # 共享公式指回主单元格
    assert 'B5' not in cells                                     # 空单元格不输出
    s2 = {c['addr']: c for c in summary['cells']}
    assert s2['A1']['value'] == '总计' and s2['C1']['value'] is True
    assert s2['B1']['formula'] == '=销售!D5' and s2['B1']['value'] == 1964
    assert s2['D1']['value'] is None and '无缓存值' in s2['D1']['note']
    formulas = {(f['sheet'], f['addr']): f for f in out['formulas']}
    assert formulas[('销售', 'D5')] == {'sheet': '销售', 'addr': 'D5', 'formula': '=SUM(D2:D4)', 'value': 1964}
    assert len(formulas) == 6
    data = (tmp_path / '销售表.xlsx').read_bytes()
    assert out['size'] == len(data) and out['sha256'] == hashlib.sha256(data).hexdigest()


def test_xlsx_caps_200_cells_per_sheet(tmp_path):
    rows = ''.join(f'<row r="{i}"><c r="A{i}"><v>{i}</v></c><c r="B{i}"><v>{i}</v></c></row>' for i in range(1, 151))
    path = _zip(tmp_path / 'big.xlsx', {
        'xl/workbook.xml': f'{HDR}<workbook xmlns="{M}" xmlns:r="{R}"><sheets><sheet name="S" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': _rels([('rId1', 'worksheets/sheet1.xml')]),
        'xl/worksheets/sheet1.xml': f'{HDR}<worksheet xmlns="{M}"><sheetData>{rows}</sheetData></worksheet>',
    })
    sheet = di.inspect(path)['sheets'][0]
    assert len(sheet['cells']) == 200 and sheet['non_empty_cells'] == 300 and sheet['truncated'] is True


# ---------------- docx ----------------

def test_docx_paragraphs_and_word_count(tmp_path):
    path = make_docx(tmp_path / '报告.docx', [['第一季度', '销售报告'], [], ['合计 1964 元, hello world'], ['  ']])
    out = di.inspect(path)
    assert out['ok'] and out['type'] == 'docx'
    assert out['paragraph_count'] == 4 and out['non_empty_paragraphs'] == 2
    assert out['paragraphs'] == ['第一季度销售报告', '合计 1964 元, hello world']
    # 中文 8+3 字 (第一季度销售报告 / 合计 / 元) + 1964 / hello / world 3 词 + 全角无 → 14
    assert out['word_count'] == 8 + 3 + 3
    assert di.count_words('你好，世界') == 5                 # 全角标点按字计


def test_docx_missing_document_part(tmp_path):
    out = di.inspect(_zip(tmp_path / 'x.docx', {'word/other.xml': '<a/>'}))
    assert out['ok'] is False and 'word/document.xml' in out['error']


# ---------------- pptx ----------------

def test_pptx_follows_sldidlst_order_not_file_names(tmp_path):
    out = di.inspect(make_pptx(tmp_path / '演示.pptx', [3, 1, 2]))
    assert out['ok'] and out['type'] == 'pptx' and out['slide_count'] == 3
    assert [s['part'] for s in out['slides']] == ['slide3.xml', 'slide1.xml', 'slide2.xml']
    assert out['slides'][0] == {'index': 1, 'part': 'slide3.xml', 'texts': ['标题3', '正文3号文件']}


# ---------------- 纯文本 ----------------

def test_text_utf8_and_gbk_fallback(tmp_path):
    gbk = tmp_path / '数据.csv'
    gbk.write_bytes('产品,金额\n合计,1964\n'.encode('gbk'))
    out = di.inspect(gbk)
    assert out['ok'] and out['type'] == 'text' and out['encoding'] == 'gbk'
    assert '合计,1964' in out['text'] and out['lines'] == 2
    utf = tmp_path / 'a.md'
    utf.write_text('# 标题\n' + 'x' * 9000, encoding='utf-8')
    out = di.inspect(utf)
    assert out['encoding'] == 'utf-8' and len(out['text']) == 8000 and out['truncated'] is True


def test_other_type_returns_size_and_hash_only(tmp_path):
    path = tmp_path / 'a.pdf'
    path.write_bytes(b'%PDF-1.4 xxx')
    out = di.inspect(path)
    assert out['ok'] and out['type'] == 'pdf' and out['parsed'] is False and out['size'] == 12
    assert out['sha256'] == hashlib.sha256(b'%PDF-1.4 xxx').hexdigest()


# ---------------- 损坏 / 炸弹 ----------------

def test_corrupt_zip_returns_clear_error(tmp_path):
    for name in ('bad.xlsx', 'bad.docx', 'bad.pptx'):
        path = tmp_path / name
        path.write_bytes(b'PK\x03\x04 this is not really a zip')
        out = di.inspect(path)
        assert out['ok'] is False and '损坏' in out['error'] and 'Traceback' not in out['error']


def test_corrupt_xml_returns_clear_error(tmp_path):
    path = _zip(tmp_path / 'x.docx', {'word/document.xml': '<w:document><w:body><unclosed>'})
    out = di.inspect(path)
    assert out['ok'] is False and 'XML' in out['error']


def test_missing_file(tmp_path):
    out = di.inspect(tmp_path / 'nope.xlsx')
    assert out['ok'] is False and '读取文件失败' in out['error']


def test_zip_bomb_member_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(di, 'MAX_MEMBER_BYTES', 1024 * 1024)
    path = _zip(tmp_path / 'bomb.docx', {'word/document.xml': b'<a>' + b'0' * (2 * 1024 * 1024) + b'</a>'})
    assert path.stat().st_size < 64 * 1024                    # 高压缩比
    out = di.inspect(path)
    assert out['ok'] is False and 'zip 炸弹' in out['error']


def test_zip_bomb_lying_header_still_capped(tmp_path, monkeypatch):
    """成员头声明的 file_size 可伪造: 读取按声明大小截断 + CRC 校验, 结果是明确错误而不是吞下超量数据。"""
    monkeypatch.setattr(di, 'MAX_MEMBER_BYTES', 1000)
    path = _zip(tmp_path / 'lie.docx', {'word/document.xml': b'<a>' + b'0' * 5000 + b'</a>'})
    real = zipfile.ZipFile
    class Liar(real):
        def getinfo(self, name):
            info = super().getinfo(name)
            info.file_size = 10
            return info
    monkeypatch.setattr(di.zipfile, 'ZipFile', Liar)
    out = di.inspect(path)
    assert out['ok'] is False and ('zip 炸弹' in out['error'] or '损坏' in out['error'])


def test_zip_member_count_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(di, 'MAX_MEMBERS', 10)
    path = _zip(tmp_path / 'many.xlsx', {f'junk/{i}.xml': '<a/>' for i in range(20)})
    out = di.inspect(path)
    assert out['ok'] is False and '成员数' in out['error']


def test_entity_expansion_rejected(tmp_path):
    lol = (f'{HDR}<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
           f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&lol2;</w:t></w:r></w:p></w:body></w:document>')
    out = di.inspect(_zip(tmp_path / 'lol.docx', {'word/document.xml': lol}))
    assert out['ok'] is False and 'DTD' in out['error']


# ---------------- windows_system inspect_file 服务端编排 ----------------

def _enroll_online(nodes, versions, alias='ops'):
    created = nodes.create(alias, 'https://litecode.example.com')
    code = re.search(r"-Code '([^']+)'", base64.b64decode(created['command'].split()[-1]).decode('utf-16le'))[1]
    device = nodes.redeem(code, {'computer': 'win11', 'user': 'tester'})
    nodes.claim(device['id'], device['token'], versions)
    return device


def test_inspect_file_downloads_then_parses_on_server(tmp_path, monkeypatch):
    from lib import windows_desktop as wd
    from lib.windows_nodes import Nodes, component_versions
    _enroll_online(Nodes(tmp_path), component_versions())
    src = make_xlsx(tmp_path / 'src.xlsx')
    seen = {}

    def fake_download(nodes, alias, args, workspace, timeout, idempotent=False):
        seen.update(alias=alias, path=args['path'], idempotent=idempotent)
        target = Path(workspace) / 'uploads' / 'Node-Download-abcd1234-销售表.xlsx'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())
        return {'ok': True, 'state': 'download_done', 'path': str(target), 'name': '销售表.xlsx'}
    monkeypatch.setattr(wd, '_node_download', fake_download)
    out = wd.execute({'alias': 'ops', 'action': 'inspect_file',
                      'path': r'%USERPROFILE%\Desktop\销售表.xlsx'}, str(tmp_path))
    assert seen == {'alias': 'ops', 'path': r'%USERPROFILE%\Desktop\销售表.xlsx', 'idempotent': True}
    assert out['ok'] and out['action'] == 'inspect_file' and out['alias'] == 'ops'
    assert out['remote_path'] == r'%USERPROFILE%\Desktop\销售表.xlsx'
    assert Path(out['local_copy']).is_file()                     # 副本保留在 uploads/ 便于审计
    d5 = next(f for f in out['formulas'] if f['addr'] == 'D5')
    assert d5['value'] == 1964 and d5['formula'] == '=SUM(D2:D4)'


def test_inspect_file_via_handler_and_real_download_chain(tmp_path, monkeypatch):
    """走 windows_system 工具入口 + 真实 _node_download (只把节点任务打桩成返回分块)。"""
    import asyncio
    from lib import windows_desktop as wd
    from lib.windows_nodes import Nodes, component_versions
    import lib.config as cfg
    _enroll_online(Nodes(tmp_path), component_versions())
    data = make_docx(tmp_path / 'r.docx', [['回读', '核对']]).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    jobs = []

    def fake_job(nodes, alias, action, request, timeout, idempotent=False):
        jobs.append((action, request.get('index'), idempotent))
        if 'index' not in request:
            return {'ok': True, 'chunks': 1, 'sha256': digest, 'name': 'r.docx', 'size': len(data)}
        return {'ok': True, 'chunk': base64.b64encode(data).decode()}
    monkeypatch.setattr(wd, '_run_node_job', fake_job)
    monkeypatch.setattr(cfg, 'WORKSPACE', tmp_path)
    from core.tools.handlers.windows_system import h_windows_system
    text, _ = asyncio.run(h_windows_system('s1', {'alias': 'ops', 'action': 'inspect_file', 'path': r'C:\x\r.docx'}))
    assert not text.startswith('ERROR'), text
    out = json.loads(text)
    assert out['paragraphs'] == ['回读核对'] and out['type'] == 'docx'
    assert jobs == [('download', None, True), ('download', 0, True)]     # 只读, 允许网络错误重试


def test_inspect_file_rejected_on_legacy_agent_and_ssh_alias(tmp_path, monkeypatch):
    from lib import windows_desktop
    from lib.windows_desktop import execute
    from lib.windows_nodes import Nodes
    # 版本落后 = 正在自动更新: 等待超时后报"节点正在更新"; 不上报版本的 legacy 仍立即"代理版本过低"
    monkeypatch.setattr(windows_desktop, 'UPDATE_WAIT_SECONDS', 0)
    _enroll_online(Nodes(tmp_path), {'agent': '0' * 16, 'assets': '0' * 16})
    with pytest.raises(ValueError, match='节点正在更新'):
        execute({'alias': 'ops', 'action': 'inspect_file', 'path': 'C:\\a.xlsx'}, str(tmp_path))
    _enroll_online(Nodes(tmp_path), {}, alias='old')
    with pytest.raises(ValueError, match='代理版本过低'):
        execute({'alias': 'old', 'action': 'inspect_file', 'path': 'C:\\a.xlsx'}, str(tmp_path))
    with pytest.raises(ValueError, match='主动回连'):
        execute({'alias': 'ssh-only', 'action': 'inspect_file', 'path': 'C:\\a.xlsx'}, str(tmp_path))


def test_inspect_file_is_readonly_for_retry_and_loop_guard():
    from lib.windows_desktop import IDEMPOTENT_ACTIONS, NEW_ACTIONS
    from plugins.tools.guard import ToolLoopGuard
    assert 'inspect_file' in IDEMPOTENT_ACTIONS and 'inspect_file' in NEW_ACTIONS
    guard = ToolLoopGuard()
    args = {'alias': 'ops', 'action': 'inspect_file', 'path': 'C:\\a.xlsx'}
    assert all(not guard.check('windows_system', args)[0] for _ in range(6))
