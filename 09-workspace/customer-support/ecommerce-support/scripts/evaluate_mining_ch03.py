import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_openai import ChatOpenAI
from sqlalchemy import delete, insert, select, update

from app.knowledge.dedup import Deduplicator, DedupService
from app.knowledge.extraction import QAExtractor
from app.knowledge.mining import MiningService
from app.knowledge.models import knowledge_chunks, knowledge_ingestion_state, qa_extraction_staging
from app.models import conversations, messages
from scripts.knowledge import mining_byte_budget, runtime


async def main():
    suffix = uuid.uuid4().hex
    namespace = "test_ch03_mining_" + suffix
    collection = "test_ch03_mining_" + suffix
    user_id = "test-ch03-" + suffix
    conversation_id = None
    staging_ids = []
    chunk_ids = []
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "synthetic_only": True}
    async with runtime(collection=collection, namespace=namespace) as service:
        try:
            async with service.repo.sessions.begin() as session:
                conversation_id = (await session.execute(insert(conversations).values(
                    user_id=user_id, status="进行中"
                ))).inserted_primary_key[0]
                await session.execute(insert(messages), [
                    {"conversation_id": conversation_id, "role": "user", "content": "虚构演示的礼品包装怎么收费？"},
                    {"conversation_id": conversation_id, "role": "assistant", "content": "虚构演示礼品包装每单收取3元。"},
                ])
                await session.execute(update(conversations).where(conversations.c.id == conversation_id).values(status="已结束"))

            settings = service.embeddings.settings
            model = ChatOpenAI(
                base_url=settings.openai_base_url, model=settings.openai_model,
                api_key=settings.openai_api_key.get_secret_value(),
                max_tokens=settings.knowledge_extraction_max_tokens,
                timeout=settings.request_timeout, max_retries=0,
            )
            dedup = DedupService(service.repo, Deduplicator(model, service.embeddings))
            mining = MiningService(service.repo, QAExtractor(model), dedup_service=dedup, ingestion=service,
                                   byte_budget=mining_byte_budget(settings))
            async with service.state.writer_lock():
                await service.repo.ensure_config()
                await service.vectors.ensure_collection()
                first = await mining.run("synthetic-" + suffix)
                second = await mining.run("synthetic-" + suffix)
                entries = await service.state.entries("source:")
                chunk_ids = [ident for _, payload in entries for ident in payload.get("ids", [])]
            async with service.repo.sessions() as session:
                staging_ids = list((await session.execute(select(qa_extraction_staging.c.id).where(
                    qa_extraction_staging.c.source_ref == str(conversation_id)
                ))).scalars())
            report.update(
                conversation_id=conversation_id,
                first=first,
                second=second,
                staging_ids=staging_ids,
                chunk_ids=chunk_ids,
            )
            report["passed"] = (
                first["extraction"]["staged"] == 1
                and first["dedup"]["new"] == 1
                and first["vectorization"]["pending"] == 0
                and second["extraction"]["staged"] == 0
                and second["dedup"]["candidates"] == 0
                and len(staging_ids) == len(set(staging_ids)) == 1
                and len(chunk_ids) == len(set(chunk_ids)) == 1
            )
        finally:
            if chunk_ids:
                await service.vectors.delete(chunk_ids)
            try:
                if await service.vectors.client.has_collection(collection, timeout=10):
                    await service.vectors.client.drop_collection(collection_name=collection, timeout=10)
            except Exception:
                pass
            async with service.repo.sessions.begin() as session:
                if staging_ids:
                    await session.execute(delete(qa_extraction_staging).where(qa_extraction_staging.c.id.in_(staging_ids)))
                if chunk_ids:
                    await session.execute(delete(knowledge_chunks).where(knowledge_chunks.c.id.in_(chunk_ids)))
                if conversation_id is not None:
                    await session.execute(delete(messages).where(messages.c.conversation_id == conversation_id))
                    await session.execute(delete(conversations).where(conversations.c.id == conversation_id))
                await session.execute(delete(knowledge_ingestion_state).where(
                    knowledge_ingestion_state.c.state_key.like(namespace + ":%")
                ))
    output = Path("../dev-notes/evals/ch03/mining-task9.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report.get("passed"), "output": str(output)}, ensure_ascii=False))
    if not report.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
