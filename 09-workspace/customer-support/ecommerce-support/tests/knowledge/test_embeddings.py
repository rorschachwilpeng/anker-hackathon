import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from pydantic import SecretStr

settings=SimpleNamespace(embedding_base_url='https://api.siliconflow.cn/v1',embedding_model='BAAI/bge-m3',embedding_api_key=SecretStr('private-test-key'))

async def test_empty_batch_no_http():
    from app.knowledge.embeddings import SiliconFlowEmbeddings
    assert await SiliconFlowEmbeddings(None,None).encode([])==[]

async def test_orders_batch_by_index_and_sends_dense_request():
    from app.knowledge.embeddings import SiliconFlowEmbeddings
    def handler(req):
        assert json.loads(req.content)=={'model':'BAAI/bge-m3','input':['甲','乙'],'encoding_format':'float'}
        return httpx.Response(200,json={'model':'BAAI/bge-m3','data':[{'index':1,'embedding':[2.0]*1024},{'index':0,'embedding':[1.0]*1024}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        vectors=await SiliconFlowEmbeddings(client,settings).encode(['甲','乙'])
    assert vectors[0][0]==1 and vectors[1][0]==2

@pytest.mark.parametrize('kind',['missing','duplicate','dimension','zero','nan','model'])
async def test_invalid_vector_batch_rejected(kind):
    from app.knowledge.embeddings import SiliconFlowEmbeddings
    from app.knowledge.types import EmbeddingError
    body={'model':'BAAI/bge-m3','data':[{'index':0,'embedding':[1.0]*1024},{'index':1,'embedding':[2.0]*1024}]}
    if kind=='missing':body['data'].pop()
    if kind=='duplicate':body['data'][1]['index']=0
    if kind=='dimension':body['data'][0]['embedding'].pop()
    if kind=='zero':body['data'][0]['embedding']=[0]*1024
    if kind=='nan':body['data'][0]['embedding'][0]=float('nan')
    if kind=='model':body['model']='other'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=json.dumps(body)))) as client:
        with pytest.raises(EmbeddingError):await SiliconFlowEmbeddings(client,settings).encode(['甲','乙'])

@pytest.mark.parametrize('status,retryable',[(400,False),(401,False),(429,True),(503,True)])
async def test_errors_sanitized_and_classified(status,retryable):
    from app.knowledge.embeddings import SiliconFlowEmbeddings
    from app.knowledge.types import EmbeddingError
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(status,text='private-test-key'))) as client:
        with pytest.raises(EmbeddingError) as error: await SiliconFlowEmbeddings(client,settings).encode(['甲'])
        assert error.value.retryable==retryable
        assert 'private-test-key' not in str(error.value)

async def test_cancellation_propagates():
    from app.knowledge.embeddings import SiliconFlowEmbeddings
    async def handler(r):raise asyncio.CancelledError()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError): await SiliconFlowEmbeddings(client,settings).encode(['甲'])
