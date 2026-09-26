"""Test doubles for external persistence/model boundaries only."""
class MemoryRepository:
    def __init__(self):self.rows=[];self.conversations={}
    async def new_conversation(self,user_id):
        cid=len(self.conversations)+1;self.conversations[cid]={'id':cid,'user_id':user_id};return cid
    async def get_conversation(self,cid):return self.conversations.get(cid)
    async def load_messages(self,cid):return [r for r in self.rows if r['conversation_id']==cid]
    async def append_message(self,cid,role,content=None,tool_calls=None,tool_call_id=None):
        self.rows.append(dict(conversation_id=cid,role=role,content=content,tool_calls=tool_calls,tool_call_id=tool_call_id));return len(self.rows)
    async def find_faq(self,keyword):return []
class FakeModel:
    def __init__(self,responses):self.responses=list(responses);self.requests=[]
    def bind_tools(self,tools,**kwargs):
        parent=self
        class Bound:
            def astream(self,messages):return parent.generate(messages,True)
        return Bound()
    def bind(self,**kwargs):
        parent=self
        class Bound:
            async def astream(self,messages):
                async for chunk in parent.generate(messages,False,kwargs.get('tool_choice')):yield chunk
        return Bound()
    def astream(self,messages):return self.generate(messages,False)
    async def generate(self,messages,bound,tool_choice=None):
        self.requests.append({'messages':list(messages),'bound':bound,'tool_choice':tool_choice})
        for chunk in self.responses.pop(0):yield chunk
