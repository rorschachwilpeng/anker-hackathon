import pytest

from app.knowledge.types import ChunkDraft, ChunkSnapshot, KnowledgeError


class FakeRepo:
    def __init__(self):
        self.row = ChunkSnapshot(19,ChunkDraft('商品','X100-Pro','两年质保。'),'a'*64)

    async def all_done_after(self, last_id, limit):
        return [self.row] if last_id < self.row.id else []

    async def pending(self):
        return []

    async def tombstones(self):
        return []


class FakeOld:
    def __init__(self, missing=False):
        self.client = self
        self.collection = 'knowledge'
        self.missing = missing

    async def query(self, collection_name, ids, output_fields, **kwargs):
        assert collection_name == self.collection and ids == [19]
        assert output_fields == ['id','vector','content_hash']
        return [] if self.missing else [{'id':19,'vector':[1.0]*1024,'content_hash':'a'*64}]


class FakeNew:
    def __init__(self):
        self.rows = {}
        self.collection = 'knowledge_hybrid_v1'

    async def upsert(self, rows):
        for row,vector in rows:
            self.rows[row.id] = (row,vector)
        return [row.id for row,_ in rows]

    async def delete(self, ids):
        for ident in ids:
            self.rows.pop(ident,None)

    async def verify(self, rows):
        return all(i in self.rows and self.rows[i][0].content_hash == digest for i,digest in rows)


class FakeState:
    def __init__(self):
        self.values = {}

    async def assert_owned(self):
        pass

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, payload):
        self.values[key] = payload


class Encoder:
    async def encode(self, texts):
        return [[1.0]*1024 for _ in texts]


async def test_migration_after_upsert_crash_retries_same_id_without_switch():
    from app.knowledge.migration import migrate_hybrid, active_collection, MigrationInterrupted
    repo, old, new, state = FakeRepo(), FakeOld(), FakeNew(), FakeState()
    with pytest.raises(MigrationInterrupted):
        await migrate_hybrid(repo,old,new,state,Encoder(),failpoint='after-upsert')
    assert await active_collection(state) == 'knowledge'
    assert list(new.rows) == [19]
    result = await migrate_hybrid(repo,old,new,state,Encoder())
    assert result.passed and result.count == 1 and list(new.rows) == [19]
    assert await active_collection(state) == 'knowledge'


async def test_missing_old_vector_is_reembedded_and_invalid_hash_blocks_activation():
    from app.knowledge.migration import migrate_hybrid, activate_hybrid
    repo, old, new, state = FakeRepo(), FakeOld(missing=True), FakeNew(), FakeState()
    result = await migrate_hybrid(repo,old,new,state,Encoder())
    assert result.passed and new.rows[19][1] == [1.0]*1024
    await activate_hybrid(repo,new,state)
    assert state.values['active_collection']['name'] == 'knowledge_hybrid_v1'
    new.rows[19] = (ChunkSnapshot(19,repo.row.draft,'b'*64),[1.0]*1024)
    with pytest.raises(KnowledgeError, match='verification'):
        await activate_hybrid(repo,new,state)
