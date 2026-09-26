"""Initialize only absent tables; preserve existing data. DDL is the user contract."""
import asyncio
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
from sqlalchemy import inspect, text
from app.database import Database
from app.models import metadata

def validate_column_defaults(table,name,actual):
    default=str(actual.get('default') or '').lower().replace('()', '').replace("'", '').strip()
    expected=None
    if name=='created_at':expected='current_timestamp'
    if name=='updated_at':expected='current_timestamp on update current_timestamp'
    if name=='status':expected='进行中' if table=='conversations' else '待处理'
    if expected is not None and default!=expected:
        raise RuntimeError('schema default mismatch: '+table+'.'+name)
    if name=='id' and not actual.get('autoincrement'):
        raise RuntimeError('schema autoincrement mismatch: '+table)

async def initialize(db):
    source=Path(__file__).resolve().parents[1]/'sql/ch02-schema.sql'
    sql='\n'.join(line for line in source.read_text().splitlines() if not line.lstrip().startswith('--'))
    async with db.engine.begin() as connection:
        existing=await connection.run_sync(lambda c: inspect(c).get_table_names())
        for statement in sql.split(';'):
            if not statement.strip():continue
            name=statement.split('CREATE TABLE ')[1].split()[0]
            if name not in existing:await connection.execute(text(statement))
        def validate(c):
            inspector=inspect(c)
            for table in metadata.sorted_tables:
                columns={x['name']:x for x in inspector.get_columns(table.name)}
                if set(columns)!=set(table.c.keys()):raise RuntimeError('schema columns mismatch: '+table.name)
                for column in table.c:
                    actual=columns[column.name]
                    validate_column_defaults(table.name,column.name,actual)
                    expected_type=str(column.type.compile(dialect=c.dialect)).lower()
                    actual_type=str(actual['type'].compile(dialect=c.dialect)).lower()
                    if actual_type!=expected_type or actual['nullable']!=column.nullable:
                        raise RuntimeError('schema type mismatch: '+table.name+'.'+column.name)
                if set(inspector.get_pk_constraint(table.name)['constrained_columns']) != {x.name for x in table.primary_key}:
                    raise RuntimeError('schema primary key mismatch')
                actual_indexes={tuple(x['column_names']) for x in inspector.get_indexes(table.name)}
                if not {tuple(x.name for x in index.columns) for index in table.indexes}<=actual_indexes:raise RuntimeError('schema indexes mismatch')
                options=inspector.get_table_options(table.name)
                if options.get('mysql_engine','').lower()!='innodb' or not options.get('mysql_collate','').startswith('utf8mb4'):
                    raise RuntimeError('schema engine/charset mismatch')
                for fk in inspector.get_foreign_keys(table.name):
                    if any(v.upper() not in ('RESTRICT','NO ACTION') for k,v in fk.get('options',{}).items() if k in ('ondelete','onupdate')):
                        raise RuntimeError('schema foreign key action mismatch')
                actual_fks={(tuple(x['constrained_columns']),x['referred_table'],tuple(x['referred_columns'])) for x in inspector.get_foreign_keys(table.name)}
                expected_fks={((f.parent.name,),f.column.table.name,(f.column.name,)) for f in table.foreign_keys}
                if actual_fks!=expected_fks:raise RuntimeError('schema foreign keys mismatch')
        await connection.run_sync(validate)
        for statement in (source.parent/'ch02-seed.sql').read_text().split(';'):
            if statement.strip():await connection.execute(text(statement))

async def main():
    values=dotenv_values(Path(__file__).resolve().parents[1]/'.env')
    db=Database(values['DATABASE_URL'])
    try:
        await initialize(db)
        print('MySQL schema validated; demo data initialized (existing rows preserved).')
    finally:await db.close()
if __name__=='__main__':asyncio.run(main())
