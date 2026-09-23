import pytest


async def test_s1pro_tool_accepts_only_confirmed_t2080():
    from app.tools.business import build_s1pro_tools
    from app.tools.executor import ToolExecutor
    class Search:
        async def find_for_model(self,query,model):
            assert model=='T2080'
            return [{'id':1,'answer':'check brush','product_model':'T2080','source_title':'official','source_url':'https://service.eufy.com/a'}]
    tools=build_s1pro_tools(Search())
    assert [tool.name for tool in tools]==['search_support_knowledge']
    executor=ToolExecutor(tools)
    valid=[event async for event in executor.execute({'id':'c1','name':'search_support_knowledge','args':{'query':'吸力变弱','confirmed_model':'T2080'}})]
    assert valid[-1].data['result']['data']['found'] is True
    invalid=[event async for event in executor.execute({'id':'c2','name':'search_support_knowledge','args':{'query':'吸力变弱','confirmed_model':'T9999'}})]
    assert invalid[-1].data['result']['error']['code']=='validation_error'
