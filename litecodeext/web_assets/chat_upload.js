// chat_upload.js — 多文件上传: 待发队列 / 进度 slot / 拖拽 / 粘贴 [原 chat_core.js 拆分 4/5]
/* [multi-upload] 待发送的文件队列 — 上传成功后入列, send() 把路径拼到消息后, 发送后清空 */
let _pendFiles=[];
// [Sprint-A 2026-07-24] 暴露 _pendFiles (let 不上 window); _renderPendFiles 是 function 声明, 已自动上 window
try{window._pendFiles=_pendFiles;}catch(e){}
/* [multi-upload] 上传/多模态限制 — loadSet/loadStats 会从 /api/health 拉来覆盖, 启动时先用默认 */
let _upLimit={max_mb:20,max_images:6,supports_vision:false};
/* [v1.10 P34-d-10 修 401 console error] 改为登录后才拉 /api/health, 避免未登录时 401 噪音 */
function _refreshUploadLimit(){
  fetch('/api/health').then(r=>r.ok?r.json():null).then(d=>{if(d){_upLimit.max_mb=d.max_upload_mb||20;_upLimit.max_images=d.multimodal_max_images||6;_upLimit.supports_vision=!!d.supports_vision;}}).catch(()=>{});
}
const _IMG_EXT=/\.(png|jpe?g|gif|webp|bmp|svg|ico)$/i;
function _fmtSz(n){return n<1024?n+'B':n<1048576?(n/1024).toFixed(1)+'KB':(n/1048576).toFixed(1)+'MB';}
function _fileBadge(name){const m=name.match(/\.([a-z0-9]+)$/i);return (m?m[1]:'?').toUpperCase().slice(0,4);}
// [Sprint-A 2026-07-24] slot 状态: uploading(loading+progress%) / done / error(可 retry)
function _renderPendFiles(){
  const pfp=document.getElementById('pfp');if(!pfp)return;
  if(!_pendFiles.length){pfp.innerHTML='';return;}
  const chips=_pendFiles.map((f,i)=>{
    const isImg=_IMG_EXT.test(f.name);
    const rel=(f.path||'').replace(/^.*?\/tmp\/openclaw_workspace\//,'');
    const src=rel?`/api/workspace/preview?path=${encodeURIComponent(rel)}`:'';
    // 客户端本地 preview: 未上传完的图直接用 blob URL
    const localSrc=f._blobUrl||src;
    const rm=`<button class="pfup" onclick="_rmPendFile(${i})" title="移除">×</button>`;
    const rt=`<button class="pfrt" onclick="_retryPendFile(${i})" title="重试上传">↻</button>`;
    const bar=(f.loading && !f.error)?`<div class="pfbar" style="width:${f.progress||0}%"></div>`:'';
    const cls=[f.loading?'loading':'',f.error?'error':'',(!f.loading&&!f.error)?'done':''].filter(Boolean).join(' ');
    if(isImg && localSrc){
      return `<div class="pfc img ${cls}"><img src="${localSrc}" onclick="_openImg('${localSrc}')"><span class="pfn" title="${esc(f.name)}">${esc(f.name)}</span>${rt}${rm}${bar}</div>`;
    }
    return `<div class="pfc ${cls}"><span class="mft" style="background:var(--ac);color:#000;padding:1px 5px;border-radius:3px;font-size:9px;font-weight:700">${_fileBadge(f.name)}</span><span class="pfn" title="${esc(f.name)}">${esc(f.name)}</span><span class="pfs">${_fmtSz(f.size)}</span>${rt}${rm}${bar}</div>`;
  }).join('');
  // 快速动作栏: ≥2 文件全部上传完 → 显示总结/翻译/提要按钮 (匹配 doc summary 场景)
  const readyCount=_pendFiles.filter(f=>f.path&&!f.loading&&!f.error).length;
  const totalCount=_pendFiles.length;
  let actions='';
  const imgReady=_pendFiles.filter(f=>f.path&&!f.loading&&!f.error&&_IMG_EXT.test(f.name)).length;
  if(readyCount>=2 && readyCount===totalCount){
    const ocrBtn=imgReady>0?`<button class="pf-act" onclick="_pendQuickAction('ocr')" title="用 vision_ocr 对 ${imgReady} 张图片做 OCR 识别">🔤 OCR (${imgReady})</button>`:'';
    actions=`<div class="pf-actions">
      <span class="pf-actions-hint">${readyCount} 个文件就绪:</span>
      <button class="pf-act" onclick="_pendQuickAction('summarize')" title="逐文件总结 + 汇总要点">📊 批量总结</button>
      <button class="pf-act" onclick="_pendQuickAction('translate')" title="翻译成英文 (默认); 目标语言可在弹出提示编辑">🌐 翻译</button>
      <button class="pf-act" onclick="_pendQuickAction('extract')" title="提取每文件关键信息 (人物/时间/数字/结论)">🔍 提取要点</button>
      <button class="pf-act" onclick="_pendQuickAction('compare')" title="比较文件之间的差异 / 共同点">🔀 对比</button>
      ${ocrBtn}
    </div>`;
  } else if(readyCount===1 && totalCount===1 && imgReady===1){
    // 单张图片时也给个 OCR 快捷入口 (常见场景)
    actions=`<div class="pf-actions">
      <span class="pf-actions-hint">图片就绪:</span>
      <button class="pf-act" onclick="_pendQuickAction('ocr')" title="识别图中文字 (vision_ocr)">🔤 OCR 识别</button>
      <button class="pf-act" onclick="_pendQuickAction('ocr_translate')" title="识别文字后自动翻成中文">🌏 OCR + 翻译</button>
    </div>`;
  }
  pfp.innerHTML=`<div class="pf-chips">${chips}</div>${actions}`;
}

// 快速动作: 自动填 prompt 到输入框, 用户可修改后回车发送 (不自动 send, 留最后确认权)
function _pendQuickAction(kind){
  const files=_pendFiles.filter(f=>f.path && !f.loading && !f.error);
  if(!files.length){toast('❌ 无就绪文件');return;}
  const list=files.map(f=>'- '+f.name).join('\n');
  const imgs=files.filter(f=>_IMG_EXT.test(f.name));
  const imgList=imgs.map(f=>'- '+f.name).join('\n');
  const prompts={
    summarize: `请对以下 ${files.length} 个附件逐一总结要点 (每文件 3-5 条), 然后给出跨文件的整体结论:\n${list}`,
    translate: `请把以下 ${files.length} 个附件的内容翻译成英文 (如原文已为英文则翻译成中文), 保留原有段落结构:\n${list}`,
    extract:   `请从以下 ${files.length} 个附件中提取关键信息 (人物/时间/地点/数字/关键结论), 以结构化列表输出:\n${list}`,
    compare:   `请比较以下 ${files.length} 个附件之间的差异与共同点, 输出对比表格:\n${list}`,
    ocr:       `请用 vision_ocr 工具对以下 ${imgs.length} 张图片逐一识别文字内容, 按图片顺序输出:\n${imgList||list}`,
    ocr_translate: `请用 vision_ocr 工具识别以下 ${imgs.length} 张图片的文字, 然后翻译成中文 (若原本已是中文则翻成英文):\n${imgList||list}`,
  };
  const mi=document.getElementById('mi');
  if(mi){
    mi.value=prompts[kind]||prompts.summarize;
    mi.focus();
    arx(mi);  // 自动 resize
    toast(`✓ 已填 ${kind} 提示词, 回车或点 ↑ 发送`);
  }
}
function _rmPendFile(i){
  const s=_pendFiles[i]; if(s && s._blobUrl){try{URL.revokeObjectURL(s._blobUrl);}catch(e){}}
  if(s && s._xhr){try{s._xhr.abort();}catch(e){}}
  _pendFiles.splice(i,1);_renderPendFiles();
}
function _retryPendFile(i){
  const s=_pendFiles[i]; if(!s || !s._file){toast('❌ 缺原始文件, 无法重试');return;}
  s.error=null; s.loading=true; s.progress=0; _renderPendFiles();
  _uploadOne(s);
}
function _openImg(src){const m=document.getElementById('imgModal');if(m){m.querySelector('img').src=src;m.classList.add('show');}}

// [Sprint-A Q007/Q008] 单文件上传 (XHR 拿 upload.progress + 可 retry)
function _uploadOne(slot){
  return new Promise(resolve=>{
    const xhr=new XMLHttpRequest();
    slot._xhr=xhr;
    xhr.upload.onprogress=(e)=>{
      if(e.lengthComputable){
        slot.progress=Math.round(e.loaded/e.total*100);
        _renderPendFiles();
      }
    };
    xhr.onload=()=>{
      slot._xhr=null;
      try{
        const d=JSON.parse(xhr.responseText||'{}');
        if(xhr.status>=200 && xhr.status<300 && d.ok){
          slot.path=d.path; slot.loading=false; slot.error=null; slot.progress=100;
        }else{
          slot.loading=false; slot.error=d.error||`HTTP ${xhr.status}`;
        }
      }catch(e){slot.loading=false; slot.error=String(e);}
      _renderPendFiles();
      resolve();
    };
    xhr.onerror=()=>{slot._xhr=null; slot.loading=false; slot.error='网络错误'; _renderPendFiles(); resolve();};
    xhr.onabort=()=>{slot._xhr=null; resolve();};
    xhr.open('POST', `/api/upload/${cur}`);
    const fd=new FormData(); fd.append('file', slot._file);
    xhr.send(fd);
  });
}

// [Sprint-A Q001-Q005] upFile 现在接受 FileList 或 File[]; 从 input / drop / paste 三处入
async function upFile(input){
  const files = input instanceof FileList ? Array.from(input) :
                Array.isArray(input) ? input :
                input && input.files ? Array.from(input.files) : [];
  if(!files.length) return;
  if(input && input.value !== undefined) input.value='';
  const MAX_SIZE=(_upLimit.max_mb||20)*1024*1024;
  /* 预警: 选了一堆图但模型不支持视觉 */
  const imgCount=files.filter(f=>_IMG_EXT.test(f.name)).length + _pendFiles.filter(f=>_IMG_EXT.test(f.name)).length;
  if(imgCount>_upLimit.max_images && _upLimit.supports_vision){
    toast(`⚠ 已选 ${imgCount} 张图, 超出 ${_upLimit.max_images} 张多模态上限, 多出的仍会上传但只作文件, 让 agent 用 vision_ocr 挑`);
  }else if(imgCount>0 && !_upLimit.supports_vision){
    toast(`⚠ 当前模型不支持视觉, 图片会作为文件路径保留, agent 可用 vision_ocr 处理`,4000);
  }
  /* session 尚未创建 → 用首个文件名作 session 名 */
  if(!cur){
    const d=await(await fetch('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Files: '+files[0].name})})).json();
    cur=d.id;sess.unshift({id:d.id,name:d.name,count:0});renderSL();
  }
  // 建 slot + 立刻渲染 (含 blob 缩略预览), 并发上传但节流为 3
  const slots=[];
  for(const f of files){
    if(f.size>MAX_SIZE){toast(`❌ ${f.name} 过大 (${_fmtSz(f.size)}), 限制 ${_upLimit.max_mb}MB`);continue;}
    // Q010 简单去重: 同 name+size 已在队列则跳
    if(_pendFiles.some(x=>x.name===f.name && x.size===f.size && !x.error)){
      toast(`⚠ 跳过重复: ${f.name}`); continue;
    }
    const slot={name:f.name,size:f.size,path:'',loading:true,progress:0,_file:f,error:null};
    if(_IMG_EXT.test(f.name)){try{slot._blobUrl=URL.createObjectURL(f);}catch(e){}}
    _pendFiles.push(slot); slots.push(slot);
  }
  _renderPendFiles();
  // 并发上限 3
  const q=slots.slice();
  const workers=Array.from({length:Math.min(3,q.length)}, async ()=>{
    while(q.length){const s=q.shift(); await _uploadOne(s);}
  });
  await Promise.all(workers);
}

