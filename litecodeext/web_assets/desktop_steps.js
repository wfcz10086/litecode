/* 桌面执行过程：每步图片与状态实时展示，历史回放使用同一渲染器。 */
(() => {
  const labels = {initialize:'初始化桌面',screenshot:'获取桌面截图',ocr:'识别当前画面',
    click_text:'点击识别到的文字',click:'按画面点击',drag:'拖动滑块或窗口',open_url:'打开网站',hotkey:'执行快捷键',
    type_text:'输入文字',type_secret:'填写登录凭据',windows:'检查桌面窗口',
    focus_window:'切换窗口',close_window:'关闭窗口',scroll:'滚动当前窗口',wechat_login:'打开微信登录',wechat_status:'检查微信状态'};
  function image(parent,path,label){
    if(typeof path!=='string'||!path.startsWith('/'))return;
    const link=document.createElement('a');link.href='/api/media?path='+encodeURIComponent(path);
    link.target='_blank';link.rel='noopener';link.title=label;
    const img=document.createElement('img');img.src=link.href;img.alt=label;img.loading='lazy';
    img.style.cssText='display:block;max-width:100%;max-height:360px;object-fit:contain;margin:8px auto';
    link.appendChild(img);parent.appendChild(link);
  }
  function render(container,step){
    if(!container||!step||typeof step!=='object')return null;
    const id=String(step.id||'');
    const old=[...container.querySelectorAll('.desktop-step')].find(e=>e.dataset.stepId===id);
    container.querySelectorAll('.desktop-step[open]').forEach(e=>e.removeAttribute('open'));
    const card=document.createElement('details');card.className='desktop-step';card.dataset.stepId=id;card.open=true;
    card.style.cssText='border:1px solid var(--bd);border-radius:8px;margin:8px 0;padding:8px;background:var(--sf2)';
    const summary=document.createElement('summary');summary.style.cssText='cursor:pointer;font-size:12px';
    const clock=Number.isFinite(step.ts)?new Date(step.ts*1000).toLocaleTimeString():'';
    summary.textContent=(step.ok?'✓ ':'✗ ')+(step.alias||'Windows')+' · '+(labels[step.action]||step.action||'桌面操作')+' · '+(step.state||'')+' '+clock;
    card.appendChild(summary);
    if(step.error){const error=document.createElement('p');error.style.color='var(--dg)';error.textContent=step.error;card.appendChild(error);}
    if(step.before_image){const before=document.createElement('details');const title=document.createElement('summary');title.textContent='操作前';before.appendChild(title);image(before,step.before_image,'操作前截图');card.appendChild(before);}
    if(step.image)image(card,step.image,'操作后截图，点击查看原图');
    if(old)old.replaceWith(card);else container.appendChild(card);
    return card;
  }
  window.LCDesktopSteps={render};
})();
