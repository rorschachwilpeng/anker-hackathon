import asyncio
import pytest
from langchain_core.tools import tool

async def test_unknown_and_invalid_are_safe():
    from app.tools.executor import ToolExecutor
    @tool
    async def lookup(order_id: str):
        """Lookup an order."""
        return {'id':order_id}
    executor=ToolExecutor([lookup],timeout_seconds=.01)
    for call,code in [({'id':'c','name':'missing','args':{}},'unknown_tool'),({'id':'c','name':'lookup','args':{}},'validation_error')]:
        events=[x async for x in executor.execute(call)]
        assert events[-1].data['result']['error']['code']==code

async def test_timeout_retries_only_once():
    from app.tools.executor import ToolExecutor
    calls=[]
    @tool
    async def slow():
        """Slow operation."""
        calls.append(1)
        await asyncio.sleep(1)
    events=[e async for e in ToolExecutor([slow],timeout_seconds=.005).execute({'id':'c','name':'slow','args':{}})]
    assert len(calls)==2
    assert events[-1].data['result']['error']['code']=='timeout'
    assert [e.data['status'] for e in events]==['running','retrying','running','failed']

async def test_ticket_context_not_model_controlled():
    from app.tools.business import build_tools,ToolContext
    from app.tools.executor import ToolExecutor
    class Repo:
        async def create_ticket_once(self,*args):return {'ticket_no':args[0],'conversation_id':args[1]}
    tools=build_tools(Repo(),ToolContext(123,'Tfixed'))
    assert {t.name for t in tools}=={'query_order','query_product','query_logistics','query_faq','create_ticket'}
    executor=ToolExecutor(tools)
    events=[e async for e in executor.execute({'id':'c','name':'create_ticket','args':{'description':'人工协助','ticket_type':'咨询'}})]
    assert events[-1].data['result']['data']=={'ticket_no':'Tfixed','conversation_id':123}
    events=[e async for e in executor.execute({'id':'c','name':'create_ticket','args':{'description':'人工协助','ticket_type':'咨询','conversation_id':999}})]
    assert events[-1].data['result']['error']['code']=='validation_error'

async def test_large_output_is_valid_error_and_cancel_propagates():
    from app.tools.executor import ToolExecutor
    @tool
    async def large():
        """Large operation."""
        return 'x'*10000
    events=[e async for e in ToolExecutor([large]).execute({'id':'c','name':'large','args':{}})]
    assert events[-1].data['result']['error']['code']=='result_too_large'
    @tool
    async def cancel():
        """Cancelled operation."""
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        [e async for e in ToolExecutor([cancel]).execute({'id':'c','name':'cancel','args':{}})]

async def test_long_ticket_description_returns_compact_success():
    from app.tools.business import build_tools,ToolContext
    from app.tools.executor import ToolExecutor
    class Repo:
        async def create_ticket_once(self,number,cid,description,kind):
            return {'ticket_no':number,'conversation_id':cid,'description':description,'ticket_type':kind,'status':'待处理'}
    tools=build_tools(Repo(),ToolContext(1,'Tfixed'))
    events=[e async for e in ToolExecutor(tools).execute({'id':'c','name':'create_ticket','args':{'description':'需要人工协助'*150,'ticket_type':'咨询'}})]
    assert events[-1].data['result']['ok'] is True
    assert events[-1].data['result']['data']['ticket_no']=='Tfixed'
