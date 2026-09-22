import pytest
from langchain_core.messages import AIMessageChunk
from tests_support import MemoryRepository, FakeModel

async def test_tool_round_persists_pair_and_final():
    from app.chat_service import ChatService
    from app.config import Settings
    repo=MemoryRepository()
    model=FakeModel([[AIMessageChunk(content='',tool_call_chunks=[{'name':'query_logistics','args':'{"order_id":"1001"}','id':'call_1','index':0}],response_metadata={'finish_reason':'tool_calls'})],[AIMessageChunk(content='模拟物流运输中',response_metadata={'finish_reason':'stop'})]])
    svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),model,repo)
    turn=await svc.prepare('订单 1001 的物流到哪了',None)
    events=[e async for e in svc.stream(turn)]
    assert [r['role'] for r in repo.rows]==['user','assistant','tool','assistant']
    assert repo.rows[2]['tool_call_id']=='call_1'
    assert len(model.requests)==2
    assert model.requests[1]['bound'] is False
    assert events[-1].event=='done'
    assert any(e.event=='tool_status' for e in events)

async def test_second_tool_skipped_and_incomplete_turn_not_replayed():
    from app.chat_service import ChatService
    from app.config import Settings
    repo=MemoryRepository()
    chunks=[{'name':'query_order','args':'{"order_id":"1001"}','id':'c1','index':0},{'name':'query_logistics','args':'{"order_id":"1001"}','id':'c2','index':1}]
    model=FakeModel([[AIMessageChunk(content='',tool_call_chunks=chunks,response_metadata={'finish_reason':'tool_calls'})],[AIMessageChunk(content='partial',response_metadata={'finish_reason':'length'})],[AIMessageChunk(content='你好',response_metadata={'finish_reason':'stop'})]])
    svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),model,repo)
    first=await svc.prepare('查订单',None)
    events=[e async for e in svc.stream(first)]
    assert len([e for e in events if e.event=='tool_status' and e.data['status']=='running'])==1
    assert len([r for r in repo.rows if r['role']=='tool'])==2
    assert repo.rows[-1]['role']=='tool'
    second=await svc.prepare('你好',first.conversation_id)
    [e async for e in svc.stream(second)]
    assert len(model.requests[-1]['messages'])==2

async def test_busy_and_missing_and_lock_cleanup():
    from app.chat_service import ChatService,ChatError
    from app.config import Settings
    repo=MemoryRepository();model=FakeModel([[AIMessageChunk(content='hi',response_metadata={'finish_reason':'stop'})]])
    svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),model,repo)
    with pytest.raises(ChatError) as exc:await svc.prepare('hello',777)
    assert exc.value.status==404
    turn=await svc.prepare('hello',None)
    with pytest.raises(ChatError) as exc:await svc.prepare('again',turn.conversation_id)
    assert exc.value.status==409
    stream=svc.stream(turn)
    await anext(stream)
    await stream.aclose()
    assert turn.conversation_id not in svc.busy

async def test_bad_call_id_and_empty_stream_fail_without_done():
    from app.chat_service import ChatService
    from app.config import Settings
    for chunks in [[],[AIMessageChunk(content='',tool_call_chunks=[{'name':'query_order','args':'{}','id':'x'*65,'index':0}],response_metadata={'finish_reason':'tool_calls'})]]:
        svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),FakeModel([chunks]),MemoryRepository())
        turn=await svc.prepare('hello',None)
        with pytest.raises(RuntimeError):[e async for e in svc.stream(turn)]
        assert not svc.busy

async def test_old_turn_close_cannot_release_new_turn_lock():
    from app.chat_service import ChatService,ChatError
    from app.config import Settings
    svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),FakeModel([]),MemoryRepository())
    first=await svc.prepare('hello',None)
    first.close()
    second=await svc.prepare('again',first.conversation_id)
    first.close()
    with pytest.raises(ChatError) as exc:await svc.prepare('third',first.conversation_id)
    assert exc.value.status==409
    second.close()

