import json
import re
import uuid
from dataclasses import dataclass
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from app.context import complete_turns,trim_turns
from app.events import StreamEvent
from app.prompts import TOOL_CHAT_PROMPT
from app.tools.business import ToolContext,build_tools
from app.tools.executor import ToolExecutor,failure

class ChatProtocolError(RuntimeError):
    def __init__(self,code):
        super().__init__(code)
        self.code=code

class ChatError(Exception):
    def __init__(self,status,message):self.status=status;self.message=message

def _first_use_items(data):
    return [item for item in data.get('items',[]) if item.get('product_model')=='T2080'
            and ('quick start' in item.get('source_title','').casefold()
                 or '首次使用' in item.get('source_title',''))]

def _fixed_demo_answer(name,result):
    if not result.get('ok'):
        return '这次查询没有成功，我暂时不能确认结果。请稍后再试。'
    data=result['data']
    if name=='query_order':
        if not data.get('found'):
            return f'没有找到订单 {data.get("order_no", "")}，请核对订单号。'
        order=data['order']
        products='、'.join(f'{item["product_name"]}（{item["model_code"]}）' for item in data['items']) or '暂无商品明细'
        latest=data.get('latest_event')
        progress=(f'{latest["occurred_at"]} 在{latest["location"]}，{latest["description"]}'
                  if latest else '暂无物流记录')
        return (f'查到了，订单 {order["order_no"]} 的商品是{products}。'
                f'支付状态：{order["payment_status"]}；配送状态：{order["shipping_status"]}。'
                f'最新物流：{progress}。详情可以点下方订单卡片查看。')
    if not data.get('found'):
        if data.get('reason')=='no_current_order':
            return '请先告诉我订单号，我再按订单里的具体型号查首次使用方法。'
        if data.get('reason')=='ambiguous_order_products':
            return '这笔订单有多个商品，请告诉我您想了解哪一款的首次使用方法。'
        return '我暂时没查到这台商品的首次使用资料，不想凭印象给您操作步骤。'
    items=_first_use_items(data)
    if not items:
        return '我查到了当前订单的商品，但这次没有命中对应的首次使用指南，暂时不能给您可靠步骤。'
    return f'查到了这台 T2080 的首次使用指南。{items[0]["answer"].strip()}\n来源见下方。'

@dataclass
class PreparedTurn:
    conversation_id: int
    message: str
    turns: list
    tools: list
    messages: list
    owner: object
    closed: bool = False
    def close(self):
        if not self.closed:
            self.owner.busy.discard(self.conversation_id)
            self.closed = True

