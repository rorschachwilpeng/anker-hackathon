'use strict';
const $ = (id) => document.getElementById(id);
let mode = null;
let currentView = 'home';
let conversationId = null;
let ticketNo = null;
let ticketRequest = 0;
let active = null;
let selectedImage = null;
let previewUrl = null;
let dragDepth = 0;
const previewUrls = new Set();
const titles = {home: '', chat: '客服对话', order: '订单与物流', product: '产品咨询与排障', ticket: '售后跟进'};
const types = {refund:'退款',return_refund:'退货退款',exchange:'换货',repair:'维修',logistics:'物流问题',other:'其他',unknown:'未说明'};
function scrollDown() { $('scroll-area').scrollTop = $('scroll-area').scrollHeight; }
function canUpload() { return currentView==='product' && (mode===null || mode==='s1pro'); }
function canContinue() { return currentView!=='ticket' && (!selectedImage || currentView==='product') && (mode!=='s1pro' || currentView==='product'); }
function homeDestination(message) {
  const normalized=message.toLowerCase().replace(/\s+/g,'');
  if (/s1[\s-]*pro/i.test(message) && /吸力|不吸/.test(message)) return 'product';
  if (/订单|物流|还没收到|没收到|配送/.test(normalized)) return 'order';
  return 'chat';
}
function syncInput() {
  const hasImage = canUpload() && Boolean(selectedImage);
  $('send').disabled = Boolean(active) || !canContinue() || (!$('message').value.trim() && !hasImage);
  $('add-image').disabled = Boolean(active) || hasImage || !canUpload();
  $('choose-image').disabled = Boolean(active) || hasImage || !canUpload();
  $('remove-image').disabled = Boolean(active);
  $('image-file').disabled = Boolean(active) || !canUpload();
}
function busy(value) {
  $('send').hidden = value; $('stop').hidden = !value;
  for (const id of ['new-chat','brand-home','home-link']) $(id).disabled = value;
  for (const button of document.querySelectorAll('[data-view]')) button.disabled=value;
  $('message').disabled = value; syncInput();
}
function addMessage(role, text, image = null) {
  $('welcome').hidden = true;
  $('messages').hidden = false;
  const article = document.createElement('article'); article.className = `message ${role}`;
  const label = document.createElement('div'); label.className = 'speaker'; label.textContent = role === 'user' ? '你' : '知遇 · AI 助手';
  const bubble = document.createElement('div'); bubble.className = 'bubble';
  if (image) {
    const attachment = document.createElement('div'); attachment.className = 'sent-image';
    const thumbnail = document.createElement('img'); thumbnail.src = image.url; thumbnail.alt = '本轮已发送的图片';
    const caption = document.createElement('span'); caption.textContent = '已附一张图片';
    attachment.append(thumbnail, caption); bubble.append(attachment);
  }
  if (text) { const body = document.createElement('div'); body.textContent = text; bubble.append(body); }
  const tools=document.createElement('div'); tools.className='tool-traces';
  article.append(label,tools,bubble); $('messages').append(article); scrollDown();
  return {article,bubble,tools,badges:new Map()};
}
function note(view,text,error=false) {
  const el=document.createElement('div'); el.className='message-note'+(error?' error':''); el.textContent=text; view.article.append(el); scrollDown();
}
function renderOrderCard(data,view) {
  if(!data?.found||data.source!=='demo'||!data.order||!Array.isArray(data.items)) throw new Error('订单卡片数据格式异常，请重试。');
  const order=data.order;
  const card=document.createElement('section'); card.className='order-card'; card.setAttribute('aria-label','演示订单信息');
  const heading=document.createElement('div'); heading.className='order-card-heading';
  const title=document.createElement('strong'); title.textContent='订单 '+order.order_no;
  const badge=document.createElement('span'); badge.textContent='演示数据'; heading.append(title,badge);
  const products=document.createElement('div');products.className='order-card-products';
  if(data.items.length) for(const item of data.items) {
    const product=document.createElement('p');product.className='order-card-product';
    product.textContent=`${item.product_name} · ${item.model_code}`;products.append(product);
  } else {
    const product=document.createElement('p');product.className='order-card-product';product.textContent='暂无商品明细';products.append(product);
  }
  const rows=document.createElement('dl');
  for(const [label,value] of [['支付状态',order.payment_status],['配送状态',order.shipping_status]]) {
    const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;rows.append(dt,dd);
  }
  const event=document.createElement('p'); event.className='order-card-event';
  event.textContent=data.latest_event?`最新物流：${data.latest_event.occurred_at} · ${data.latest_event.location} · ${data.latest_event.description}`:'最新物流：暂无物流记录';
  const link=document.createElement('a'); link.href='/orders/'+encodeURIComponent(order.order_no); link.target='_blank'; link.rel='noopener noreferrer'; link.textContent='查看订单详情 ↗';
  card.append(heading,products,rows,event,link); view.article.append(card); scrollDown();
}
function renderKnowledgeSources(data,view) {
  if(!Array.isArray(data?.items)) return;
  const sources=data.items.filter(item=>item&&item.product_model&&item.source_title&&typeof item.source_url==='string');
  if(!sources.length) return;
  const section=document.createElement('div');section.className='knowledge-sources';
  for(const item of sources) {
    let url;
    try { url=new URL(item.source_url); } catch { continue; }
    if(url.protocol!=='https:'||!['service.eufy.com','www.eufy.com','salesforce-knowledge-download.s3.us-west-2.amazonaws.com'].includes(url.hostname)) continue;
    const link=document.createElement('a');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';
    link.textContent=`型号 ${item.product_model} · 官方资料：${item.source_title}`;section.append(link);
  }
  if(section.childElementCount) {view.article.append(section);scrollDown();}
}
function navigate(next) {
  if(active) return;
  if(next!=='ticket') ticketRequest++;
  currentView=next;
  $('page-title').textContent=titles[next];
  $('breadcrumb').hidden=next==='home';
  for(const name of ['home','order','product']) $(name+'-welcome').hidden=name!==next;
  $('welcome').hidden=next==='ticket'||next==='chat'||(next!=='home'&&$('messages').childElementCount>0);
  $('messages').hidden=next==='home'||next==='ticket'||!$('messages').childElementCount;
  $('ticket-panel').hidden=next!=='ticket';
  $('composer-area').hidden=next==='ticket';
  $('s1pro-status').hidden=next!=='product'||!$('s1pro-status').textContent;
  for(const name of ['order','product','ticket']) {
    const button=$(name+'-mode'); button.classList.toggle('selected',name===next);
    if(name===next) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  }
  closeImageMenu();
  $('image-tools').hidden=!canUpload();
  $('attachment').hidden=!canUpload()||!selectedImage;
  $('message').placeholder=next==='product'?'描述产品和使用问题…':'说说你遇到的问题…';
  $('input-hint').textContent=selectedImage&&next!=='product'
    ?'图片草稿保留在产品咨询；请返回处理后再发送'
    :mode==='s1pro'&&next!=='product'
    ?'当前排障会话请回到产品咨询继续；换链路需新对话'
    :mode==='chat'&&next==='product'
      ?'当前会话继续通用咨询；图片排障需新对话'
      :next==='product'?'可添加一张图片 · 建单前需确认':'Enter 发送 · Shift + Enter 换行';
  syncInput();
  if(next==='ticket') loadTicket();
  else $('scroll-area').scrollTop=0;
}
function reset() {
  if(active) return;
  for (const url of previewUrls) URL.revokeObjectURL(url);
  previewUrls.clear(); selectedImage=null; previewUrl=null; $('image-file').value='';
  $('attachment').hidden=true; $('thumbnail').removeAttribute('src'); $('image-feedback').hidden=true;
  $('image-feedback').textContent=''; $('composer').classList.remove('dragging');
  $('drop-mask').hidden=true; dragDepth=0; closeImageMenu();
  mode=null; conversationId=null; ticketNo=null; ticketRequest++; $('ticket-number').value='';
  $('ticket-detail').replaceChildren(); $('ticket-detail').hidden=true; $('ticket-refresh').hidden=true;
  $('messages').replaceChildren(); $('message').value='';
  $('s1pro-status').hidden=true; $('s1pro-status').textContent='';
  $('announcement').textContent='已开始新对话';
  navigate('home'); $('message').focus();
}
async function streamChat(response,view) {
  if(!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('没有收到有效的流式回复，请重试。');
  const reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer='', answer='', finished=false, reason='';
  try {
    while(!finished) {
      const {value,done}=await reader.read(); buffer += done?decoder.decode():decoder.decode(value,{stream:true});
      let boundary;
      while((boundary=/\r?\n\r?\n/.exec(buffer))) {
        const block=buffer.slice(0,boundary.index); buffer=buffer.slice(boundary.index+boundary[0].length);
        const lines=block.split(/\r?\n/); const event=lines.find(line=>line.startsWith('event:'))?.slice(6).trim();
        const raw=lines.filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
        if(!raw) continue;
        const data=JSON.parse(raw);
        if(event==='answer_reset') {answer='';view.bubble.textContent='正在查询…';}
        if(event==='session') { if(!Number.isSafeInteger(data.conversation_id)||data.conversation_id<1) throw new Error('会话标识无效'); conversationId=data.conversation_id; }
        if(event==='tool_status') updateToolBadge(view,data);
        if(event==='order_card') renderOrderCard(data,view);
        if(event==='knowledge_sources') renderKnowledgeSources(data,view);
        if(event==='error') throw new Error(data.message || '回复中断，请重试。');
        if(event==='token') {
          if(typeof data.text!=='string') throw new Error('回复格式异常，请重试。');
          view.bubble.classList.remove('pending');
          answer+=data.text; view.bubble.textContent=answer; scrollDown();
        }
        if(event==='done') {reason=data.finish_reason; if(!['stop','length'].includes(reason)) throw new Error('回复未正常结束，请重试。'); finished=true; break;}
      }
      if(done) break;
    }
    if(!finished || !answer.trim()) throw new Error('回复没有完整到达，请重试。');
    return {answer,reason};
  } finally { await reader.cancel().catch(()=>{}); reader.releaseLock(); }
}
function showExtraction(data) {
  if(!data || Object.keys(data).sort().join(',')!=='expected_solution,order_id,request_type' || !Object.hasOwn(types,data.request_type) || ![data.order_id,data.expected_solution].every(v=>v===null||typeof v==='string')) throw new Error('提取结果格式异常，请重试。');
  const result=$('extract-result'); result.replaceChildren();
  const list=document.createElement('dl'); list.className='result-fields';
  for(const [label,value] of [['订单号',data.order_id],['诉求类型',types[data.request_type]],['期望方案',data.expected_solution]]) {
    const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value??'未提供';list.append(dt,dd);
  }
  result.append(list);
}
function ticketField(list,label,value) {
  const dt=document.createElement('dt'); dt.textContent=label;
  const dd=document.createElement('dd'); dd.textContent=Array.isArray(value)?value.join('；'):(value??'待补充');
  list.append(dt,dd);
}
function renderTicket(ticket) {
  if(typeof ticket?.ticket_no!=='string'||typeof ticket.status!=='string') throw new Error('工单数据格式异常。');
  const detail=$('ticket-detail'); detail.replaceChildren();
  const title=document.createElement('h2'); title.textContent='工单 '+ticket.ticket_no;
  const badge=document.createElement('span'); badge.className='demo-label'; badge.textContent='本地演示';
  const heading=document.createElement('div'); heading.className='ticket-heading'; heading.append(title,badge);
  const list=document.createElement('dl');
  for(const [label,value] of [['当前状态',ticket.status],['型号',ticket.product_model],['问题',ticket.symptom],['已建议步骤',ticket.suggested_steps],['已尝试步骤',ticket.completed_steps],['用户反馈',ticket.feedback],['交接摘要',ticket.description]]) ticketField(list,label,value);
  detail.append(heading,list);
  for(const source of ticket.sources||[]) {
    if(typeof source?.title!=='string'||typeof source.url!=='string') continue;
    let url; try {url=new URL(source.url);} catch {continue;}
    if(url.protocol!=='https:'||!['service.eufy.com','www.eufy.com','salesforce-knowledge-download.s3.us-west-2.amazonaws.com'].includes(url.hostname)) continue;
    const link=document.createElement('a'); link.href=url.href; link.target='_blank'; link.rel='noopener noreferrer'; link.textContent='资料来源：'+source.title; detail.append(link);
  }
  detail.hidden=false;
}
async function loadTicket() {
  const request=++ticketRequest;
  const detail=$('ticket-detail'); detail.replaceChildren(); detail.hidden=true;
  $('ticket-refresh').hidden=!ticketNo;
  if(!ticketNo) { $('ticket-status').textContent='当前会话还没有工单。可输入已知演示工单号，或返回产品咨询与排障。'; return; }
  $('ticket-number').value=ticketNo;
  $('ticket-status').textContent='正在读取工单…';
  try {
    const response=await fetch('/s1pro/tickets/'+encodeURIComponent(ticketNo),{cache:'no-store'});
    if(request!==ticketRequest) return;
    if(response.status===404) { $('ticket-status').textContent='未找到该工单，请核对工单号。'; return; }
    if(!response.ok) throw new Error('读取失败，请点击“刷新进度”重试。');
    renderTicket(await response.json());
    if(request!==ticketRequest) {detail.replaceChildren();detail.hidden=true;return;}
    $('ticket-status').textContent='已读取服务端当前记录。';
  } catch {
    if(request===ticketRequest) {detail.replaceChildren();detail.hidden=true;$('ticket-status').textContent='工单读取失败，旧状态已隐藏。请点击“刷新进度”重试。';}
  }
}
const s1proStages={clarify_product:'确认产品中',ask_symptom:'了解故障现象',ask_feedback:'等待您的反馈',confirm_ticket:'等待建单确认',ticket_created:'工单已创建',ticket_failed:'建单未成功',ticket_deferred:'暂不建单',resolved:'问题已解决',no_answer:'暂无对应官方资料',degraded:'服务暂时降级'};
function updateS1ProStatus(progress,stage) {
  const bar=$('s1pro-status');
  if (currentView!=='product'||!progress) { bar.hidden=true; return; }
  const parts=[progress.product?`已确认：${progress.product}`:'产品：待确认'];
  if (progress.symptom) parts.push(`症状：${progress.symptom}`);
  parts.push(`已建议 ${progress.suggested} 步 · 已尝试 ${progress.tried} 步`);
  parts.push(`阶段：${stage}`);
  if (progress.urgent) parts.push('用户已表示着急');
  if (Array.isArray(progress.sources)&&progress.sources.length) parts.push(`来源：${progress.sources[progress.sources.length-1]}`);
  if (progress.ticket_no) parts.push(`工单：${progress.ticket_no}`);
  bar.textContent=parts.join(' ｜ '); bar.hidden=false;
}
async function streamS1Pro(response,view) {
  if(!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    showS1Pro(await response.json(),view); return;
  }
  const reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer='', result=null, failure=null;
  const stageText={understanding:'正在理解您的描述…',searching:'正在检索 eufy 官方排障资料…'};
  try {
    for(;;) {
      const {value,done}=await reader.read(); buffer+=done?decoder.decode():decoder.decode(value,{stream:true});
      let boundary;
      while((boundary=/\r?\n\r?\n/.exec(buffer))) {
        const block=buffer.slice(0,boundary.index); buffer=buffer.slice(boundary.index+boundary[0].length);
        const lines=block.split(/\r?\n/); const event=lines.find(line=>line.startsWith('event:'))?.slice(6).trim();
        const raw=lines.filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
        if(!raw) continue;
        const data=JSON.parse(raw);
        if(event==='stage') { view.bubble.textContent=stageText[data.stage]||'正在处理…'; scrollDown(); }
        if(event==='search_done' && typeof data.title==='string') {
          const badge=document.createElement('span'); badge.className='tool-badge'; badge.dataset.status='succeeded';
          badge.textContent=`检索到官方资料：${data.title}`; view.tools.append(badge); scrollDown();
        }
        if(event==='error') failure=data;
        if(event==='result') result=data;
      }
      if(done) break;
    }
  } finally { await reader.cancel().catch(()=>{}); reader.releaseLock(); }
  if(failure) throw new Error(typeof failure.message==='string'?failure.message:'服务暂时不可用，请重试。');
  if(!result) throw new Error('回复没有完整到达，请重试。');
  showS1Pro(result,view);
}
const productQuickReplies={candidate:['确认是 T2080 扫地机器人','不是这款产品'],category:['是扫地机器人','是吸乳器']};
const s1proQuickReplies={symptom:['吸力变弱'],feedback:['好了，吸力恢复了','还是不行'],ticket_confirmation:['可以建单','再想想，先不建']};
function disableQuickReplies() {
  for(const button of document.querySelectorAll('.quick-reply')) button.disabled=true;
}
function renderS1ProQuickReplies(view,awaiting,productOptions) {
  const options=awaiting==='product'?productQuickReplies[productOptions]:s1proQuickReplies[awaiting];
  if(!options) return;
  const row=document.createElement('div'); row.className='quick-replies';
  for(const text of options) {
    const button=document.createElement('button'); button.type='button'; button.className='quick-reply'; button.textContent=text;
    button.onclick=()=>{ if(active||button.disabled) return; $('message').value=text; syncInput(); $('composer').requestSubmit(); };
    row.append(button);
  }
  view.article.append(row); scrollDown();
}
function showS1Pro(data,view) {
  if (!Number.isSafeInteger(data.conversation_id) || typeof data.reply !== 'string') throw new Error('服务回复格式异常。');
  disableQuickReplies();
  conversationId=data.conversation_id; view.bubble.classList.remove('pending'); view.bubble.textContent=data.reply;
  if (typeof data.notice==='string' && data.notice) note(view,data.notice);
  if (data.source?.url && data.source?.title) {
    const link=document.createElement('a'); link.href=data.source.url; link.textContent=`资料来源：${data.source.title}`;
    link.target='_blank'; link.rel='noopener noreferrer'; view.article.append(link);
  }
  if (typeof data.ticket_no==='string' && data.ticket_url==='/s1pro/tickets/'+data.ticket_no) {
    ticketNo=data.ticket_no;
    const link=document.createElement('button'); link.type='button'; link.textContent='查看工单进度（本地演示）';
    link.className='ticket-link'; link.onclick=()=>navigate('ticket'); view.article.append(link);
  }
  const stage=s1proStages[data.action]||'处理中';
  note(view,`当前阶段：${stage}`);
  updateS1ProStatus(data.progress,stage);
  renderS1ProQuickReplies(view,data.progress?.awaiting,data.progress?.product_options);
}
function closeImageMenu() {
  $('add-menu').hidden=true;
  $('add-image').setAttribute('aria-expanded','false');
}
function setImageFeedback(message='') {
  $('image-feedback').textContent=message;
  $('image-feedback').hidden=!message;
}
function showImageDraft(image,url) {
  selectedImage=image; previewUrl=url; previewUrls.add(url);
  $('thumbnail').src=url; $('file-name').textContent=image.name||'图片';
  $('attachment').hidden=false; setImageFeedback(); syncInput();
}
function clearImageDraft({revoke=true,clearFeedback=true}={}) {
  if(previewUrl&&revoke) { URL.revokeObjectURL(previewUrl); previewUrls.delete(previewUrl); }
  selectedImage=null; previewUrl=null; $('image-file').value='';
  $('attachment').hidden=true; $('thumbnail').removeAttribute('src'); $('file-name').textContent='';
  if(clearFeedback) setImageFeedback();
  syncInput();
}
function chooseImage(image) {
  $('image-file').value='';
  if(!image||active||!canUpload()) return;
  setImageFeedback();
  if(selectedImage) { setImageFeedback('本轮只能添加一张图片，请先移除已选图片。'); return; }
  if(!['image/png','image/jpeg'].includes(image.type)) { setImageFeedback('仅支持 JPG 或 PNG 图片。'); return; }
  if(image.size>5*1024*1024) { setImageFeedback('图片不能超过 5 MB，请选择更小的图片。'); return; }
  showImageDraft(image,URL.createObjectURL(image));
}
async function submit(event) {
  event.preventDefault(); if(active||!canContinue()) return;
  const message=$('message').value.trim();
  const destination=mode?null:currentView==='home'?homeDestination(message):currentView;
  const submittedMode=mode||(destination==='product'?'s1pro':'chat');
  const submittedImage=canUpload()?selectedImage:null;
  const submittedPreviewUrl=submittedImage?previewUrl:null;
  if(!message&&!submittedImage) return;
  disableQuickReplies();
  mode=submittedMode;
  if(currentView==='home') navigate(destination);
  closeImageMenu();
  const controller=new AbortController(); active=controller; busy(true); $('message').value='';
  $('announcement').textContent=submittedImage?'正在识别图片并整理可见线索…':'正在生成回复';
  addMessage('user',message,submittedImage?{url:submittedPreviewUrl}:null);
  const view=addMessage('assistant',submittedImage?'正在识别图片并整理可见线索…':submittedMode==='chat'?'正在思考…':'正在理解您的描述…');
  view.bubble.classList.add('pending');
  if(submittedImage) clearImageDraft({revoke:false});
  let timedOut=false; const timer=setTimeout(()=>{timedOut=true;controller.abort();},150000);
  try {
    let response;
    if(submittedImage) {
      const form=new FormData(); form.append('message',message);
      if(conversationId!==null) form.append('conversation_id',String(conversationId));
      form.append('image',submittedImage);
      response=await fetch('/s1pro/chat/image',{method:'POST',body:form,signal:controller.signal});
    } else {
      const payload={message,conversation_id:conversationId};
      response=await fetch(submittedMode==='chat'?'/chat':'/s1pro/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:controller.signal});
    }
    if(!response.ok) {const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:`请求未成功（${response.status}），请检查输入后重试。`);}
    if(submittedMode==='chat') {
      const {answer,reason}=await streamChat(response,view);
      if(reason==='length') note(view,'回复达到长度上限，内容可能不完整；本轮未加入对话记忆。');

    } else if(!submittedImage) await streamS1Pro(response,view);
    else showS1Pro(await response.json(),view);
    $('announcement').textContent='回复完成';
  } catch(error) {
    view.bubble.classList.remove('pending');
    if(['正在思考…','正在识别图片并整理可见线索…','正在理解您的描述…','正在检索 eufy 官方排障资料…'].includes(view.bubble.textContent)) view.bubble.textContent='';
    const text=controller.signal.aborted?(timedOut?'等待超时，请重试。':'已停止生成，本轮未加入对话记忆。'):(error instanceof TypeError?'无法连接服务，请确认本地服务正在运行。':error.message);
    for(const badge of view.badges.values()) if(['running','retrying'].includes(badge.dataset.status)) {badge.dataset.status='failed';badge.textContent+=' · 状态未确认';}
    const ticketAttempt=submittedMode==='s1pro' && /可以建单|同意建单|确认创建工单|请创建工单/.test(message);
    note(view,text+(ticketAttempt?' 建单结果尚未确认，请先核对工单再重试。':''),!controller.signal.aborted||timedOut);
    const retry=document.createElement('button');retry.type='button';retry.className='retry';retry.textContent='重新编辑';retry.onclick=()=>{
      if(active) return;
      if(mode!==submittedMode||!canContinue()) { note(view,'请返回本轮服务模块后再重新编辑。'); return; }
      if(submittedImage&&selectedImage&&selectedImage!==submittedImage) { note(view,'请先移除输入区当前图片，再恢复本轮图片。',true); return; }
      $('message').value=message;
      if(submittedImage&&!selectedImage) showImageDraft(submittedImage,submittedPreviewUrl);
      syncInput(); $('message').focus();
    };view.article.append(retry);
    $('announcement').textContent=text;
  } finally {clearTimeout(timer);active=null;busy(false);$('message').focus();scrollDown();}
}
$('composer').addEventListener('submit',submit);
$('message').addEventListener('input',syncInput);
$('message').addEventListener('keydown',(event)=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();$('composer').requestSubmit();}});
$('stop').onclick=()=>active?.abort();
$('new-chat').onclick=()=>reset();
$('brand-home').onclick=()=>navigate('home');
$('home-link').onclick=()=>navigate('home');
for(const button of document.querySelectorAll('[data-view]')) button.onclick=()=>navigate(button.dataset.view);
$('ticket-to-product').onclick=()=>navigate('product');
$('ticket-lookup').addEventListener('submit',(event)=>{
  event.preventDefault(); const value=$('ticket-number').value.trim();
  if(!value) {$('ticket-status').textContent='请输入工单号。';return;}
  ticketNo=value; loadTicket();
});
$('ticket-refresh').onclick=()=>loadTicket();
$('about-open').onclick=()=>{$('about-mask').hidden=false;$('about-close').focus();};
$('about-close').onclick=()=>{$('about-mask').hidden=true;$('about-open').focus();};
$('about-mask').onclick=(event)=>{if(event.target===$('about-mask')) $('about-mask').hidden=true;};
$('extract-form').addEventListener('submit',async(event)=>{
  event.preventDefault();const description=$('extract-input').value.trim();if(!description)return;
  $('extract-result').textContent='正在整理…';
  try {
    const response=await fetch('/after-sales/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({description})});
    if(!response.ok) throw new Error();
    showExtraction(await response.json());
  } catch {$('extract-result').textContent='提取失败，请检查服务后重试。';}
});
$('add-image').addEventListener('click',()=>{
  if(active||selectedImage) return;
  $('add-menu').hidden=!$('add-menu').hidden;
  $('add-image').setAttribute('aria-expanded',String(!$('add-menu').hidden));
});
$('choose-image').addEventListener('click',()=>{closeImageMenu();$('image-file').click();});
$('image-file').addEventListener('change',()=>chooseImage($('image-file').files[0]));
$('remove-image').addEventListener('click',()=>clearImageDraft());
document.addEventListener('click',(event)=>{if(!event.target.closest('#image-tools')) closeImageMenu();});
document.addEventListener('keydown',(event)=>{if(event.key==='Escape') {closeImageMenu();$('about-mask').hidden=true;}});
$('composer').addEventListener('dragenter',(event)=>{
  if(!event.dataTransfer?.types.includes('Files')) return;
  event.preventDefault();
  if(active||!canUpload()) return;
  dragDepth++; $('composer').classList.add('dragging'); $('drop-mask').hidden=false;
});
$('composer').addEventListener('dragover',(event)=>{
  if(!event.dataTransfer?.types.includes('Files')) return;
  event.preventDefault();
  if(!active&&canUpload()) event.dataTransfer.dropEffect='copy';
});
$('composer').addEventListener('dragleave',(event)=>{
  if(!event.dataTransfer?.types.includes('Files')||active||!canUpload()) return;
  event.preventDefault();
  dragDepth=Math.max(0,dragDepth-1);
  if(dragDepth===0) { $('composer').classList.remove('dragging'); $('drop-mask').hidden=true; }
});
$('composer').addEventListener('drop',(event)=>{
  if(!event.dataTransfer?.files.length) return;
  event.preventDefault(); dragDepth=0; $('composer').classList.remove('dragging'); $('drop-mask').hidden=true;
  if(active||!canUpload()) return;
  if(event.dataTransfer.files.length!==1) { setImageFeedback('本轮只能添加一张图片。'); return; }
  chooseImage(event.dataTransfer.files[0]);
});
for(const button of document.querySelectorAll('[data-prompt]')) button.onclick=()=>{$('message').value=button.dataset.prompt;syncInput();$('message').focus();};
fetch('/health').then(r=>{if(!r.ok)throw new Error();$('connection').textContent='服务已连接';$('connection').classList.add('online');}).catch(()=>{$('connection').textContent='服务未连接';});
navigate('home');

function updateToolBadge(view,data) {
  const names={query_order:'查询订单',query_product:'查询商品',query_logistics:'查询物流',query_faq:'查询常见问题',create_ticket:'创建人工工单'};
  const statuses={running:'查询中',retrying:'重试中',succeeded:'已完成',failed:'未完成',skipped:'本轮跳过'};
  if(typeof data.tool_call_id!=='string'||!Object.hasOwn(statuses,data.status)) return;
  let badge=view.badges.get(data.tool_call_id);
  if(!badge) {badge=document.createElement('span');badge.className='tool-badge';view.badges.set(data.tool_call_id,badge);view.tools.append(badge);}
  badge.dataset.status=data.status;
  badge.textContent=(names[data.name]||'工具')+' · '+statuses[data.status];
  badge.title=String(data.name)+' · '+data.tool_call_id;
  badge.setAttribute('aria-label',badge.textContent);scrollDown();
}
