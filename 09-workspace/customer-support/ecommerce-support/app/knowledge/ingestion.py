import asyncio
from pathlib import Path
from sqlalchemy import select
from app.models import faq
from app.knowledge.chunking import split_markdown
from app.knowledge.text import embedding_text
from app.knowledge.types import ChunkDraft, KnowledgeError

class InjectedFailure(RuntimeError):
    pass

class IngestionService:
    def __init__(self,repo,embeddings,vectors,state,*,failpoint=None):
        self.repo=repo;self.embeddings=embeddings;self.vectors=vectors;self.state=state
        self.failpoint=failpoint

    def checkpoint(self,point):
        if self.failpoint==point:
            self.failpoint=None
            raise InjectedFailure(point)

    async def retry(self,operation):
        for attempt in range(3):
            try:return await operation()
            except KnowledgeError as exc:
                if not exc.retryable or attempt==2:raise
                await asyncio.sleep(2**attempt)

    async def resume(self):
        await self.state.assert_owned()
        deleted=await self.repo.tombstones()
        if deleted:
            await self.vectors.delete(deleted)
            await self.state.assert_owned()
            await self.repo.clear_tombstones(deleted)
        self.checkpoint('after-mysql')
        while rows:=await self.repo.pending():
            vectors=await self.retry(lambda:self.embeddings.encode([embedding_text(r.draft) for r in rows]))
            await self.state.assert_owned()
            ids=await self.vectors.upsert(list(zip(rows,vectors,strict=True)))
            if ids!=[r.id for r in rows]:raise KnowledgeError('upsert_ids_mismatch')
            self.checkpoint('after-milvus')
            await self.state.assert_owned()
            for row in rows:
                if not await self.repo.mark_done(row,str(row.id)):raise KnowledgeError('source_changed')
        return await self.repo.status()

    async def import_document(self,path):
        path=Path(path).resolve()
        drafts=split_markdown(path.read_text(),path.stem,'manual' if 'manual' in path.stem else 'policy')
        await self.repo.replace_source('doc:'+str(path),drafts)

    async def import_directory(self,path):
        root=Path(path).resolve()
        if not root.is_dir():raise KnowledgeError('invalid_document_directory')
        parsed=[]
        for file in sorted(root.rglob('*.md')):
            if file.is_symlink():raise KnowledgeError('document_symlink_not_supported')
            parsed.append((file,split_markdown(file.read_text(),file.stem,'manual' if 'manual' in file.stem else 'policy')))
        # Parse everything first; a malformed file must not trigger source deletion.
        current={'doc:'+str(file.resolve()) for file,_ in parsed}
        for file,drafts in parsed:await self.repo.replace_source('doc:'+str(file.resolve()),drafts)
        for _,payload in await self.state.entries('source:'):
            source=payload.get('source','')
            if source.startswith('doc:') and Path(source[4:]).is_relative_to(root) and source not in current:
                await self.repo.replace_source(source,[])
        return await self.resume()

    async def import_faq(self):
        async with self.repo.sessions() as s:
            rows=(await s.execute(select(faq).order_by(faq.c.id))).mappings().all()
        current=set()
        for row in rows:
            key='faq:'+str(row['id']);current.add(key)
            await self.repo.replace_source(key,[ChunkDraft(row['category'],row['question'],row['answer'],content_type='faq')])
        for _,payload in await self.state.entries('source:'):
            key=payload.get('source','')
            if key.startswith('faq:') and key not in current:await self.repo.replace_source(key,[])
        return await self.resume()