async def test_tool_result_budget_reserved_before_accepting_long_input():
    from app.chat_service import ChatService
    from app.config import Settings
    from app.context import ContextBudgetExceeded
    svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),FakeModel([]),MemoryRepository())
    with pytest.raises(ContextBudgetExceeded):await svc.prepare('x'*4400,None)
    assert not svc.repo.rows

async def test_tool_preamble_reset_before_final_stream():
    from app.chat_service import ChatService
    from app.config import Settings
    model=FakeModel([[AIMessageChunk(content='我来帮您查询'),AIMessageChunk(content='',tool_call_chunks=[{'name':'query_faq','args':'{"keyword":"退货政策"}','id':'c','index':0}],response_metadata={'finish_reason':'tool_calls'})],[AIMessageChunk(content='没查到',response_metadata={'finish_reason':'stop'})]])
    repo=MemoryRepository();svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),model,repo)
    events=[e async for e in svc.stream(await svc.prepare('退货政策',None))]
    assert any(e.event=='answer_reset' for e in events)
    assert repo.rows[-1]['content']=='没查到'
    assert events[-1].event=='done'

async def test_final_request_explicitly_forbids_tools():
    from app.chat_service import ChatService
    from app.config import Settings
    model=FakeModel([[AIMessageChunk(content='',tool_call_chunks=[{'name':'query_faq','args':'{"keyword":"退货政策"}','id':'c','index':0}],response_metadata={'finish_reason':'tool_calls'})],[AIMessageChunk(content='没有查到',response_metadata={'finish_reason':'stop'})]])
    svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),model,MemoryRepository())
    [e async for e in svc.stream(await svc.prepare('退货政策',None))]
    assert model.requests[-1].get('tool_choice')=='none'


async def test_dense_faq_legacy_chunk_ids_have_authoritative_citation_mapping():
    from app.chat_service import ChatService
    from app.config import Settings
    from app.knowledge.types import ChunkDraft,ChunkSnapshot
    class KnowledgeRepo:
        async def load_done(self,ids):
            return {125:ChunkSnapshot(125,ChunkDraft('退货','退货政策','签收7天内可申请退货。','政策/退货'),'a'*64)}
    class Search:
        repo=KnowledgeRepo()
        async def find(self,keyword):
            return [{'id':125,'question':'退货政策','answer':'签收7天内可申请退货。','category':'退货'}]
    call=AIMessageChunk(content='',tool_call_chunks=[{'name':'query_faq','args':'{"keyword":"退货政策"}','id':'c','index':0}],response_metadata={'finish_reason':'tool_calls'})
    model=FakeModel([[call],[AIMessageChunk(content='签收7天内可申请退货[125]。',response_metadata={'finish_reason':'stop'})]])
    svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),model,MemoryRepository(),Search())
    events=[e async for e in svc.stream(await svc.prepare('退货政策是怎样的？',None))]
    citations=[e.data['items'] for e in events if e.event=='citations']
    assert citations==[[{'n':125,'chunk_id':125,'section_path':'政策/退货'}]]
    assert events[-1].event=='done'


async def test_dense_faq_without_citation_keeps_previous_answer_behavior():
    from app.chat_service import ChatService
    from app.config import Settings
    from app.knowledge.types import ChunkDraft,ChunkSnapshot
    class KnowledgeRepo:
        async def load_done(self,ids):
            return {7:ChunkSnapshot(7,ChunkDraft('配送','运费','满99元包邮。','政策/运费'),'a'*64)}
    class Search:
        repo=KnowledgeRepo()
        async def find(self,keyword):return [{'id':7,'question':'运费','answer':'满99元包邮。','category':'配送'}]
    call=AIMessageChunk(content='',tool_call_chunks=[{'name':'query_faq','args':'{"keyword":"邮费"}','id':'c','index':0}],response_metadata={'finish_reason':'tool_calls'})
    repo=MemoryRepository()
    svc=ChatService(Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake'),
                    FakeModel([[call],[AIMessageChunk(content='满99元包邮。',response_metadata={'finish_reason':'stop'})]]),repo,Search())
    [e async for e in svc.stream(await svc.prepare('邮费是多少',None))]
    assert repo.rows[-1]['content']=='满99元包邮。'
