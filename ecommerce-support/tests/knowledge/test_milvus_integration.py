import uuid
import pytest

async def test_real_milvus_chinese_bm25_and_hybrid():
    from pymilvus import AsyncMilvusClient
    from app.knowledge.hybrid_vectors import HybridVectors
    from app.knowledge.types import ChunkDraft, ChunkSnapshot
    client = AsyncMilvusClient('http://127.0.0.1:19530')
    name = 'test_ch04_' + uuid.uuid4().hex
    adapter = HybridVectors(client, name)
    try:
        await adapter.ensure_collection()
        row = ChunkSnapshot(1001,ChunkDraft('商品','X100-Pro 的质保期','X100-Pro 支持两年质保。'),'a'*64)
        vector = [1.0]+[0.0]*1023
        assert await adapter.upsert([(row,vector)]) == [1001]
        assert await adapter.count_live() == 1
        await adapter.upsert([(row,vector)])
        assert await adapter.count_live() == 1
        assert [h.id for h in await adapter.retrieve('X100-Pro 质保','X100-Pro 质保',vector,'商品','bm25')] == [1001]
        assert [h.id for h in await adapter.retrieve('X100-Pro 质保','X100-Pro 质保',vector,'商品','hybrid')] == [1001]
        assert await adapter.probe([row])
        await adapter.delete([1001])
        assert await adapter.count_live() == 0
        assert await adapter.retrieve('X100-Pro','X100-Pro',vector,'商品','bm25') == []
    finally:
        if await client.has_collection(name,timeout=5):
            await client.drop_collection(name,timeout=10)
        await client.close()

async def test_real_milvus_upsert_same_id_and_delete():
    from pymilvus import AsyncMilvusClient
    from app.knowledge.vectors import MilvusVectors
    from app.knowledge.types import ChunkDraft, ChunkSnapshot
    client=AsyncMilvusClient('http://127.0.0.1:19530')
    name='test_ch03_'+uuid.uuid4().hex
    adapter=MilvusVectors(client,name)
    try:
        await adapter.ensure_collection()
        row=ChunkSnapshot(1001,ChunkDraft('c','q','a'),'a'*64)
        vector=[1.0]+[0.0]*1023
        assert await adapter.upsert([(row,vector)])==[1001]
        await adapter.upsert([(row,vector)])
        hits=await adapter.search(vector,5)
        assert len(hits)==1 and hits[0].id==1001 and hits[0].score>0.99
        await adapter.delete([1001])
        assert await adapter.search(vector,5)==[]
    finally:
        if await client.has_collection(name,timeout=5):await client.drop_collection(name,timeout=10)
        await client.close()
