"""Rebuild a versioned hybrid collection without modifying the live dense collection."""
from dataclasses import dataclass

from app.knowledge.embeddings import valid_vector
from app.knowledge.text import embedding_text
from app.knowledge.types import KnowledgeError


class MigrationInterrupted(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationReport:
    passed: bool
    count: int


async def active_collection(state):
    value = await state.get('active_collection')
    return value['name'] if value else 'knowledge'


async def _all_rows(repo):
    last = 0
    while batch := await repo.all_done_after(last,32):
        for row in batch:
            yield row
        last = batch[-1].id


async def _verify(repo, new_vectors):
    count = 0
    async for row in _all_rows(repo):
        if not await new_vectors.verify([(row.id,row.content_hash)]):
            return MigrationReport(False,count)
        count += 1
    return MigrationReport(True,count)


async def migrate_hybrid(repo, old_vectors, new_vectors, state, embeddings, failpoint=None):
    await state.assert_owned()
    # Always re-check from the beginning: a source can be changed or deleted below a saved cursor.
    count = 0
    last_id = 0
    async for row in _all_rows(repo):
        old = await old_vectors.client.query(old_vectors.collection, ids=[row.id],
            output_fields=['id','vector','content_hash'], timeout=15)
        vector = old[0]['vector'] if len(old)==1 and old[0]['content_hash']==row.content_hash else None
        if not valid_vector(vector):
            vector = (await embeddings.encode([embedding_text(row.draft)]))[0]
        await state.assert_owned()
        await new_vectors.upsert([(row,vector)])
        if failpoint == 'after-upsert':
            raise MigrationInterrupted('after-upsert')
        last_id = row.id
        count += 1
        await state.put('migration:ch04',{'target':new_vectors.collection,'last_id':last_id})
    deleted = await repo.tombstones()
    if deleted:
        await new_vectors.delete(deleted)
    report = await _verify(repo,new_vectors)
    return MigrationReport(report.passed and report.count == count,report.count)


async def activate_hybrid(repo, new_vectors, state):
    await state.assert_owned()
    report = await _verify(repo,new_vectors)
    if not report.passed:
        raise KnowledgeError('hybrid_migration_verification_failed')
    await state.put('active_collection',{'name':new_vectors.collection})
    return report
