from app.knowledge.types import ChunkDraft, ChunkSnapshot
from pathlib import Path


def test_labeled_dataset_has_buckets_and_disjoint_queries():
    from scripts.evaluate_ch04 import load_cases,validate_dataset
    root=Path(__file__).resolve().parents[2]
    holdout=load_cases(root/'evals/ch04/holdout.jsonl')
    calibration=load_cases(root/'evals/ch04/calibration.jsonl')
    assert validate_dataset(holdout)==18 and validate_dataset(calibration)==6
    assert {case['query'] for case in holdout}.isdisjoint(case['query'] for case in calibration)
    assert all(sum(case['bucket']==bucket for case in holdout)>=3 for bucket in
               ('A_policy','B_model','C_colloquial','D_no_answer','E_multi','F_category'))


def test_metrics_two_gold_and_no_answer():
    from app.knowledge.evaluation import recall_at_k, mrr
    assert recall_at_k({2,3},[1,2,4,3],3)==.5
    assert mrr({2,3},[1,2,4,3])==.5
    assert recall_at_k(set(),[],10)==1
    assert recall_at_k(set(),[1],10)==0
    assert mrr(set(),[1])==0


def test_calibration_picks_threshold_separating_answerable_and_unknown():
    from app.knowledge.evaluation import recommend_threshold
    samples=[(.87,True),(.75,True),(.31,False),(.42,False)]
    threshold=recommend_threshold(samples)
    assert .42 < threshold <= .75


async def test_four_strategies_and_all_evidence_snapshot():
    from app.knowledge.evaluation import evaluate_cases
    from app.knowledge.text import content_hash
    rows=[ChunkSnapshot(i,ChunkDraft('政策',f'问{i}',f'答{i}',f'政策/节{i}'),content_hash(ChunkDraft('政策',f'问{i}',f'答{i}',f'政策/节{i}'))) for i in (1,2)]
    class Search:
        def __init__(self):self.calls=[]
        async def find_evidence(self,query,category=None,strategy='hybrid'):
            self.calls.append((strategy,category))
            return [(r,1.) for r in rows]
    class Judge:
        async def score(self,query,answer,evidence):return {'faithful':False,'reason':'无依据','claims':['编造']}
    class Generator:
        async def answer(self,query,evidence):return '编造[1]'
    class Pool:
        def __init__(self):self.cases=[]
        async def record_faith_case(self,case):self.cases.append(case)
    search,pool=Search(),Pool()
    case={'id':'A01','bucket':'A_policy','query':'问题','gold_hashes':[r.content_hash for r in rows]}
    report=await evaluate_cases([case],search,Judge(),Generator(),pool)
    assert [call[0] for call in search.calls]==['dense','bm25','hybrid','hybrid_rerank']
    assert report['strategies']['hybrid']['overall']['recall@1']==.5
    assert report['strategies']['hybrid']['overall']['recall@5']==1.
    assert len(pool.cases)==1 and len(pool.cases[0]['citations'])==2
    assert report['strategies']['hybrid_rerank']['cases'][0]['faithfulness']['faithful'] is False
    assert report['strategies']['hybrid_rerank']['overall']['citation_valid_rate']==1
    assert report['metadata']['judge_model'] is None
