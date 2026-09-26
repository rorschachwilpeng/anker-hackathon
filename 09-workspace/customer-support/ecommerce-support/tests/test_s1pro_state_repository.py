import asyncio

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.database import Database
from app.repositories import Repository


def test_state_migration_preserves_old_conversation_and_reserves_once():
    from app.s1pro.state import S1ProState
    from scripts.migrate_s1pro import migrate

    settings=Settings()
    url=settings.database_url.get_secret_value()
    engine=create_engine(make_url(url).set(drivername='mysql+pymysql'))
    with engine.begin() as connection:
        result=connection.execute(text("INSERT INTO conversations (user_id) VALUES ('s1pro-migration-test')"))
        old_id=result.lastrowid
    try:
        migrate();migrate()
        assert 'task_state' in {column['name'] for column in inspect(engine).get_columns('conversations')}
        with engine.connect() as connection:
            assert connection.execute(text('SELECT task_state FROM conversations WHERE id = :id'),{'id':old_id}).scalar_one() is None

        async def exercise():
            database=Database(url)
            try:
                repo=Repository(database.sessions)
                assert await repo.load_s1pro_state(old_id)==S1ProState()
                state=S1ProState(confirmed_model='T2080',suggested_steps=['清理侧刷'],completed_steps=['清理侧刷'])
                await repo.save_s1pro_state(old_id,state)
                first=await repo.reserve_s1pro_ticket(old_id)
                second=await repo.reserve_s1pro_ticket(old_id)
                assert first==second and first.startswith('T')
            finally:
                await database.close()
            reopened=Database(url)
            try:
                restored=await Repository(reopened.sessions).load_s1pro_state(old_id)
                assert restored.confirmed_model=='T2080'
                assert restored.completed_steps==['清理侧刷']
                assert restored.ticket_no==first
            finally:
                await reopened.close()
        asyncio.run(exercise())
    finally:
        with engine.begin() as connection:
            connection.execute(text('DELETE FROM conversations WHERE id = :id'),{'id':old_id})
        engine.dispose()
