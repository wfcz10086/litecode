"""浏览器空闲时接上定时器新轮次，只追加新消息，不重绘旧历史。"""
from pathlib import Path
import subprocess


def test_background_runs_append_and_follow_without_repainting_history():
    path = Path(__file__).resolve().parents[1] / 'web_assets/chat_attach.js'
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
let current={running:false,started:1,user_ts:0.9},messages=[],added=[],attached=0,repainted=0,fail=false;
global.window=global;global.document={hidden:false};global.cur='web-test';global.streaming=false;
global.setInterval=()=>0;global.setTimeout=()=>0;global.sbot=()=>{};
global.renderMsgs=()=>{repainted++};global._mergeConsecutiveAssistants=x=>x;
global.addMsg=(role,content)=>added.push([role,content]);global._newAiBubble=()=>({});
global._streamTurn=()=>{attached++;streaming=true};
global.fetch=async url=>url.endsWith('/run')?{ok:true,json:async()=>current}:
 {ok:!fail,json:async()=>({messages})};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
(async()=>{
 await LCAttach.resumeIfRunning(cur,[]);
 current={running:true,started:2,user_ts:1.9};messages=[{role:'user',content:'旧',ts:0.9},{role:'user',content:'定时巡检',ts:1.9}];
 await LCAttach.pollBackground();assert.equal(attached,1);assert.deepEqual(added,[['user','定时巡检']]);assert.equal(repainted,0);
 await LCAttach.pollBackground();assert.equal(attached,1);
 streaming=false;current={running:false,started:2,user_ts:1.9};await LCAttach.afterTurn(cur);
 current={running:false,started:3,user_ts:2.9};messages.push({role:'user',content:'下一次巡检',ts:2.9},{role:'assistant',content:'没有新回复',ts:3.1});
 fail=true;await LCAttach.pollBackground();assert.equal(added.length,1);
 fail=false;await LCAttach.pollBackground();assert.deepEqual(added.slice(1),[['user','下一次巡检'],['assistant','没有新回复']]);
 await LCAttach.pollBackground();assert.equal(added.length,3);assert.equal(repainted,0);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    subprocess.run(['node', '-e', script, str(path)], check=True, capture_output=True, text=True)
