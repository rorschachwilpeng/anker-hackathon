"""Create the two user-specified ch04 tables only when absent; validate existing schema."""
import asyncio
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import inspect, text
from app.database import Database
from app.knowledge.quality_models import metadata


def _normal(value):
    return re.sub(r'\s+', ' ', str('' if value is None else value).lower().replace('()', '').replace("'", '')).strip()


def sql_statements(script):
    script = re.sub(r'^\s*--[^\n]*', '', script, flags=re.M)
    statements, chars = [], []
    quoted = False
    index = 0
    while index < len(script):
        char = script[index]
        if char == "'":
            if quoted and index+1 < len(script) and script[index+1] == "'":
                chars.extend((char, char))
                index += 2
                continue
            quoted = not quoted
        if char == ';' and not quoted:
            value = ''.join(chars).strip()
            if value:
                statements.append(value)
            chars = []
        else:
            chars.append(char)
        index += 1
    value = ''.join(chars).strip()
    if value:
        statements.append(value)
    if quoted:
        raise RuntimeError('unterminated SQL quote')
    return statements


def validate_ch04_schema(conn):
    inspector = inspect(conn)
    for table in metadata.tables.values():
        columns = {c['name']: c for c in inspector.get_columns(table.name)}
        if set(columns) != set(table.c.keys()):
            raise RuntimeError('schema columns mismatch: '+table.name)
        for field in table.c:
            actual = columns[field.name]
            expected_type = _normal(field.type.compile(dialect=conn.dialect)).replace('integer', 'int')
            actual_type = _normal(actual['type'].compile(dialect=conn.dialect)).replace('integer', 'int')
            if (expected_type != actual_type or actual['nullable'] != field.nullable
                    or actual.get('comment') != field.comment):
                raise RuntimeError('schema column mismatch: '+table.name+'.'+field.name)
            default = _normal(field.server_default.arg if field.server_default is not None else None)
            if _normal(actual.get('default')) != default:
                raise RuntimeError('schema default mismatch: '+table.name+'.'+field.name)
        if set(inspector.get_pk_constraint(table.name)['constrained_columns']) != {f.name for f in table.primary_key}:
            raise RuntimeError('schema primary key mismatch: '+table.name)
        expected = {i.name: (tuple(f.name for f in i.columns), i.unique) for i in table.indexes}
        actual = {i['name']: (tuple(i['column_names']), i['unique']) for i in inspector.get_indexes(table.name)}
        # InnoDB creates a supporting index for the FK when the DDL did not name one.
        if table.name == 'low_confidence_questions':
            expected['fk_lcq_conversation'] = (('conversation_id',), False)
        if expected != actual:
            raise RuntimeError(f'schema indexes mismatch: {table.name}: {expected!r} != {actual!r}')
        exp_fk = {(f.constraint.name, f.parent.name, *f.target_fullname.split('.')) for f in table.foreign_keys}
        got_fk = {(f['name'],f['constrained_columns'][0],f['referred_table'],f['referred_columns'][0]) for f in inspector.get_foreign_keys(table.name)}
        if exp_fk != got_fk:
            raise RuntimeError('schema foreign keys mismatch: '+table.name)
        options = inspector.get_table_options(table.name)
        if (options.get('mysql_engine','').lower() != 'innodb'
                or not options.get('mysql_collate','').startswith('utf8mb4')
                or options.get('mysql_comment') != table.comment):
            raise RuntimeError('schema storage mismatch: '+table.name)


async def initialize_ch04(db):
    statements = sql_statements((Path(__file__).resolve().parents[1]/'sql/ch04-schema.sql').read_text())
    async with db.engine.begin() as conn:
        await conn.execute(text('SET NAMES utf8mb4'))
        existing = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
        for statement in statements:
            if not statement or statement.upper() == 'SET NAMES UTF8MB4':
                continue
            match = re.match(r'CREATE TABLE (\w+)\s*\(', statement)
            if not match or match[1] not in metadata.tables:
                raise RuntimeError('unexpected schema statement')
            if match[1] not in existing:
                await conn.execute(text(statement))
        await conn.run_sync(validate_ch04_schema)


async def main():
    from dotenv import dotenv_values
    db = Database(dotenv_values(Path(__file__).resolve().parents[1]/'.env')['DATABASE_URL'])
    try:
        await initialize_ch04(db)
        print('ch04 schema validated; existing data preserved.')
    finally:
        await db.close()


if __name__ == '__main__':
    asyncio.run(main())
