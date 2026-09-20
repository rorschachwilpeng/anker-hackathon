from dataclasses import replace
import pytest
from app.knowledge.types import ChunkDraft

async def test_source_identity_and_changed_content_cas(knowledge_repo):
    repo=knowledge_repo
    a=ChunkDraft('测试','运费','满99元包邮。')
    first=await repo.replace_source('faq:10001',[a])
    assert await repo.mark_done(first[0],str(first[0].id))
    again=await repo.replace_source('faq:10001',[a])
    assert first==again
    assert first[0].id in await repo.load_done([first[0].id])
    changed=await repo.replace_source('faq:10001',[replace(a,answer='满199元包邮。')])
    assert changed[0].id==first[0].id
    assert not await repo.mark_done(first[0],str(first[0].id))
    assert await repo.load_done([first[0].id])=={}

async def test_source_delete_tracks_tombstone(knowledge_repo):
    repo=knowledge_repo
    rows=await repo.replace_source('doc:a',[ChunkDraft('t','q','first'),ChunkDraft('t','q','second')])
    await repo.replace_source('doc:a',[])
    assert set(await repo.tombstones())=={r.id for r in rows}
    assert await repo.load_done([r.id for r in rows])=={}

@pytest.mark.parametrize('point',['after-mysql','after-milvus'])
async def test_retry_after_dual_write_crash(knowledge_repo,point):
    from app.knowledge.ingestion import IngestionService, InjectedFailure
    class Encoder:
        async def encode(self,texts):return [[1.0]*1024 for t in texts]
    class Vectors:
        def __init__(self):self.rows={}
        async def upsert(self,rows):
            self.rows.update({r.id:r for r,v in rows});return [r.id for r,v in rows]
        async def delete(self,ids):
            for i in ids:self.rows.pop(i,None)
    vector=Vectors();repo=knowledge_repo
    rows=await repo.replace_source('doc:a',[ChunkDraft('t','q','a')])
    service=IngestionService(repo,Encoder(),vector,repo.state,failpoint=point)
    with pytest.raises(InjectedFailure):await service.resume()
    assert await repo.load_done([rows[0].id])=={}
    await IngestionService(repo,Encoder(),vector,repo.state).resume()
    assert rows[0].id in await repo.load_done([rows[0].id])
    assert len(vector.rows)==1

async def test_config_change_rejects_old_ack(knowledge_repo):
    repo=knowledge_repo
    rows=await repo.replace_source('doc:a',[ChunkDraft('t','q','a')])
    await repo.state.put('config',{'fingerprint':'another'})
    assert not await repo.mark_done(rows[0],str(rows[0].id))
