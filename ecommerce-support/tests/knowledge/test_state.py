import pytest
from app.knowledge.types import KnowledgeError

async def test_writer_lock_excludes_second_connection(knowledge_repo):
    from app.knowledge.state import StateStore
    second=StateStore(knowledge_repo.state.db)
    with pytest.raises(KnowledgeError,match='busy'):
        async with second.writer_lock():raise AssertionError('should not acquire')

async def test_closed_lock_connection_detected(knowledge_repo):
    from app.knowledge.state import StateStore
    state=StateStore(knowledge_repo.state.db)
    with pytest.raises(KnowledgeError,match='lock'):
        await state.assert_owned()
