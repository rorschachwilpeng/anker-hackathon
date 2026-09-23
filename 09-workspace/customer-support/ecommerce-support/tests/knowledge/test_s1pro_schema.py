from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings


def test_migration_preserves_existing_knowledge_and_is_idempotent():
    from scripts.migrate_s1pro import migrate

    url = make_url(Settings().database_url.get_secret_value()).set(drivername='mysql+pymysql')
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            result = connection.execute(text("INSERT INTO knowledge_chunks (category, questions, answer) VALUES ('s1pro-migration-test', 'old question', 'old answer')"))
            ident = result.lastrowid
        migrate()
        migrate()
        names = {column['name'] for column in inspect(engine).get_columns('knowledge_chunks')}
        assert {'product_model', 'source_title', 'source_url'} <= names
        with engine.connect() as connection:
            row = connection.execute(text('SELECT questions, product_model, source_title, source_url FROM knowledge_chunks WHERE id = :id'), {'id': ident}).one()
        assert row == ('old question', None, None, None)
    finally:
        if 'ident' in locals():
            with engine.begin() as connection:
                connection.execute(text('DELETE FROM knowledge_chunks WHERE id = :id'), {'id': ident})
        engine.dispose()