class ChatService:
    def __init__(self,settings,model,repo,knowledge_search=None):
        self.settings=settings;self.model=model;self.repo=repo;self.knowledge_search=knowledge_search;self.busy=set()
        self.system=TOOL_CHAT_PROMPT.format()

    async def prepare(self,message,conversation_id=None):
        # Validate fixed input before creating/writing a conversation.
        tools=build_tools(self.repo,ToolContext(conversation_id or 0,'T'+uuid.uuid4().hex[:30]),self.knowledge_search)
        schema_bytes=max(3072,len(json.dumps([convert_to_openai_tool(t) for t in tools],ensure_ascii=False).encode()))
        trim_turns(self.system,[],[HumanMessage(message)],self.settings.input_budget,schema_bytes)
        if conversation_id is None:conversation_id=await self.repo.new_conversation('local-demo')
        else:
            row=await self.repo.get_conversation(conversation_id)
            if not row or row['user_id']!='local-demo':raise ChatError(404,'会话不存在。')
        if conversation_id in self.busy:raise ChatError(409,'该会话正在回复，请稍后再试。')
        self.busy.add(conversation_id)
        try:
            turns=complete_turns(await self.repo.load_messages(conversation_id))
            tools=build_tools(self.repo,ToolContext(conversation_id,'T'+uuid.uuid4().hex[:30]),self.knowledge_search)
            messages=trim_turns(self.system,turns,[HumanMessage(message)],self.settings.input_budget,schema_bytes)
            return PreparedTurn(conversation_id,message,turns,tools,messages,self)
        except BaseException:
            self.busy.discard(conversation_id);raise

    async def stream(self,turn):
        try:
            cid=turn.conversation_id
            await self.repo.append_message(cid,'user',turn.message)
            yield StreamEvent('session',{'conversation_id':cid})
            normalized=re.sub(r'\s+','',turn.message.casefold())
            s1_pro=re.search(r's1[\s-]*pro',turn.message,re.IGNORECASE) is not None
            order_match=re.search(r'(?<![A-Za-z0-9])DEMO\s*-\s*[0-9]+(?![0-9])',turn.message,re.IGNORECASE)
            direct=None
            if not order_match and s1_pro and any(term in turn.message for term in ('还没收到','没收到','帮我查')):
                direct='可以，我帮您查。请告诉我订单号。'
            elif '明天' in turn.message and any(term in turn.message for term in ('收到','送到','到货')):
                current=await self.repo.get_current_order_detail(cid)
                if current is None:
                    direct='请先告诉我订单号，我才能查看配送信息。'
                else:
                    eta=current['order']['estimated_delivery_at']
                    direct=(f'这笔订单记录的预计送达时间是 {eta}，但实际送达仍以物流更新为准。'
                            if eta else '这笔订单目前没有预计送达时间，我无法确认明天能否收到。')
            if direct is not None:
                await self.repo.append_message(cid,'assistant',direct)
                yield StreamEvent('token',{'text':direct})
                yield StreamEvent('done',{'finish_reason':'stop'})
                return
            forced=None
            if order_match:
                forced={'name':'query_order','args':{'order_id':re.sub(r'\s+','',order_match.group()).upper()}}
            elif s1_pro and any(term in normalized for term in ('第一次怎么用','首次使用','第一次使用')):
                forced={'name':'query_faq','args':{'keyword':turn.message[:64],'scope':'current_order_product'}}
            aggregate=None;answer='';reason=None
            if forced:
                calls=[{'id':'demo-'+uuid.uuid4().hex,'name':forced['name'],'args':forced['args']}]
                reason='tool_calls'
            else:
                upstream=self.model.bind_tools(turn.tools,parallel_tool_calls=False).astream(turn.messages)
                try:
                    async for chunk in upstream:
                        aggregate=chunk if aggregate is None else aggregate+chunk
                        # Tool argument chunks never appear as visible answer text.
                        if chunk.text:
                            if aggregate.tool_call_chunks:continue
                            answer+=chunk.text
                            yield StreamEvent('token',{'text':chunk.text})
                        reason=chunk.response_metadata.get('finish_reason') or reason
                finally:await upstream.aclose()
                if aggregate is None or aggregate.invalid_tool_calls:raise ChatProtocolError('invalid model stream')
                calls=aggregate.tool_calls
            if calls:
                if answer:
                    yield StreamEvent('answer_reset',{})
                    answer=''
                if reason!='tool_calls' or len(calls)>8:raise ChatProtocolError('invalid tool finish')
                ids=[c.get('id') for c in calls]
                if any(not isinstance(i,str) or not i or len(i)>64 for i in ids) or len(set(ids))!=len(ids):raise ChatProtocolError('invalid tool ids')
                stored=[{'id':c['id'],'type':'function','function':{'name':c['name'],'arguments':json.dumps(c['args'],ensure_ascii=False)}} for c in calls]
                current=[HumanMessage(turn.message),AIMessage(content='',tool_calls=calls)]
                # Check actual parameters and bounded results BEFORE any side effect.
                reserved=[ToolMessage(content='x'*(2048 if i==0 else 256),tool_call_id=c['id']) for i,c in enumerate(calls)]
                trim_turns(self.system,[],[*current,*reserved],self.settings.input_budget)
                await self.repo.append_message(cid,'assistant',tool_calls=stored)
                for index,call in enumerate(calls):
                    if index:
                        result=failure('single_tool_limit','本轮只执行第一个工具，此工具未执行')
                        yield StreamEvent('tool_status',{'name':call['name'],'tool_call_id':call['id'],'status':'skipped','attempt':0})
                    else:
                        timeout=self.settings.knowledge_tool_timeout if call['name']=='query_faq' else self.settings.tool_timeout
                        executor=ToolExecutor(turn.tools,timeout_seconds=timeout)
                        result=None
                        async for event in executor.execute(call):
                            if 'result' in event.data:result=event.data['result']
                            # The complete result is persisted, not exposed in a badge.
                            yield StreamEvent(event.event,{k:v for k,v in event.data.items() if k!='result'})
                        if result is None:raise ChatProtocolError('missing tool result')
                    content=json.dumps(result,ensure_ascii=False)
                    await self.repo.append_message(cid,'tool',content,tool_call_id=call['id'])
                    current.append(ToolMessage(content=content,tool_call_id=call['id']))
                    if (index==0 and call['name'] in ('query_order','query_logistics')
                            and result.get('ok') and result['data'].get('found')):
                        yield StreamEvent('order_card',result['data'])
                    if (index==0 and call['name']=='query_faq'
                            and call['args'].get('scope')=='current_order_product'
                            and result.get('ok') and result['data'].get('found')):
                        items=_first_use_items(result['data']) if forced else result['data']['items']
                        if items:yield StreamEvent('knowledge_sources',{'items':items})
                if forced:
                    answer=_fixed_demo_answer(forced['name'],result)
                    reason='stop'
                    yield StreamEvent('token',{'text':answer})
                else:
                    final_messages=trim_turns(self.system,turn.turns,current,self.settings.input_budget)
                    upstream=self.model.bind(tool_choice='none').astream(final_messages) # Explicitly forbid another tool call.
                    answer='';reason=None
                    try:
                        async for chunk in upstream:
                            if chunk.tool_call_chunks:raise ChatProtocolError('unexpected final tool call')
                            if chunk.text:
                                answer+=chunk.text
                                yield StreamEvent('token',{'text':chunk.text})
                            reason=chunk.response_metadata.get('finish_reason') or reason
                    finally:await upstream.aclose()
            if not answer.strip() or reason not in ('stop','length'):raise ChatProtocolError('unfinished response')
            if reason=='stop':await self.repo.append_message(cid,'assistant',answer)
            yield StreamEvent('done',{'finish_reason':reason})
        finally:turn.close()
