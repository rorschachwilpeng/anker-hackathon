"""Prepare and verify the versioned hybrid index; activation is a separate command."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from pymilvus import AsyncMilvusClient
from app.config import Settings
from app.database import Database
from app.knowledge.embeddings import SiliconFlowEmbeddings
from app.knowledge.ingestion import IngestionService
from app.knowledge.migration import active_collection, activate_hybrid, migrate_hybrid, _verify
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.state import StateStore
from app.knowledge.vectors import MilvusVectors
from app.knowledge.hybrid_vectors import HybridVectors


async def run(command, failpoint=None):
    settings = Settings()
    settings.ensure_knowledge_ready(search=False)
    db = Database(settings.database_url.get_secret_value())
    client = AsyncMilvusClient(settings.milvus_uri)
    try:
        state = StateStore(db, 'knowledge')
        active = await active_collection(state)
        if command == 'status':
            return {'active_collection':active,'migration':await state.get('migration:ch04')}
        fingerprint = hashlib.sha256(
            f'{settings.embedding_base_url.rstrip("/")}|{settings.embedding_model}|1024|text-v1'.encode()).hexdigest()
        repo = KnowledgeRepository(db.sessions,state=state,fingerprint=fingerprint)
        old = MilvusVectors(client,'knowledge')
        new = HybridVectors(client,'knowledge_hybrid_v1')
        async with state.writer_lock():
            active = await active_collection(state)
            if command == 'prepare':
                if active != 'knowledge':
                    raise RuntimeError('hybrid collection already active')
                await old.ensure_collection()
                await new.ensure_collection()
                async with httpx.AsyncClient() as http:
                    embeddings = SiliconFlowEmbeddings(http,settings)
                    await IngestionService(repo,embeddings,old,state).resume()
                    report = await migrate_hybrid(repo,old,new,state,embeddings,failpoint)
            else:
                if command == 'activate':
                    settings.ensure_hybrid_ready()
                if not await client.has_collection(new.collection,timeout=10):
                    raise RuntimeError('prepare hybrid index first')
                await new.ensure_collection()
                report = await (_verify(repo,new) if command == 'verify' else activate_hybrid(repo,new,state))
        return {'passed':report.passed,'count':report.count,'active_collection':await active_collection(state)}
    finally:
        await client.close()
        await db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['status','prepare','verify','activate'])
    parser.add_argument('--failpoint', choices=['after-upsert'])
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args.command,args.failpoint)),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__,'code':str(exc)},ensure_ascii=False))
        sys.exit(1)
