from dataclasses import asdict
from sqlalchemy import select, insert, update, delete, func
from app.knowledge.models import knowledge_chunks as chunks
from app.knowledge.types import ChunkDraft, ChunkSnapshot, KnowledgeError
from app.knowledge.text import content_hash, embedding_text
from app.knowledge.state import source_state_key


def snapshot(row):
    draft=ChunkDraft(**{key:row[key] for key in ChunkDraft.__dataclass_fields__})
    return ChunkSnapshot(row['id'],draft,content_hash(draft))


class KnowledgeRepository:
    def __init__(self,sessions,*,state,fingerprint):
        self.sessions=sessions; self.state=state; self.fingerprint=fingerprint

    async def ensure_config(self,rebuild=False):
        await self.state.assert_owned()
        async with self.sessions.begin() as s:
            config=await self.state.get('config',s,True)
            if config and config.get('fingerprint')!=self.fingerprint and not rebuild:
                raise KnowledgeError('embedding_config_changed_reembed_required')
            if rebuild:
                records=await self.state.entries('source:')
                ids=[i for _,p in records for i in p.get('ids',[])]
                if ids:await s.execute(update(chunks).where(chunks.c.id.in_(ids)).values(vectorize_status='pending',vector_id=None))
            await self.state.put('config',{'fingerprint':self.fingerprint},s)

    async def config_matches(self,session=None):
        config=await self.state.get('config',session)
        return bool(config and config.get('fingerprint')==self.fingerprint)

    async def replace_source(self,source_key,drafts):
        await self.state.assert_owned()
        for d in drafts:
            if not d.category.strip() or not d.questions.strip() or not d.answer.strip():raise KnowledgeError('empty_knowledge')
            if len(d.category)>255 or len(d.section_path or '')>512 or len(d.content_type or '')>32 or len(embedding_text(d).encode())>6000:
                raise KnowledgeError('knowledge_too_large')
        key=source_state_key(source_key)
        async with self.sessions.begin() as s:
            if not await self.config_matches(s):raise KnowledgeError('embedding_config_changed')
            previous=await self.state.get(key,s,True) or {}
            previous_ids=previous.get('ids',[])
            rows={}
            if previous_ids:
                rows={r['id']:r for r in (await s.execute(select(chunks).where(chunks.c.id.in_(previous_ids)).with_for_update())).mappings()}
            ids=[]; result=[]
            for i,d in enumerate(drafts):
                old=rows.get(previous_ids[i]) if i<len(previous_ids) else None
                values=asdict(d)
                if old:
                    ident=old['id']
                    if snapshot(old).content_hash!=content_hash(d):values.update(vectorize_status='pending',vector_id=None)
                    await s.execute(update(chunks).where(chunks.c.id==ident).values(**values))
                else:
                    ident=(await s.execute(insert(chunks).values(**values))).inserted_primary_key[0]
                if not 0<ident<2**63:raise KnowledgeError('knowledge_id_out_of_range')
                ids.append(ident); result.append(ChunkSnapshot(ident,d,content_hash(d)))
            for i,ident in enumerate(ids):
                await s.execute(update(chunks).where(chunks.c.id==ident).values(prev_chunk_id=ids[i-1] if i else None,next_chunk_id=ids[i+1] if i+1<len(ids) else None))
            removed=[i for i in previous_ids if i not in ids]
            if removed:
                await s.execute(delete(chunks).where(chunks.c.id.in_(removed)))
            await self.state.put(key,{'source':source_key,'ids':ids,'hashes':[r.content_hash for r in result],
                                     'deleted_ids':list(dict.fromkeys(previous.get('deleted_ids',[])+removed))},s)
            return result

    async def pending(self,limit=16):
        records=await self.state.entries('source:')
        ids=[i for _,p in records for i in p.get('ids',[])]
        if not ids:return []
        async with self.sessions() as s:
            rows=(await s.execute(select(chunks).where(chunks.c.id.in_(ids),chunks.c.vectorize_status=='pending').order_by(chunks.c.id).limit(limit))).mappings().all()
            return [snapshot(r) for r in rows]

    async def mark_done(self,row,vector_id):
        await self.state.assert_owned()
        if vector_id!=str(row.id):return False
        async with self.sessions.begin() as s:
            if not await self.config_matches(s):return False
            actual=(await s.execute(select(chunks).where(chunks.c.id==row.id).with_for_update())).mappings().first()
            if not actual or snapshot(actual).content_hash!=row.content_hash:return False
            await s.execute(update(chunks).where(chunks.c.id==row.id).values(vectorize_status='done',vector_id=vector_id))
            return True

    async def load_done(self,ids):
        if not ids:return {}
        async with self.sessions() as s:
            if not await self.config_matches(s):raise KnowledgeError('embedding_config_changed')
            rows=(await s.execute(select(chunks).where(chunks.c.id.in_(ids),chunks.c.vectorize_status=='done'))).mappings().all()
            return {r['id']:snapshot(r) for r in rows if r['vector_id']==str(r['id'])}

    async def tombstones(self):
        return [i for _,p in await self.state.entries('source:') for i in p.get('deleted_ids',[])]

    async def clear_tombstones(self,ids):
        await self.state.assert_owned()
        removed=set(ids)
        for key,payload in await self.state.entries('source:'):
            if any(i in removed for i in payload.get('deleted_ids',[])):
                payload['deleted_ids']=[i for i in payload['deleted_ids'] if i not in removed]
                await self.state.put(key,payload)

    async def status(self):
        async with self.sessions() as s:
            rows=(await s.execute(select(chunks.c.vectorize_status,func.count()).group_by(chunks.c.vectorize_status))).all()
        return {'pending':0,'done':0,**dict(rows),'pending_deletes':len(await self.tombstones())}
