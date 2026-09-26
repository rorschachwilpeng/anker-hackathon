"""Offline comparison; --validate-dataset is local-only and does not call providers."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.knowledge.chunking import split_markdown
from app.knowledge.text import content_hash


def load_cases(path):
    cases=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if len({case['id'] for case in cases})!=len(cases):raise ValueError('duplicate eval id')
    return cases


def validate_dataset(cases):
    available={content_hash(row):row for path in (ROOT/'knowledge/demo').glob('*.md')
               for row in split_markdown(path.read_text(),path.stem,
                   {'products':'faq','manual':'manual','policies':'policy'}[path.stem])}
    for case in cases:
        if not {'id','bucket','query','gold_hashes','expected_refusal','evidence_points'} <= case.keys():raise ValueError('incomplete case')
        missing=set(case['gold_hashes'])-available.keys()
        if missing:raise ValueError(f"{case['id']}: missing gold content hash")
        if case['expected_refusal'] != (not case['gold_hashes']):raise ValueError(f"{case['id']}: invalid refusal label")
        expected=[{'section_path':available[d].section_path,'text':available[d].answer} for d in case['gold_hashes']]
        if case['evidence_points']!=expected:raise ValueError(f"{case['id']}: evidence changed")
    return len(cases)


async def run_live(cases,output,calibrate=False):
    import hashlib
    import httpx
    from pymilvus import AsyncMilvusClient
    from app.config import Settings
    from app.database import Database
    from app.service import CustomerSupportService
    from app.knowledge.state import StateStore
    from app.knowledge.repository import KnowledgeRepository
    from app.knowledge.embeddings import SiliconFlowEmbeddings
    from app.knowledge.hybrid_vectors import HybridVectors
    from app.knowledge.hybrid_retrieval import HybridKnowledgeSearch
    from app.knowledge.query import QueryUnderstanding
    from app.knowledge.reranker import SiliconFlowReranker
    from app.knowledge.faithfulness import FaithJudge
    from app.knowledge.evaluation import evaluate_cases,recommend_threshold
    from app.knowledge.quality_repository import QualityRepository
    from app.knowledge.migration import active_collection
    from langchain_core.messages import HumanMessage, SystemMessage

    settings=Settings()
    settings.ensure_knowledge_ready(search=True)
    if not calibrate:settings.ensure_hybrid_ready()
    if settings.database_url is None:raise RuntimeError('DATABASE_URL required')
    db=Database(settings.database_url.get_secret_value())
    async with httpx.AsyncClient() as http:
        milvus=AsyncMilvusClient(settings.milvus_uri)
        try:
            state=StateStore(db,namespace='knowledge')
            if not calibrate and await active_collection(state)!='knowledge_hybrid_v1':
                raise RuntimeError('activate verified hybrid collection first')
            fingerprint=hashlib.sha256(f'{settings.embedding_base_url.rstrip("/")}|{settings.embedding_model}|1024|text-v1'.encode()).hexdigest()
            repo=KnowledgeRepository(db.sessions,state=state,fingerprint=fingerprint)
            vectors=HybridVectors(milvus)
            await vectors.ensure_collection()
            model=CustomerSupportService(settings,http_async_client=http).model
            search=HybridKnowledgeSearch(repo,SiliconFlowEmbeddings(http,settings),vectors,
                QueryUnderstanding(model,settings.structured_output_method),
                SiliconFlowReranker(http,settings),None if calibrate else settings.rerank_score_threshold)
            if calibrate:
                samples=[]; observations=[]
                for case in cases:
                    rows=await search.find_evidence(case['query'],category=case.get('category'),strategy='hybrid_rerank')
                    top=rows[0] if rows else None
                    score=top[1] if top else 0.
                    relevant=bool(top and top[0].content_hash in case['gold_hashes'])
                    samples.append((score,relevant))
                    observations.append({'id':case['id'],'top_chunk_id':top[0].id if top else None,
                                         'score':score,'relevant':relevant})
                recommended=recommend_threshold(samples)
                result={'collection':vectors.collection,'model':settings.rerank_model,
                        'dataset':str(ROOT/'evals/ch04/calibration.jsonl'),
                        'recommended_threshold':recommended,'observations':observations}
                output.parent.mkdir(parents=True,exist_ok=True)
                output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
                print(json.dumps({'recommended_threshold':recommended,'cases':len(cases)},ensure_ascii=False))
                return
            class Generator:
                async def answer(self,query,evidence):
                    result=await model.ainvoke([SystemMessage(content='只根据提供证据回答，事实标注 [n]；不足则明确拒答，不承诺退款到账或配送时间。'),
                        HumanMessage(content=json.dumps({'question':query,'evidence':evidence},ensure_ascii=False))])
                    return result.content if isinstance(result.content,str) else str(result.content)
            report=await evaluate_cases(cases,search,FaithJudge(model,settings.structured_output_method),
                Generator(),QualityRepository(db.sessions),settings.openai_model)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps({'case_count':report['case_count'],
                'strategies':{name:{'overall':data['overall'],'errors':data['errors']} for name,data in report['strategies'].items()}},ensure_ascii=False))
        finally:
            await milvus.close()
            await db.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset',type=Path,default=ROOT/'evals/ch04/holdout.jsonl')
    parser.add_argument('--output',type=Path,default=ROOT.parent/'dev-notes/evals/ch04/report.json')
    parser.add_argument('--validate-dataset',action='store_true',help='No database or external model calls')
    parser.add_argument('--calibrate-threshold',action='store_true',help='Use prepared hybrid collection; write recommendation, then set .env before activation')
    args=parser.parse_args()
    cases=load_cases(args.dataset)
    count=validate_dataset(cases)
    if args.validate_dataset:print(json.dumps({'validated_cases':count}));return
    asyncio.run(run_live(cases,args.output,args.calibrate_threshold))


if __name__=='__main__':main()
