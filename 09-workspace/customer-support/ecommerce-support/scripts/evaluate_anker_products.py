"""Evaluate the curated Anker product knowledge pack through the active production retrieval path."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import httpx
from pymilvus import AsyncMilvusClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.database import Database
from app.knowledge.embeddings import SiliconFlowEmbeddings
from app.knowledge.hybrid_vectors import HybridVectors
from app.knowledge.migration import active_collection
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.reranker import RankedChunk, SiliconFlowReranker
from app.knowledge.state import StateStore
from app.knowledge.vectors import MilvusVectors


CASES = ROOT / "evals/anker-products.jsonl"
REPORT = ROOT.parent / "dev-notes/evals/anker-products-report.json"


def load_cases() -> list[dict]:
    return [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]


async def evaluate() -> dict:
    settings = Settings()
    settings.ensure_knowledge_ready(search=False)
    database = Database(settings.database_url.get_secret_value())
    milvus = AsyncMilvusClient(settings.milvus_uri)
    async with httpx.AsyncClient() as client:
        try:
            state = StateStore(database, namespace="knowledge")
            collection = await active_collection(state)
            if collection == "knowledge_hybrid_v1":
                settings.ensure_hybrid_ready()
            else:
                settings.ensure_knowledge_ready(search=True)
            fingerprint = hashlib.sha256(
                f'{settings.embedding_base_url.rstrip("/")}|{settings.embedding_model}|1024|text-v1'.encode()
            ).hexdigest()
            repository = KnowledgeRepository(database.sessions, state=state, fingerprint=fingerprint)
            vectors = (
                HybridVectors(milvus, collection)
                if collection == "knowledge_hybrid_v1"
                else MilvusVectors(milvus, collection)
            )
            await vectors.ensure_collection()
            embeddings = SiliconFlowEmbeddings(client, settings)
            reranker = SiliconFlowReranker(client, settings) if isinstance(vectors, HybridVectors) else None
            hybrid_search = None
            if isinstance(vectors, HybridVectors):
                from app.knowledge.hybrid_retrieval import HybridKnowledgeSearch
                from app.knowledge.query import QueryUnderstanding
                from app.service import CustomerSupportService
                model = CustomerSupportService(settings, http_async_client=client).model
                hybrid_search = HybridKnowledgeSearch(
                    repository,
                    embeddings,
                    vectors,
                    QueryUnderstanding(model, settings.structured_output_method),
                    reranker,
                    settings.rerank_score_threshold,
                )
            results = []
            for case in load_cases():
                if isinstance(vectors, HybridVectors):
                    evidence = await hybrid_search.find_evidence(
                        case["query"], strategy="hybrid_rerank"
                    )
                    accepted = [RankedChunk(row, score) for row, score in evidence]
                else:
                    vector = (await embeddings.encode([case["query"]]))[0]
                    candidates = await vectors.search(vector, settings.knowledge_top_k)
                    rows = await repository.load_done([candidate.id for candidate in candidates])
                    current = [
                        rows[candidate.id]
                        for candidate in candidates
                        if candidate.id in rows
                        and rows[candidate.id].content_hash == candidate.content_hash
                    ]
                    score_by_id = {candidate.id: candidate.score for candidate in candidates}
                    ranked = [RankedChunk(row, score_by_id[row.id]) for row in current]
                    accepted = [item for item in ranked if item.score >= settings.knowledge_score_threshold]
                expected = case["expected"]
                matching = next(
                    (
                        item for item in accepted[:5]
                        if expected
                        and expected in item.row.draft.answer
                        and (
                            not case.get("expected_path")
                            or case["expected_path"] == item.row.draft.section_path
                        )
                    ),
                    None,
                )
                passed = not accepted if expected is None else matching is not None
                results.append({
                    "case_id": case["case_id"],
                    "query": case["query"],
                    "expected": expected,
                    "passed": passed,
                    "matched_chunk_id": matching.row.id if matching else None,
                    "top5": [
                        {
                            "chunk_id": item.row.id,
                            "score": item.score,
                            "section_path": item.row.draft.section_path,
                        }
                        for item in accepted[:5]
                    ],
                })
            return {
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
                "collection": collection,
                "retrieval": "hybrid_rrf" if isinstance(vectors, HybridVectors) else "dense",
                "embedding_model": settings.embedding_model,
                "rerank_model": settings.rerank_model,
                "rerank_threshold": settings.rerank_score_threshold,
                "active_threshold": {
                    "name": "rerank_score_threshold" if isinstance(vectors, HybridVectors) else "knowledge_score_threshold",
                    "value": settings.rerank_score_threshold if isinstance(vectors, HybridVectors) else settings.knowledge_score_threshold,
                },
                "passed": sum(item["passed"] for item in results),
                "total": len(results),
                "cases": results,
            }
        finally:
            await milvus.close()
            await database.close()


if __name__ == "__main__":
    report = asyncio.run(evaluate())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": report["passed"], "total": report["total"], "report": str(REPORT)}, ensure_ascii=False))
    raise SystemExit(0 if report["passed"] == report["total"] else 1)
