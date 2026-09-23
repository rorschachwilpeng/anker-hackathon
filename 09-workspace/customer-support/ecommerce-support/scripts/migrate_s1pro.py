"""Add nullable S1 Pro fields without rebuilding existing tables."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings


KNOWLEDGE_COLUMNS = {
    'product_model': 'VARCHAR(64) NULL',
    'source_title': 'VARCHAR(255) NULL',
    'source_url': 'VARCHAR(1024) NULL',
}


def migrate():
    settings = Settings()
    if settings.database_url is None:
        raise ValueError('DATABASE_URL is required')
    url = make_url(settings.database_url.get_secret_value()).set(drivername='mysql+pymysql')
    engine = create_engine(url)
    added = []
    try:
        for name, definition in KNOWLEDGE_COLUMNS.items():
            if name in {column['name'] for column in inspect(engine).get_columns('knowledge_chunks')}:
                continue
            with engine.begin() as connection:
                connection.execute(text(f'ALTER TABLE knowledge_chunks ADD COLUMN {name} {definition}'))
            added.append(name)
        if 'task_state' not in {column['name'] for column in inspect(engine).get_columns('conversations')}:
            with engine.begin() as connection:
                connection.execute(text('ALTER TABLE conversations ADD COLUMN task_state JSON NULL'))
            added.append('task_state')
    finally:
        engine.dispose()
    return added


if __name__ == '__main__':
    print({'added': migrate()})
