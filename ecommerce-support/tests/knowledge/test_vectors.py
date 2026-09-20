import pytest
from app.knowledge.types import ChunkDraft, ChunkSnapshot, VectorError

async def test_upsert_missing_ack_not_success():
    from app.knowledge.vectors import MilvusVectors
    class Client:
        async def upsert(self,**kwargs):return {'ids':[],'upsert_count':0}
    with pytest.raises(VectorError,match='ids'):
        await MilvusVectors(Client()).upsert([(ChunkSnapshot(1,ChunkDraft('c','q','a'),'a'*64),[1.0]*1024)])

async def test_out_of_range_primary_key_rejected_before_rpc():
    from app.knowledge.vectors import MilvusVectors
    with pytest.raises(VectorError,match='id'):
        await MilvusVectors(None).upsert([(ChunkSnapshot(2**63,ChunkDraft('c','q','a'),'a'*64),[1.0]*1024)])
