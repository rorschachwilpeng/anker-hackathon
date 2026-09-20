"""Knowledge commands; credentials remain in the local environment."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from langchain_openai import ChatOpenAI
from pymilvus import AsyncMilvusClient
from app.config import Settings
from app.database import Database
from app.knowledge.embeddings import SiliconFlowEmbeddings
from app.knowledge.vectors import MilvusVectors
from app.knowledge.state import StateStore
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.ingestion import IngestionService, InjectedFailure
from app.knowledge.extraction import QAExtractor
from app.knowledge.dedup import Deduplicator, DedupService
from app.knowledge.mining import MiningService
from app.knowledge.worker import run_worker
from app.knowledge.types import KnowledgeError


def embedding_fingerprint(settings):
    raw=f'{settings.embedding_base_url.rstrip("/")}|{settings.embedding_model}|1024|text-v1'
    return hashlib.sha256(raw.encode()).hexdigest()

@asynccontextmanager
async def runtime(settings=None,collection='knowledge',namespace='knowledge',failpoint=None):
    settings=settings or Settings()
    settings.ensure_knowledge_ready(search=False)
    db=Database(settings.database_url.get_secret_value())
    client=AsyncMilvusClient(settings.milvus_uri)
    async with httpx.AsyncClient() as http:
        state=StateStore(db,namespace)
        repo=KnowledgeRepository(db.sessions,state=state,fingerprint=embedding_fingerprint(settings))
        vectors=MilvusVectors(client,collection)
        service=IngestionService(repo,SiliconFlowEmbeddings(http,settings),vectors,state,failpoint=failpoint)
        try:yield service
        finally:
            await client.close()
            await db.close()

async def execute(args):
    async with runtime(failpoint=args.failpoint) as service:
        if args.command=='status':return await service.repo.status()
        async with service.state.writer_lock():
            await service.repo.ensure_config(rebuild=args.command=='reembed-all')
            await service.vectors.ensure_collection()
            if args.command=='import-dir':return await service.import_directory(args.path)
            if args.command=='import-faq':return await service.import_faq()
            if args.command=='mine':
                model=ChatOpenAI(base_url=service.embeddings.settings.openai_base_url,
                    model=service.embeddings.settings.openai_model,
                    api_key=service.embeddings.settings.openai_api_key.get_secret_value(),
                    max_tokens=service.embeddings.settings.knowledge_extraction_max_tokens,
                    timeout=service.embeddings.settings.request_timeout,max_retries=0)
                dedup=DedupService(service.repo,Deduplicator(model,service.embeddings))
                mining=MiningService(service.repo,QAExtractor(model),dedup_service=dedup,ingestion=service)
                return await mining.run(args.run_id)
            return await service.resume()

async def execute_worker(args):
    if not args.enabled:return {'status':'disabled'}
    settings=Settings()
    async def run_once(run_id):
        mine_args=argparse.Namespace(**vars(args))
        mine_args.command='mine';mine_args.run_id=run_id
        try:return await execute(mine_args)
        except KnowledgeError as exc:
            if exc.code=='writer_busy':return {'status':'skipped','reason':'writer_busy'}
            raise
    async with runtime() as service:
        return await run_worker(run_once,service.state,enabled=True,zone=settings.knowledge_worker_timezone)

def parser():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['import-dir','import-faq','resume','reembed-all','status','mine','worker'])
    p.add_argument('path',nargs='?')
    p.add_argument('--failpoint',choices=['after-mysql','after-milvus'])
    p.add_argument('--run-id',default='manual-mining')
    p.add_argument('--enabled',action='store_true')
    return p

if __name__=='__main__':
    p=parser();args=p.parse_args()
    if args.command=='import-dir' and not args.path:p.error('import-dir requires PATH')
    try:print(json.dumps(asyncio.run(execute_worker(args) if args.command=='worker' else execute(args)),ensure_ascii=False))
    except (KnowledgeError,InjectedFailure) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));sys.exit(1)
