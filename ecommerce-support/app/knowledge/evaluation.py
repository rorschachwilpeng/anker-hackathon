"""Four-strategy retrieval comparison and separate generation faithfulness."""
import time
from collections import defaultdict

from app.knowledge.evidence import pack_evidence
from app.knowledge.reranker import RankedChunk

STRATEGIES=('dense','bm25','hybrid','hybrid_rerank')


def recall_at_k(gold,ranked,k):
    return len(gold.intersection(ranked[:k]))/len(gold) if gold else float(not ranked[:k])


def mrr(gold,ranked):
    return next((1/i for i,ident in enumerate(ranked,1) if ident in gold),0.)


def _summary(cases):
    if not cases:return {}
    return {**{f'recall@{k}':sum(c['metrics'][f'recall@{k}'] for c in cases)/len(cases) for k in (1,5,10)},
            'mrr':sum(c['metrics']['mrr'] for c in cases)/len(cases),
            'no_answer_false_positive_rate':sum(bool(c['ranked_ids']) for c in cases if not c['gold_ids']) /
                max(1,sum(not c['gold_ids'] for c in cases))}


async def evaluate_cases(cases,search,judge,generator,pool=None,judge_model=None):
    wanted={digest for case in cases for digest in case['gold_hashes']}
    gold_map={}
    repo=getattr(search,'repo',None)
    if repo is not None and hasattr(repo,'all_done_after'):
        cursor=0
        while True:
            page=await repo.all_done_after(cursor,limit=128)
            if not page:break
            for row in page:
                if row.content_hash in wanted:gold_map[row.content_hash]=row.id
            cursor=page[-1].id
        missing=wanted-gold_map.keys()
        if missing:raise ValueError(f'{len(missing)} gold chunks missing from active MySQL corpus')
    results={}
    for strategy in STRATEGIES:
        details=[]
        for case in cases:
            started=time.monotonic()
            item={'eval_id':case['id'],'bucket':case['bucket'],'query':case['query'],'gold_hashes':case['gold_hashes']}
            try:
                rows=(await search.find_evidence(case['query'],category=case.get('category'),strategy=strategy))[:50]
                gold_hashes=set(case['gold_hashes'])
                # IDs are resolved from authoritative content hashes at evaluation time.
                ranked=[row.id for row,_ in rows]
                gold_ids={gold_map.get(digest,f'hash:{digest}') for digest in gold_hashes}
                ranked_for_metrics=[gold_map.get(row.content_hash,f'hash:{row.content_hash}') for row,_ in rows]
                ranked_keys=[f'hash:{row.content_hash}' for row,_ in rows]
                item.update(gold_ids=sorted(gold_ids),ranked_ids=ranked,ranked_hashes=ranked_keys,
                    metrics={**{f'recall@{k}':recall_at_k(gold_ids,ranked_for_metrics,k) for k in (1,5,10)},
                             'mrr':mrr(gold_ids,ranked_for_metrics)})
                if strategy=='hybrid_rerank':
                    evidence=pack_evidence([RankedChunk(row,score) for row,score in rows],byte_budget=12000)
                    snapshot=[{'n':e.n,'chunk_id':e.chunk_id,'section_path':e.section_path,
                               'question':e.question,'answer':e.answer} for e in evidence]
                    item['evidence']=snapshot
                    answer=await generator.answer(case['query'],snapshot)
                    item['answer']=answer
                    verdict=await judge.score(case['query'],answer,snapshot)
                    item['faithfulness']=verdict
                    if not verdict['faithful'] and pool is not None:
                        await pool.record_faith_case({'eval_id':case['id'],'bucket':case['bucket'],
                            'query':case['query'],'strategy':strategy,'answer':answer,
                            'reason':verdict['reason'],'citations':snapshot,'judge_model':judge_model})
            except Exception as exc:
                item['error']=type(exc).__name__ + ': ' + str(exc)
            item['latency_ms']=round((time.monotonic()-started)*1000,2)
            details.append(item)
        valid=[c for c in details if 'metrics' in c]
        buckets=defaultdict(list)
        for c in valid:buckets[c['bucket']].append(c)
        results[strategy]={'overall':_summary(valid),'buckets':{key:_summary(value) for key,value in buckets.items()},
                           'cases':details,'errors':sum('error' in c for c in details),
                           'faithfulness':(sum(c.get('faithfulness',{}).get('faithful',False) for c in details if 'faithfulness' in c)/
                             max(1,sum('faithfulness' in c for c in details))) if strategy=='hybrid_rerank' else None}
    return {'strategies':results,'case_count':len(cases)}
