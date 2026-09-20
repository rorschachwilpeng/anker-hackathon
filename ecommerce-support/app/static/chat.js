'use strict';
const $ = (id) => document.getElementById(id);
let mode = 'chat';
let conversationId = null;
let active = null;
const titles = {chat: '客服对话', extract: '售后提取'};
const types = {refund:'退款',return_refund:'退货退款',exchange:'换货',repair:'维修',logistics:'物流问题',other:'其他',unknown:'未说明'};
function scrollDown() { $('scroll-area').scrollTop = $('scroll-area').scrollHeight; }
function syncInput() { $('send').disabled = Boolean(active) || !$('message').value.trim(); }
function busy(value) {
  $('send').hidden = value; $('stop').hidden = !value;
  for (const id of ['new-chat','chat-mode','extract-mode']) $(id).disabled = value;
  $('message').disabled = value; syncInput();
}
function addMessage(role, text) {
  $('welcome').hidden = true;
  const article = document.createElement('article'); article.className = `message ${role}`;
  const label = document.createElement('div'); label.className = 'speaker'; label.textContent = role === 'user' ? '你' : '知遇 · AI 助手';
  const bubble = document.createElement('div'); bubble.className = 'bubble'; bubble.textContent = text;
  const tools=document.createElement('div'); tools.className='tool-traces';
  article.append(label,tools,bubble); $('messages').append(article); scrollDown();
  return {article,bubble,tools,badges:new Map()};
}
function note(view,text,error=false) {
  const el=document.createElement('div'); el.className='message-note'+(error?' error':''); el.textContent=text; view.article.append(el); scrollDown();
}
function reset(next=mode) {
  if(active) return;
  mode=next; conversationId=null; $('messages').replaceChildren(); $('welcome').hidden=false; $('message').value='';
  $('announcement').textContent='已开始新对话';
  $('page-title').textContent=titles[mode];
  for(const kind of ['chat','extract']) {const button=$(kind+'-mode'); button.classList.toggle('selected',kind===mode); if(kind===mode) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');}
  $('welcome-title').innerText=mode==='chat'?'每一个问题，\n都值得认真回应。':'把一段描述，\n整理成清楚的诉求。';
  $('welcome-description').textContent=mode==='chat'?'退换货、物流、售后咨询，告诉我你遇到了什么。':'贴入售后描述，提取订单号、诉求类型和期望方案。';
  $('suggestions').hidden=mode!=='chat';
  $('message').placeholder=mode==='chat'?'说说你的问题，我来帮你一起解决…':'例如：订单 A123 的衣服太小了，我想换大一码。';
  $('input-hint').textContent=mode==='chat'?'Enter 发送 · Shift + Enter 换行':'只提取已提供的信息，不执行售后操作';
  syncInput(); $('message').focus();
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
        if(event==='error') throw new Error(data.message || '回复中断，请重试。');
        if(event==='token') {
          if(typeof data.text!=='string') throw new Error('回复格式异常，请重试。');
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
function showExtraction(data,view) {
  if(!data || Object.keys(data).sort().join(',')!=='expected_solution,order_id,request_type' || !Object.hasOwn(types,data.request_type) || ![data.order_id,data.expected_solution].every(v=>v===null||typeof v==='string')) throw new Error('提取结果格式异常，请重试。');
  view.bubble.textContent='已整理这段售后描述';
  const list=document.createElement('dl'); list.className='result-fields';
  for(const [label,value] of [['订单号',data.order_id],['诉求类型',types[data.request_type]],['期望方案',data.expected_solution]]) {
    const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value??'未提供';list.append(dt,dd);
  }
  const details=document.createElement('details'); const summary=document.createElement('summary');summary.textContent='查看 JSON';const pre=document.createElement('pre');pre.textContent=JSON.stringify(data,null,2);details.append(summary,pre);view.article.append(list,details);scrollDown();
}
async function submit(event) {
  event.preventDefault(); if(active) return;
  const message=$('message').value.trim(); if(!message) return;
  const controller=new AbortController(); active=controller; busy(true); $('message').value='';
  $('announcement').textContent=mode==='chat'?'正在生成回复':'正在提取售后信息';
  addMessage('user',message); const view=addMessage('assistant',mode==='chat'?'正在思考…':'正在整理…');
  let timedOut=false; const timer=setTimeout(()=>{timedOut=true;controller.abort();},150000);
  try {
    const response=await fetch(mode==='chat'?'/chat':'/after-sales/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(mode==='chat'?{message,conversation_id:conversationId}:{description:message}),signal:controller.signal});
    if(!response.ok) {const data=await response.json().catch(()=>({}));throw new Error(typeof data.detail==='string'?data.detail:`请求未成功（${response.status}），请检查输入后重试。`);}
    if(mode==='chat') {
      const {answer,reason}=await streamChat(response,view);
      if(reason==='length') note(view,'回复达到长度上限，内容可能不完整；本轮未加入对话记忆。');

    } else showExtraction(await response.json(),view);
    $('announcement').textContent='回复完成';
  } catch(error) {
    if(view.bubble.textContent==='正在思考…'||view.bubble.textContent==='正在整理…') view.bubble.textContent='';
    const text=controller.signal.aborted?(timedOut?'等待超时，请重试。':'已停止生成，本轮未加入对话记忆。'):(error instanceof TypeError?'无法连接服务，请确认本地服务正在运行。':error.message);
    for(const badge of view.badges.values()) if(['running','retrying'].includes(badge.dataset.status)) {badge.dataset.status='failed';badge.textContent+=' · 状态未确认';}
    note(view,text+' 已提交的工单不会因停止生成而撤销。',!controller.signal.aborted||timedOut);
    const retry=document.createElement('button');retry.type='button';retry.className='retry';retry.textContent='重新编辑';retry.onclick=()=>{if(!active){$('message').value=message;syncInput();$('message').focus();}};view.article.append(retry);
    $('announcement').textContent=text;
  } finally {clearTimeout(timer);active=null;busy(false);$('message').focus();scrollDown();}
}
$('composer').addEventListener('submit',submit);
$('message').addEventListener('input',syncInput);
$('message').addEventListener('keydown',(event)=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();$('composer').requestSubmit();}});
$('stop').onclick=()=>active?.abort();
$('new-chat').onclick=()=>reset();
$('chat-mode').onclick=()=>{if(mode!=='chat')reset('chat');};
$('extract-mode').onclick=()=>{if(mode!=='extract')reset('extract');};
for(const button of document.querySelectorAll('[data-prompt]')) button.onclick=()=>{$('message').value=button.dataset.prompt;syncInput();$('message').focus();};
fetch('/health').then(r=>{if(!r.ok)throw new Error();$('connection').textContent='服务已连接';$('connection').classList.add('online');}).catch(()=>{$('connection').textContent='服务未连接';});

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
