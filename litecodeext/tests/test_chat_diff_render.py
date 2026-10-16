"""直接执行前端历史/流式 diff 渲染代码，覆盖错误类型与正常补丁。"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_history_and_stream_diff_render_accept_only_text():
    node = shutil.which('node')
    if not node:
        pytest.skip('需要 Node.js 执行前端回归测试')
    source = (Path(__file__).resolve().parents[1] / 'web_assets/chat_core.js').read_text()
    lines = source.splitlines()
    history = next(line.strip() for line in lines if line.strip().startswith('diffs.forEach(dv=>'))
    stream = next(line.strip() for line in lines if line.strip().startswith('if(delta.diff_view)'))
    rdiff = next(line.strip() for line in lines if line.startswith('function rdiff('))
    script = '''
const assert=require('node:assert/strict');
const esc=s=>String(s), sbot=()=>{};
const document={createElement:()=>({className:'',innerHTML:''})};
let cards=[];const bubble={appendChild:e=>cards.push(e)};
const bodyEl={appendChild:e=>cards.push(e),querySelector:()=>null};
const sdiffs=[];
''' + rdiff + '\n' + '''
const bad=[{},[],42,true,null];
let diffs=bad.map(diff=>({diff}));
''' + history + '\n' + '''
assert.equal(cards.length,0);
diffs=[{filepath:99,diff:'+new\\n-old'}];
''' + history + '\n' + '''
assert.equal(cards.length,1);assert.ok(cards[0].innerHTML.includes('+1'));
cards=[];
for(const diff of [...bad,'+new\\n-old']){
 const delta={diff_view:{filepath:99,diff}};
''' + stream + '\n' + '''
}
assert.equal(cards.length,1);assert.ok(cards[0].innerHTML.includes('-1'));
assert.equal(rdiff({}), '');
'''
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
