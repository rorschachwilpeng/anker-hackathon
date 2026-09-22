import uuid
import json

from dotenv import dotenv_values
from sqlalchemy import text


async def test_record_faith_case_reopens_resolved():
    from app.database import Database
    from app.knowledge.quality_repository import QualityRepository
    from scripts.init_ch04 import initialize_ch04

    db = Database(dotenv_values('.env')['DATABASE_URL'])
    await initialize_ch04(db)
    repo = QualityRepository(db.sessions)
    ident = 'T' + uuid.uuid4().hex[:15]
    case = dict(eval_id=ident, bucket='B_model', query='X100 型号?', strategy='hybrid_rerank', answer='答一', reason='无证据', citations=[{'n':1,'chunk_id':4,'section_path':'商品','question':'型号','answer':'原文'}], judge_model='judge')
    try:
        assert await repo.record_faith_case(case) == 'new'
        await repo.resolve(ident,'已解决','已补充型号原文')
        assert await repo.record_faith_case({**case,'answer':'答二'}) == 'recurrent'
        async with db.sessions() as s:
            row=(await s.execute(text('SELECT answer,status,seen_count,resolution,resolved_at,citations FROM faith_cases WHERE eval_id=:id'),{'id':ident})).mappings().one()
            assert (row['answer'],row['status'],row['seen_count'],row['resolution']) == ('答二','未解决',2,None)
            assert row['resolved_at'] is not None
            assert json.loads(row['citations'])[0]['chunk_id'] == 4
    finally:
        async with db.sessions.begin() as s:
            await s.execute(text('DELETE FROM faith_cases WHERE eval_id=:id'),{'id':ident})
        await db.close()


async def test_record_low_once_by_message_id():
    from app.database import Database
    from app.knowledge.quality_repository import QualityRepository
    from scripts.init_ch04 import initialize_ch04

    db = Database(dotenv_values('.env')['DATABASE_URL'])
    await initialize_ch04(db)
    repo = QualityRepository(db.sessions)
    conversation_id = message_id = low_id = None
    try:
        async with db.sessions.begin() as s:
            conversation_id=(await s.execute(text("INSERT INTO conversations(user_id) VALUES ('test-quality')"))).lastrowid
            message_id=(await s.execute(text("INSERT INTO messages(conversation_id,role,content) VALUES (:id,'user','原话')"),{'id':conversation_id})).lastrowid
        low_id=await repo.record_low(message_id,conversation_id,'原话','证据不足')
        assert await repo.record_low(message_id,conversation_id,'原话','证据不足') == low_id
        async with db.sessions() as s:
            assert (await s.execute(text('SELECT COUNT(*) FROM low_confidence_questions WHERE id=:id'),{'id':low_id})).scalar_one() == 1
    finally:
        async with db.sessions.begin() as s:
            if message_id:
                await s.execute(text('DELETE FROM knowledge_ingestion_state WHERE state_key=:k'),{'k':'knowledge:quality:low:'+str(message_id)})
            if low_id:
                await s.execute(text('DELETE FROM low_confidence_questions WHERE id=:id'),{'id':low_id})
            if conversation_id:
                await s.execute(text('DELETE FROM messages WHERE conversation_id=:id'),{'id':conversation_id})
                await s.execute(text('DELETE FROM conversations WHERE id=:id'),{'id':conversation_id})
        await db.close()
