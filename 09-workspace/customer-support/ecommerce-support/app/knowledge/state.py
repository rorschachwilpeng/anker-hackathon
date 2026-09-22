from contextlib import asynccontextmanager
import hashlib
from sqlalchemy import select, text
from sqlalchemy.dialects.mysql import insert
from app.knowledge.models import knowledge_ingestion_state as states
from app.knowledge.types import KnowledgeError


class StateStore:
    def __init__(self, db, namespace='knowledge'):
        self.db=db; self.namespace=namespace; self.connection=None

    def key(self,key):
        value=self.namespace+':'+key
        if len(value)>191:raise KnowledgeError('state_key_too_long')
        return value

    async def get(self,key,session=None,lock=False):
        if session is None:
            async with self.db.sessions() as s:return await self.get(key,s,lock)
        query=select(states.c.payload).where(states.c.state_key==self.key(key))
        if lock:query=query.with_for_update()
        return (await session.execute(query)).scalar_one_or_none()

    async def put(self,key,payload,session=None):
        if session is None:
            async with self.db.sessions.begin() as s:return await self.put(key,payload,s)
        value={'schema_version':1,**payload}
        await session.execute(insert(states).values(state_key=self.key(key),payload=value).on_duplicate_key_update(payload=value))

    async def entries(self,prefix):
        async with self.db.sessions() as s:
            # Escape LIKE metacharacters; test namespaces contain underscores.
            value=self.key(prefix).replace('!','!!').replace('%','!%').replace('_','!_')
            rows=(await s.execute(select(states).where(states.c.state_key.like(value+'%',escape='!')).order_by(states.c.state_key))).mappings().all()
            return [(r['state_key'][len(self.namespace)+1:],r['payload']) for r in rows]

    @asynccontextmanager
    async def writer_lock(self):
        if self.connection is not None:raise KnowledgeError('writer_lock_reentry')
        conn=await self.db.engine.connect()
        acquired=False
        try:
            acquired=(await conn.execute(text("SELECT GET_LOCK('ch03_knowledge_writer',0)"))).scalar()==1
            if not acquired:raise KnowledgeError('writer_busy')
            self.connection=conn
            yield self
        finally:
            self.connection=None
            if acquired and not conn.closed and not conn.invalidated:
                await conn.execute(text("SELECT RELEASE_LOCK('ch03_knowledge_writer')"))
            await conn.close()

    async def assert_owned(self):
        conn=self.connection
        if conn is None or conn.closed or conn.invalidated:raise KnowledgeError('writer_lock_lost')
        try:
            owner=(await conn.execute(text("SELECT IS_USED_LOCK('ch03_knowledge_writer') = CONNECTION_ID()"))).scalar()
        except Exception:
            raise KnowledgeError('writer_lock_lost') from None
        if owner!=1:raise KnowledgeError('writer_lock_lost')


def source_state_key(source):
    return 'source:'+hashlib.sha256(source.encode()).hexdigest()
