"""Create missing ch03 tables; validate, never alter existing tables."""
import asyncio
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, text
from app.database import Database
from app.knowledge.models import metadata


def normalized_default(value):
    return str(value or '').lower().replace('()', '').replace("'", '').strip()


def validate_default(name, actual):
    expected = {'created_at':'current_timestamp', 'updated_at':'current_timestamp on update current_timestamp',
                'is_key_clause':'0', 'vectorize_status':'pending', 'status':'extracted'}.get(name)
    if expected is not None and normalized_default(actual) != expected:
        raise RuntimeError('schema default mismatch: '+name)


def validate_schema(conn):
    inspector = inspect(conn)
    for table in metadata.tables.values():
        columns = {c['name']:c for c in inspector.get_columns(table.name)}
        if set(columns) != set(table.c.keys()):
            raise RuntimeError('schema columns mismatch: '+table.name)
        for col in table.c:
            actual=columns[col.name]
            if str(actual['type'].compile(dialect=conn.dialect)).lower() != str(col.type.compile(dialect=conn.dialect)).lower() or actual['nullable'] != col.nullable:
                raise RuntimeError('schema type mismatch: '+table.name+'.'+col.name)
            validate_default(col.name, actual.get('default'))
            if col.name=='id' and not actual.get('autoincrement'):
                raise RuntimeError('schema autoincrement mismatch: '+table.name)
        if set(inspector.get_pk_constraint(table.name)['constrained_columns']) != {c.name for c in table.primary_key}:
            raise RuntimeError('schema primary key mismatch: '+table.name)
        indexes={i['name']:tuple(i['column_names']) for i in inspector.get_indexes(table.name)}
        if any(indexes.get(i.name) != tuple(c.name for c in i.columns) for i in table.indexes):
            raise RuntimeError('schema indexes mismatch: '+table.name)
        def action(v):
            return (v or 'RESTRICT').upper().replace('NO ACTION','RESTRICT')
        expected={(f.constraint.name, f.parent.name, f.column.table.name, f.column.name, action(f.ondelete), action(f.onupdate)) for f in table.foreign_keys}
        actual={(f['name'],f['constrained_columns'][0],f['referred_table'],f['referred_columns'][0],action(f.get('options',{}).get('ondelete')),action(f.get('options',{}).get('onupdate'))) for f in inspector.get_foreign_keys(table.name)}
        if expected != actual:
            raise RuntimeError('schema foreign keys mismatch: '+table.name)
        opts=inspector.get_table_options(table.name)
        if opts.get('mysql_engine','').lower()!='innodb' or not opts.get('mysql_collate','').startswith('utf8mb4'):
            raise RuntimeError('schema storage mismatch: '+table.name)


async def initialize_knowledge(db):
    root=Path(__file__).resolve().parents[1]/'sql'
    async with db.engine.begin() as conn:
        await conn.execute(text('SET NAMES utf8mb4'))
        existing=set(await conn.run_sync(lambda c:inspect(c).get_table_names()))
        for filename in ('ch03-schema.sql','ch03-state.sql'):
            for statement in (root/filename).read_text().split(';'):
                statement=statement.strip()
                if not statement or statement.upper().startswith('SET NAMES'):
                    continue
                match=re.match(r'CREATE TABLE (\w+)\s*\(',statement)
                if not match or match[1] not in metadata.tables:
                    raise RuntimeError('unexpected schema statement')
                if match[1] not in existing:
                    await conn.execute(text(statement))
        await conn.run_sync(validate_schema)

async def main():
    from dotenv import dotenv_values
    values=dotenv_values(Path(__file__).resolve().parents[1]/'.env')
    db=Database(values['DATABASE_URL'])
    try:
        await initialize_knowledge(db)
        print('ch03 schema validated; existing data preserved.')
    finally:
        await db.close()

if __name__=='__main__':
    asyncio.run(main())
