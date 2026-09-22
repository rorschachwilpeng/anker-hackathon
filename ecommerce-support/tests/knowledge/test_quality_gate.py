import pytest
from langchain_core.messages import AIMessageChunk

from app.knowledge.types import ChunkDraft, ChunkSnapshot
from tests_support import FakeModel, MemoryRepository


class SearchRepo:
    async def load_done(self, ids):
        return {1:ChunkSnapshot(1,ChunkDraft('配送','运费','满99元包邮。','配送/运费'),'a'*64)}


class Search:
    repo = SearchRepo()
    async def find(self, keyword):
        return [{'id':1,'question':'运费','answer':'满99元包邮。','category':'配送'}]


class Gate:
    def __init__(self,useful):
        self.useful=useful
    async def check(self,question,evidence):
        from app.knowledge.quality import Useful
        return Useful(useful=self.useful,reason='没有足够证据' if not self.useful else '足够')


class LowPool:
    def __init__(self):self.calls=[]
    async def record_low(self,*args):self.calls.append(args);return len(self.calls)


def config():
    from app.config import Settings
    return Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake')


def faq_call():
    return AIMessageChunk(content='',tool_call_chunks=[{'name':'query_faq','args':'{"keyword":"邮费"}','id':'c1','index':0}],response_metadata={'finish_reason':'tool_calls'})


async def test_useful_false_refuses_and_records_once_without_final_generation():
    from app.chat_service import ChatService
    repo, pool = MemoryRepository(), LowPool()
    model = FakeModel([[faq_call()]])
    service = ChatService(config(),model,repo,Search(),quality_gate=Gate(False),quality_repo=pool)
    events = [e async for e in service.stream(await service.prepare('不知道的政策',None))]
    assert len(pool.calls) == 1 and pool.calls[0][2] == '不知道的政策'
    assert len(model.requests) == 1
    assert '不足以确认' in repo.rows[-1]['content']
    assert events[-1].event == 'done' and events[-1].data['message_id'] == len(repo.rows)


async def test_invalid_citation_resets_stream_and_does_not_save_candidate():
    from app.chat_service import ChatService
    repo = MemoryRepository()
    model = FakeModel([[faq_call()],[AIMessageChunk(content='规则见[11]',response_metadata={'finish_reason':'stop'})]])
    service = ChatService(config(),model,repo,Search(),quality_gate=Gate(True),quality_repo=LowPool())
    events = [e async for e in service.stream(await service.prepare('邮费是多少',None))]
    assert any(e.event=='answer_reset' for e in events)
    assert '规则见' not in repo.rows[-1]['content']
    assert events[-1].event=='done'


async def test_unclosed_citation_is_not_streamed_or_saved():
    from app.chat_service import ChatService
    repo = MemoryRepository()
    model = FakeModel([[faq_call()],[
        AIMessageChunk(content='规则见['),
        AIMessageChunk(content='11',response_metadata={'finish_reason':'stop'}),
    ]])
    service = ChatService(config(),model,repo,Search(),quality_gate=Gate(True),quality_repo=LowPool())
    events = [e async for e in service.stream(await service.prepare('邮费是多少',None))]
    assert not any(e.event=='token' and '[' in e.data['text'] for e in events)
    assert '不足以确认' in repo.rows[-1]['content']


async def test_self_check_invalid_schema_is_error_not_useful():
    from app.knowledge.quality import EvidenceGate
    class Model:
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):return {'useful':'yes','reason':'足够'}
    with pytest.raises(Exception):
        await EvidenceGate(Model()).check('问题',[])
