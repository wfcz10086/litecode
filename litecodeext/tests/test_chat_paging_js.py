"""前端分段渲染的纯函数 (node 执行): 合并块全局下标 / 片段起点跳过半轮 / 指纹 / 片段前 user 基数。"""
from pathlib import Path
import shutil
import subprocess

import pytest

ASSETS = Path(__file__).resolve().parents[1] / 'web_assets'


def test_paging_pure_functions():
    node = shutil.which('node')
    if not node:
        pytest.skip('需要 Node.js')
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const listeners=[];
global.window=global;global.document={readyState:'complete',getElementById:()=>null,addEventListener:(...a)=>listeners.push(a)};
global.cur='s1';
vm.runInThisContext(fs.readFileSync(process.argv[1]+'/chat_paging.js','utf8'));
// 只取 chat_render.js 里的 _mergeConsecutiveAssistants
const src=fs.readFileSync(process.argv[1]+'/chat_render.js','utf8');
const a=src.indexOf('function _mergeConsecutiveAssistants'),b=src.indexOf('\nfunction renderMsgs');
vm.runInThisContext(src.slice(a,b));
const M=(role,content,extra={})=>({role,content,...extra});
// 1. 完整列表 (无 index): 下标按位置 + base
let out=_mergeConsecutiveAssistants([M('user','q0'),M('assistant','a1'),M('assistant','a2'),M('user','q3'),M('assistant','a4')]);
assert.deepEqual(out.map(m=>[m.role,m._i0,m._i1]),[['user',0,0],['assistant',1,2],['user',3,3],['assistant',4,4]]);
assert.equal(out[1]._key,'assistant|a2');           // 合并块指纹取末条 (重试按末条定位)
// 2. 片段 (带 index): 以 index 为准
out=_mergeConsecutiveAssistants([M('user','q',{index:940}),M('assistant','x',{index:941}),M('assistant','y',{index:942})]);
assert.deepEqual(out.map(m=>[m._i0,m._i1]),[[940,940],[941,942]]);
// 3. 不同 source 的 assistant 不合并
out=_mergeConsecutiveAssistants([M('assistant','a',{source:'wechat'}),M('assistant','b')]);
assert.equal(out.length,2);
// 4. 片段起点在半轮中间: 跳过开头的 assistant; 起点是 0 或片段无 user 时不跳
assert.equal(_lcSkipLead([M('assistant','a'),M('assistant','b'),M('user','q')],10),2);
assert.equal(_lcSkipLead([M('assistant','a'),M('user','q')],0),0);
assert.equal(_lcSkipLead([M('assistant','a'),M('assistant','b')],10),0);
// 5. 指纹: 忽略空白, 多模态取 text 部分, 截 40 字
assert.equal(_lcKey(M('user',' 继 续\n')),'user|继续');
assert.equal(_lcKey(M('user',[{type:'text',text:'看图'},{type:'image_url',image_url:{url:'x'}}])),'user|看图');
assert.equal(_lcKey(M('assistant','x'.repeat(100))).length,'assistant|'.length+40);
// 6. 片段之前的 user 数 (DOM 序号 → 全局序号)
const full=[M('user','0'),M('assistant','1'),M('user','2'),M('assistant','3'),M('user','4')];
_lcPage.sid='s1';_lcPage.start=4;assert.equal(_lcUserBase(full),2);
_lcPage.sid='other';assert.equal(_lcUserBase(full),0);     // 别的会话的片段状态不算数
// 7. 缩略图 URL: 只给 uploads 内栅格图
assert.ok(_lcThumbUrl('/tmp/litecode_workspace/uploads/a.png',480).startsWith('/api/thumb?path=%2Ftmp%2Flitecode_workspace%2Fuploads%2Fa.png&w='));
assert.equal(_lcThumbUrl('/tmp/litecode_workspace/screens/a.png',480),'');
assert.equal(_lcThumbUrl('/tmp/litecode_workspace/uploads/a.gif',480),'');
global.esc=s=>String(s);
const h=_lcImgHtml('/tmp/litecode_workspace/uploads/a.png','a.png',480);
assert.ok(h.indexOf('loading="lazy"')<h.indexOf(' src=') && h.includes('decoding="async"') && h.includes('data-full="/api/media?path='));
'''
    subprocess.run([node, '-e', script, str(ASSETS)], check=True, capture_output=True, text=True)
