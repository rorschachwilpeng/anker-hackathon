import pytest

from app.knowledge.types import ChunkDraft, ChunkSnapshot, VectorError


class SpyClient:
    def __init__(self):
        self.requests = None
        self.ranker = None
        self.data = None

    async def hybrid_search(self, collection_name, reqs, ranker, limit, **kwargs):
        self.requests = reqs
        self.ranker = ranker
        assert collection_name == 'knowledge_hybrid_v1' and limit == 50
        return [[{'id': 12, 'distance': 0.8, 'entity': {'content_hash': 'a'*64}}]]

    async def search(self, collection_name, data, anns_field, limit, **kwargs):
        self.data = data
        assert collection_name == 'knowledge_hybrid_v1' and limit == 50
        return [[{'id':12,'distance':0.8,'entity':{'content_hash':'a'*64}}]]


async def test_hybrid_has_two_filtered_top50_legs():
    from app.knowledge.hybrid_vectors import HybridVectors
    client = SpyClient()
    adapter = HybridVectors(client)
    hits = await adapter.retrieve('型号 X100', 'X100 Pro 价格', [1.0]*1024, '商品', 'hybrid')
    assert hits[0].id == 12 and hits[0].content_hash == 'a'*64
    assert len(client.requests) == 2
    assert [req.limit for req in client.requests] == [50,50]
    assert [req.anns_field for req in client.requests] == ['vector','sparse']
    assert client.requests[0].data == [[1.0]*1024]
    assert client.requests[1].data == ['X100 Pro 价格']
    assert client.requests[0].expr == client.requests[1].expr
    assert '商品' in client.requests[0].expr
    assert type(client.ranker).__name__ == 'RRFRanker'


async def test_bm25_uses_text_query_and_invalid_category_rejected():
    from app.knowledge.hybrid_vectors import HybridVectors
    client = SpyClient()
    adapter = HybridVectors(client)
    hits = await adapter.retrieve('X100','X100-Pro',[1.0]*1024,None,'bm25')
    assert hits[0].id == 12 and client.data == ['X100-Pro']
    with pytest.raises(VectorError, match='category'):
        await adapter.retrieve('X100','X100',[1.0]*1024,'商品" or id > 0','hybrid')


async def test_upsert_rejects_bad_ack_and_bad_ids_before_request():
    from app.knowledge.hybrid_vectors import HybridVectors
    class Client:
        async def upsert(self, **kwargs):
            assert set(kwargs['data'][0]) == {'id','vector','text','category','content_hash'}
            return {'ids': []}
    row = ChunkSnapshot(1, ChunkDraft('商品','X100','原文'),'a'*64)
    with pytest.raises(VectorError, match='ids'):
        await HybridVectors(Client()).upsert([(row,[1.0]*1024)])
    with pytest.raises(VectorError, match='id'):
        await HybridVectors(None).upsert([(ChunkSnapshot(2**63,row.draft,row.content_hash),[1.0]*1024)])
