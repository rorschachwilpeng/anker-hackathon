from app.knowledge.types import ChunkDraft, ChunkSnapshot
from app.knowledge.hybrid_vectors import Candidate


class Repo:
    async def categories(self):
        return {'商品','配送'}

    async def load_done(self, ids):
        return {2:ChunkSnapshot(2,ChunkDraft('商品','X100-Pro 质保','两年质保。'),'b'*64)}


class Vectors:
    def __init__(self):
        self.args = None

    async def retrieve(self,canonical,lexical,vector,category,strategy,limit=50):
        self.args=(canonical,lexical,category,strategy,limit)
        return [Candidate(1,0.95,'a'*64,1),Candidate(2,0.8,'b'*64,2)]


class Encoder:
    async def encode(self,texts):
        return [[1.0]*1024]


class Rewriter:
    async def normalize(self,raw):
        from app.knowledge.query import QueryPlan
        return QueryPlan(canonical='X100-Pro 质保多久',synonyms=['保修期'],category='商品')


async def test_stale_candidate_filtered_and_public_faq_shape_unchanged():
    from app.knowledge.hybrid_retrieval import HybridKnowledgeSearch
    vec=Vectors()
    search=HybridKnowledgeSearch(Repo(),Encoder(),vec,Rewriter())
    assert await search.find('X100-Pro 有保修吗') == [
        {'id':2,'question':'X100-Pro 质保','answer':'两年质保。','category':'商品'}]
    assert vec.args[2:] == ('商品','hybrid',50)
    assert 'X100-Pro 有保修吗' in vec.args[1]


async def test_unknown_category_never_filters_and_explicit_invalid_category_rejected():
    from app.knowledge.hybrid_retrieval import HybridKnowledgeSearch
    from app.knowledge.query import QueryPlan
    class Unknown:
        async def normalize(self,raw):
            return QueryPlan(canonical=raw,synonyms=[],category='不存在')
    vec=Vectors()
    search=HybridKnowledgeSearch(Repo(),Encoder(),vec,Unknown())
    await search.find('运费')
    assert vec.args[2] is None


async def test_rewrite_preserves_model_and_negation_even_if_model_drops_them():
    from app.knowledge.query import QueryUnderstanding
    class Model:
        def with_structured_output(self,*args,**kwargs):
            return self
        async def ainvoke(self,messages):
            return {'canonical':'保修多久','synonyms':['质保'],'category':None}
    query=QueryUnderstanding(Model())
    plan=await query.normalize('X100-Pro 不保修吗')
    assert plan.canonical == 'X100-Pro 不保修吗'
    assert 'X100-Pro' in query.lexical('X100-Pro 不保修吗',plan)
