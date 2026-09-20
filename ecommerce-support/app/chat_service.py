import json
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
    def __init__(self,settings,model,repo):
        self.settings=settings;self.model=model;self.repo=repo;self.busy=set()
        self.system=TOOL_CHAT_PROMPT.format()

    async def prepare(self,message,conversation_id=None):
        # Validate fixed input before creating/writing a conversation.
        tools=build_tools(self.repo,ToolContext(conversation_id or 0,'T'+uuid.uuid4().hex[:30]))
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
            tools=build_tools(self.repo,ToolContext(conversation_id,'T'+uuid.uuid4().hex[:30]))
            messages=trim_turns(self.system,turns,[HumanMessage(message)],self.settings.input_budget,schema_bytes)
            return PreparedTurn(conversation_id,message,turns,tools,messages,self)
        except BaseException:
            self.busy.discard(conversation_id);raise

    async def stream(self,turn):
        try:
            cid=turn.conversation_id
            await self.repo.append_message(cid,'user',turn.message)
            yield StreamEvent('session',{'conversation_id':cid})
            aggregate=None;answer='';reason=None
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
                executor=ToolExecutor(turn.tools,timeout_seconds=self.settings.tool_timeout)
                for index,call in enumerate(calls):
                    if index:
                        result=failure('single_tool_limit','本轮只执行第一个工具，此工具未执行')
                        yield StreamEvent('tool_status',{'name':call['name'],'tool_call_id':call['id'],'status':'skipped','attempt':0})
                    else:
                        result=None
                        async for event in executor.execute(call):
                            if 'result' in event.data:result=event.data['result']
                            # The complete result is persisted, not exposed in a badge.
                            yield StreamEvent(event.event,{k:v for k,v in event.data.items() if k!='result'})
                        if result is None:raise ChatProtocolError('missing tool result')
                    content=json.dumps(result,ensure_ascii=False)
                    await self.repo.append_message(cid,'tool',content,tool_call_id=call['id'])
                    current.append(ToolMessage(content=content,tool_call_id=call['id']))
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
