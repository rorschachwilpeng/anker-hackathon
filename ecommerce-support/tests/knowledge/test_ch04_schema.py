import uuid

from dotenv import dotenv_values
from sqlalchemy import inspect, text


def test_sql_parser_does_not_split_semicolons_inside_comments():
    from scripts.init_ch04 import sql_statements
    script = "SET NAMES utf8mb4; CREATE TABLE sample (c TEXT COMMENT '甲;乙');"
    assert sql_statements(script) == ['SET NAMES utf8mb4', "CREATE TABLE sample (c TEXT COMMENT '甲;乙')"]


async def test_ch04_schema_exact_and_repeatable():
    from app.database import Database
    from scripts.init_ch04 import initialize_ch04

    db = Database(dotenv_values('.env')['DATABASE_URL'])
    tag = 'T' + uuid.uuid4().hex[:15]
    try:
        await initialize_ch04(db)
        async with db.engine.begin() as conn:
            await conn.execute(text("INSERT INTO faith_cases(eval_id,bucket,query,answer,reason) VALUES (:id,'B_model','型号','答','错')"), {'id': tag})
        await initialize_ch04(db)
        async with db.engine.begin() as conn:
            def read(sync_conn):
                inspector = inspect(sync_conn)
                low = inspector.get_columns('low_confidence_questions')
                cases = inspector.get_columns('faith_cases')
                assert {c['name'] for c in low} == {'id','conversation_id','raw_question','source','reason','created_at'}
                assert {c['name'] for c in cases} == {'id','eval_id','bucket','query','strategy','answer','reason','citations','judge_model','status','seen_count','first_seen_at','last_seen_at','resolution','resolved_at'}
                assert str(next(c['type'] for c in cases if c['name']=='citations')).upper() == 'JSON'
                assert {i['name'] for i in inspector.get_indexes('faith_cases')} >= {'uk_eval_id','idx_status','idx_last_seen_at'}
                assert inspector.get_foreign_keys('low_confidence_questions')[0]['referred_table'] == 'conversations'
                assert inspector.get_table_options('faith_cases')['mysql_engine'].lower() == 'innodb'
            await conn.run_sync(read)
            assert (await conn.execute(text('SELECT COUNT(*) FROM faith_cases WHERE eval_id=:id'), {'id':tag})).scalar_one() == 1
    finally:
        async with db.engine.begin() as conn:
            await conn.execute(text('DELETE FROM faith_cases WHERE eval_id=:id'), {'id':tag})
        await db.close()
