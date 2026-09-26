import math
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from app.knowledge.types import ChunkDraft, ChunkSnapshot, KnowledgeError


def settings():
    return SimpleNamespace(rerank_base_url='https://api.siliconflow.cn/v1',
                           rerank_model='BAAI/bge-reranker-v2-m3',
                           rerank_api_key=SecretStr('test-key'),embedding_api_key=None)


async def test_reranker_reorders_by_result_index_and_retries_429():
    from app.knowledge.reranker import SiliconFlowReranker
    attempts = []
    def handler(req):
        body = __import__('json').loads(req.content)
        attempts.append(body)
        assert body['top_n'] == 2 and body['return_documents'] is False
        assert req.headers['authorization'] == 'Bearer test-key'
        if len(attempts) == 1:
            return httpx.Response(429)
        return httpx.Response(200,json={'results':[{'index':1,'relevance_score':0.98},
                                                   {'index':0,'relevance_score':0.3}]})
    rows = [ChunkSnapshot(i,ChunkDraft('商品',f'型号{i}',f'答案{i}'),'a'*64) for i in (1,2)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ranked = await SiliconFlowReranker(client,settings()).rank('型号2',rows,top_n=2)
    assert [r.row.id for r in ranked] == [2,1]
    assert [r.score for r in ranked] == [0.98,0.3]
    assert len(attempts) == 2


@pytest.mark.parametrize('results',[
    [{'index':0,'relevance_score':0.8},{'index':0,'relevance_score':0.7}],
    [{'index':4,'relevance_score':0.8}],
    [{'index':0,'relevance_score':math.nan}],
])
async def test_reranker_rejects_invalid_response(results):
    from app.knowledge.reranker import SiliconFlowReranker
    row = ChunkSnapshot(1,ChunkDraft('商品','问题','答案'),'a'*64)
    import json
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=json.dumps({'results':results},allow_nan=True)))) as client:
        with pytest.raises(KnowledgeError,match='rerank_invalid_response'):
            await SiliconFlowReranker(client,settings()).rank('问题',[row])
