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
from pymilvus import AsyncMilvusClient
from app.config import Settings
from app.database import Database
from app.knowledge.embeddings import SiliconFlowEmbeddings
from app.knowledge.vectors import MilvusVectors
from app.knowledge.state import StateStore
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.ingestion import IngestionService, InjectedFailure
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
            return await service.resume()

def parser():
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['import-dir','import-faq','resume','reembed-all','status'])
    p.add_argument('path',nargs='?')
    p.add_argument('--failpoint',choices=['after-mysql','after-milvus'])
    return p

if __name__=='__main__':
    p=parser();args=p.parse_args()
    if args.command=='import-dir' and not args.path:p.error('import-dir requires PATH')
    try:print(json.dumps(asyncio.run(execute(args)),ensure_ascii=False))
    except (KnowledgeError,InjectedFailure) as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=False));sys.exit(1)
