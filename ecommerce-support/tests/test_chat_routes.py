import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessageChunk
from tests_support import MemoryRepository,FakeModel
from app.config import Settings
from app.main import create_app
from app.chat_service import ChatService


def test_stateful_route_and_history_migration():
    settings=Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake')
    repo=MemoryRepository();model=FakeModel([[AIMessageChunk(content='你好',response_metadata={'finish_reason':'stop'})]])
    chat=ChatService(settings,model,repo)
    with TestClient(create_app(service=object(),chat_service=chat)) as client:
        r=client.post('/chat',json={'message':'hi'})
        assert r.status_code==200
        assert 'event: session' in r.text
        assert 'event: done' in r.text
        assert len(repo.rows)==2
        r=client.post('/chat',json={'message':'hi','history':[{'role':'user','content':'a'},{'role':'assistant','content':'b'}]})
        assert r.status_code==422
        r=client.post('/chat',json={'message':'hi','conversation_id':999})
        assert r.status_code==404
        r=client.post('/chat',json={'message':'hi','conversation_id':0})
        assert r.status_code==422