// [Sprint-A Q002] 全局拖拽: dragover 显 overlay, drop 到消息区/输入区都触发 upFile
function _initAttachDragDrop(){
  // overlay 元素
  let ov=document.getElementById('drop-overlay');
  if(!ov){ov=document.createElement('div');ov.id='drop-overlay';ov.innerHTML='<div class="di">📎</div><div>松开鼠标以上传附件</div>';document.body.appendChild(ov);}
  let dragCnt=0;
  window.addEventListener('dragenter', e=>{
    if(!e.dataTransfer || !Array.from(e.dataTransfer.types||[]).includes('Files')) return;
    e.preventDefault(); dragCnt++; ov.classList.add('on');
  });
  window.addEventListener('dragover', e=>{
    if(!e.dataTransfer || !Array.from(e.dataTransfer.types||[]).includes('Files')) return;
    e.preventDefault();
  });
  window.addEventListener('dragleave', e=>{
    dragCnt=Math.max(0,dragCnt-1); if(dragCnt===0) ov.classList.remove('on');
  });
  window.addEventListener('drop', async e=>{
    if(!e.dataTransfer) return;
    dragCnt=0; ov.classList.remove('on');
    const items=e.dataTransfer.items;
    if(items && items.length){
      e.preventDefault();
      const collected=[];
      for(const it of items){
        if(it.kind!=='file') continue;
        const entry = it.webkitGetAsEntry && it.webkitGetAsEntry();
        if(entry && entry.isDirectory){
          await _walkDirEntry(entry,'',collected);
        }else{
          const f=it.getAsFile(); if(f) collected.push(f);
        }
      }
      if(collected.length) upFile(collected);
    }else if(e.dataTransfer.files && e.dataTransfer.files.length){
      e.preventDefault(); upFile(e.dataTransfer.files);
    }
  });
}
// [Sprint-A Q002] 递归读取目录 (DataTransferItem.webkitGetAsEntry)
function _walkDirEntry(entry, pathPrefix, collected){
  return new Promise(resolve=>{
    if(entry.isFile){
      entry.file(f=>{
        // 保留相对路径 (改文件名, 后端仍按 basename 收)
        try{Object.defineProperty(f,'name',{value: pathPrefix + f.name});}catch(e){}
        collected.push(f); resolve();
      }, ()=>resolve());
    }else if(entry.isDirectory){
      const reader=entry.createReader();
      const readAll=(acc)=>reader.readEntries(async ents=>{
        if(!ents.length){
          for(const e of acc) await _walkDirEntry(e, pathPrefix+entry.name+'/', collected);
          resolve();
        }else readAll(acc.concat(Array.from(ents)));
      }, ()=>resolve());
      readAll([]);
    }else resolve();
  });
}
// [Sprint-A Q003] 主输入框 paste: 抓剪贴板里的 image blob 直接上传
function _initAttachPaste(){
  const mi=document.getElementById('mi'); if(!mi) return;
  mi.addEventListener('paste', e=>{
    const items=e.clipboardData && e.clipboardData.items; if(!items) return;
    const imgs=[];
    for(const it of items){
      if(it.kind==='file' && it.type && it.type.startsWith('image/')){
        const f=it.getAsFile();
        if(f){
          // 剪贴板里的图通常叫 "image.png", 加时间戳避免撞 dedupe
          const nm=`pasted_${Date.now()}_${imgs.length}.${(it.type.split('/')[1]||'png').split('+')[0]}`;
          try{Object.defineProperty(f,'name',{value:nm});}catch(e){}
          imgs.push(f);
        }
      }
    }
    if(imgs.length){e.preventDefault(); upFile(imgs);}
  });
}
