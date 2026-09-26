from fastapi.testclient import TestClient
from app.main import create_app
from app.knowledge.types import ChunkDraft, ChunkSnapshot


def test_chunk_detail_unknown_returns_404():
    with TestClient(create_app(service=object(),chat_service=object())) as client:
        assert client.get('/knowledge/chunks/1234567').status_code == 404


def test_chunk_detail_returns_authoritative_text_and_section():
    class Repo:
        async def load_done(self,ids):
            return {9:ChunkSnapshot(9,ChunkDraft('退货','七天退货','完整原文。','退货/条件'),'a'*64)}
    app = create_app(service=object(),chat_service=object())
    app.state.knowledge_repo = Repo()
    with TestClient(app) as client:
        body=client.get('/knowledge/chunks/9').json()
        assert body['chunk_id']==9 and body['section_path']=='退货/条件'
        assert body['answer']=='完整原文。'


def test_chunk_detail_never_exposes_source_outside_knowledge_root():
    class Repo:
        async def load_done(self,ids):
            return {9:ChunkSnapshot(9,ChunkDraft('退货','七天退货','完整原文。','退货/条件'),'a'*64)}
    class State:
        async def entries(self,prefix):
            return [('source:unsafe',{'ids':[9],'source':'doc:/etc/passwd'})]
    app=create_app(service=object(),chat_service=object())
    app.state.knowledge_repo=Repo()
    app.state.knowledge_state=State()
    with TestClient(app) as client:
        response=client.get('/knowledge/chunks/9')
        assert response.status_code==404
        assert '/etc/passwd' not in response.text
