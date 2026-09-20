import uuid
import pytest
from dotenv import dotenv_values
from sqlalchemy import text


def test_knowledge_settings_require_calibration_only_for_search():
    from app.config import Settings
    s = Settings(_env_file=None, openai_base_url='https://example.com/v1', openai_model='demo', openai_api_key='secret')
    assert s.knowledge_score_threshold is None
    with pytest.raises(ValueError, match='embedding'):
        s.ensure_knowledge_ready()


def test_default_validation_rejects_done_and_accepts_pending():
    from scripts.init_knowledge import validate_default
    validate_default('vectorize_status', "'pending'")
    with pytest.raises(RuntimeError, match='vectorize_status'):
        validate_default('vectorize_status', "'done'")


async def test_repeat_initialize_preserves_rows_and_self_fk():
    from app.database import Database
    from scripts.init_knowledge import initialize_knowledge
    db = Database(dotenv_values('.env')['DATABASE_URL'])
    key = 'test:'+uuid.uuid4().hex
    ids = []
    try:
        await initialize_knowledge(db)
        async with db.engine.begin() as conn:
            a = await conn.execute(text('INSERT INTO knowledge_chunks(category,questions,answer) VALUES (:c,"问题","答案")'), {'c':key})
            ids.append(a.lastrowid)
            b = await conn.execute(text('INSERT INTO knowledge_chunks(category,questions,answer,prev_chunk_id) VALUES (:c,"问题2","答案2",:p)'), {'c':key,'p':ids[0]})
            ids.append(b.lastrowid)
        await initialize_knowledge(db)
        async with db.engine.begin() as conn:
            rows = (await conn.execute(text('SELECT id,vectorize_status,prev_chunk_id FROM knowledge_chunks WHERE category=:c ORDER BY id'), {'c':key})).mappings().all()
            assert [r['id'] for r in rows] == ids
            assert rows[0]['vectorize_status'] == 'pending'
            await conn.execute(text('DELETE FROM knowledge_chunks WHERE id=:id'), {'id':ids[0]})
            assert (await conn.execute(text('SELECT prev_chunk_id FROM knowledge_chunks WHERE id=:id'),{'id':ids[1]})).scalar_one() is None
            comment=(await conn.execute(text("SELECT TABLE_COMMENT FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='knowledge_chunks'"))).scalar_one()
            assert comment == '知识库 chunk 原文权威源'
    finally:
        if ids:
            async with db.engine.begin() as conn:
                await conn.execute(text('DELETE FROM knowledge_chunks WHERE category=:c'),{'c':key})
        await db.close()
