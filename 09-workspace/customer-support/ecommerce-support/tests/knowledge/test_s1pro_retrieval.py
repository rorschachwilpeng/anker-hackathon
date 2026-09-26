from app.knowledge.types import ChunkDraft, ChunkSnapshot, Hit, KnowledgeError


async def test_model_search_rejects_other_models_old_rows_missing_urls_and_stale_hashes():
    from app.knowledge.retrieval import KnowledgeSearch
    class Encoder:
        async def encode(self, texts):return [[1.0]*1024]
    class Vectors:
        async def search(self, vector, top_k):
            assert top_k == 20
            return [Hit(i,.9,str(i)) for i in range(1,6)]
    class Repo:
        async def load_done(self, ids):
            return {
                1:ChunkSnapshot(1,ChunkDraft('a','q','clear dust',product_model='T2080',source_title='official',source_url='https://service.eufy.com/a'),'1'),
                2:ChunkSnapshot(2,ChunkDraft('a','q','wrong model',product_model='T9999',source_title='official',source_url='https://service.eufy.com/b'),'2'),
                3:ChunkSnapshot(3,ChunkDraft('a','q','legacy'),'3'),
                4:ChunkSnapshot(4,ChunkDraft('a','q','no URL',product_model='T2080',source_title='official'),'4'),
                5:ChunkSnapshot(5,ChunkDraft('a','q','stale',product_model='T2080',source_title='official',source_url='https://service.eufy.com/c'),'old'),
            }
    rows=await KnowledgeSearch(Repo(),Encoder(),Vectors(),.5).find_for_model('吸力变弱','T2080')
    assert rows==[{'id':1,'answer':'clear dust','product_model':'T2080','source_title':'official','source_url':'https://service.eufy.com/a'}]


async def test_model_search_empty_and_infrastructure_failure():
    from app.knowledge.retrieval import KnowledgeSearch
    class Encoder:
        async def encode(self, texts):return [[1.0]*1024]
    class EmptyVectors:
        async def search(self, vector, top_k):return []
    class FailingVectors:
        async def search(self, vector, top_k):raise KnowledgeError('vector_down',retryable=True)
    class Repo:
        async def load_done(self, ids):raise AssertionError('no lookup')
    assert await KnowledgeSearch(Repo(),Encoder(),EmptyVectors(),.5).find_for_model('x','T2080')==[]
    import pytest
    with pytest.raises(KnowledgeError,match='vector_down'):
        await KnowledgeSearch(Repo(),Encoder(),FailingVectors(),.5).find_for_model('x','T2080')
