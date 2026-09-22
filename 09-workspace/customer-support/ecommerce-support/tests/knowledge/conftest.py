import uuid
import pytest
from dotenv import dotenv_values
from sqlalchemy import delete
from app.database import Database
from app.knowledge.models import knowledge_chunks, knowledge_ingestion_state

@pytest.fixture
async def knowledge_repo():
    from app.knowledge.repository import KnowledgeRepository
    from app.knowledge.state import StateStore
    db=Database(dotenv_values('.env')['DATABASE_URL'])
    namespace='test_'+uuid.uuid4().hex
    state=StateStore(db,namespace=namespace)
    repo=KnowledgeRepository(db.sessions,state=state,fingerprint='test-config')
    async with state.writer_lock():
        await repo.ensure_config()
        try:yield repo
        finally:
            records=await state.entries('source:')
            ids=[i for key,p in records for i in p.get('ids',[])]
            async with db.sessions.begin() as session:
                if ids:await session.execute(delete(knowledge_chunks).where(knowledge_chunks.c.id.in_(ids)))
                await session.execute(delete(knowledge_ingestion_state).where(knowledge_ingestion_state.c.state_key.like(namespace+':%')))
    await db.close()
