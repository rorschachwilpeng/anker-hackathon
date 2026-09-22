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


async def test_document_reimport_replays_accepted_questions_and_changed_policy_moves_them_to_review(knowledge_repo):
    from app.knowledge.dedup import DedupDecision
    from app.knowledge.models import qa_extraction_staging
    from sqlalchemy import delete, insert, select

    base = ChunkDraft('配送', '运费说明', '满99元包邮。', content_type='policy')
    rows = await knowledge_repo.replace_source('doc:policy', [base])
    async with knowledge_repo.sessions.begin() as session:
        inserted = await session.execute(insert(qa_extraction_staging).values(
            batch_no='reimport', source_ref='901000', question='邮费是多少', answer=base.answer, status='extracted'
        ))
        staging_id = inserted.inserted_primary_key[0]
    candidate = {'staging_id': staging_id, 'conversation_id': 901000, 'category': '配送', 'question': '邮费是多少', 'answer': base.answer}
    try:
        await knowledge_repo.apply_dedup(candidate, DedupDecision(action='merge', target_id=rows[0].id, reason='same fact'))
        same = await knowledge_repo.replace_source('doc:policy', [base])
        assert same[0].draft.questions == '运费说明\n邮费是多少'

        changed = await knowledge_repo.replace_source('doc:policy', [ChunkDraft('配送', '运费说明', '满199元包邮。', content_type='policy')])
        assert changed[0].draft.questions == '运费说明'
        async with knowledge_repo.sessions() as session:
            status = (await session.execute(select(qa_extraction_staging.c.status).where(qa_extraction_staging.c.id == staging_id))).scalar_one()
        decision = await knowledge_repo.state.get('dedup:staging:' + str(staging_id))
        assert status == 'extracted'
        assert decision['action'] == 'review'
        assert decision['reason'] == 'authoritative_source_changed'
    finally:
        async with knowledge_repo.sessions.begin() as session:
            await session.execute(delete(qa_extraction_staging).where(qa_extraction_staging.c.id == staging_id))


async def test_lost_writer_lock_rejects_mining_and_dedup_writes(knowledge_repo, monkeypatch):
    from app.knowledge.dedup import DedupDecision
    from app.knowledge.types import KnowledgeError

    async def lost():
        raise KnowledgeError('writer_lock_lost')

    monkeypatch.setattr(knowledge_repo.state, 'assert_owned', lost)
    with pytest.raises(KnowledgeError, match='writer_lock_lost'):
        await knowledge_repo.commit_mining_run('lost-run', [], [], [])
    with pytest.raises(KnowledgeError, match='writer_lock_lost'):
        await knowledge_repo.apply_dedup(
            {'staging_id': 999999, 'category': '测试', 'question': '问题', 'answer': '答案'},
            DedupDecision(action='new', reason='new'),
        )
