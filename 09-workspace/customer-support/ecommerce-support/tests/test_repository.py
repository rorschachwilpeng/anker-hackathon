"""Real MySQL boundary checks; unique rows, never clears application data."""
import importlib.util
import uuid
import pytest
from dotenv import dotenv_values


def test_repository_feature_exists():
    assert importlib.util.find_spec('app.repositories') is not None

@pytest.fixture
async def repo():
    from app.database import Database
    from app.repositories import Repository
    config = dotenv_values('.env')
    if not config.get('DATABASE_URL'):
        pytest.skip('MySQL DATABASE_URL required')
    db = Database(config['DATABASE_URL'])
    yield Repository(db.sessions)
    await db.close()

async def test_faq_literal_search(repo):
    assert await repo.find_faq('退货政策')
    assert await repo.find_faq('邮费') == []
    assert await repo.find_faq('%') == []
    assert await repo.find_faq('_') == []
    assert await repo.find_faq('\\') == []

async def test_ticket_atomic_idempotency_and_conflict(repo):
    cid = await repo.new_conversation('test-'+uuid.uuid4().hex)
    number = 'T'+uuid.uuid4().hex[:30]
    first = await repo.create_ticket_once(number,cid,'需要人工协助','咨询')
    second = await repo.create_ticket_once(number,cid,'需要人工协助','咨询')
    assert first == second
    assert (await repo.get_conversation(cid))['status'] == '已转人工'
    with pytest.raises(ValueError):
        await repo.create_ticket_once(number,cid,'不同问题','咨询')

async def test_messages_preserve_call_ids_and_nullable_content(repo):
    cid = await repo.new_conversation('test-'+uuid.uuid4().hex)
    call = [{'id':'call_test','type':'function','function':{'name':'query_faq','arguments':'{"keyword":"退货政策"}'}}]
    await repo.append_message(cid,'user','退货政策是什么')
    await repo.append_message(cid,'assistant',tool_calls=call)
    await repo.append_message(cid,'tool','{"ok":true}',tool_call_id='call_test')
    rows = await repo.load_messages(cid)
    assert [r['role'] for r in rows] == ['user','assistant','tool']
    assert rows[1]['content'] is None
    assert rows[1]['tool_calls'] == call
    assert rows[2]['tool_call_id'] == 'call_test'

async def test_foreign_key_rejects_missing_conversation(repo):
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        await repo.append_message(18446744073709551614,'user','invalid')
