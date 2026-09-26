import json
import logging

from app.knowledge.types import KnowledgeError


logger = logging.getLogger(__name__)


class KnowledgeSearch:
    def __init__(self, repo, embeddings, vectors, threshold: float, top_k: int = 5):
        if not -1 <= threshold <= 1:
            raise ValueError("threshold must be between -1 and 1")
        if not 1 <= top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        self.repo = repo
        self.embeddings = embeddings
        self.vectors = vectors
        self.threshold = threshold
        self.top_k = top_k

    async def find(self, keyword: str) -> list[dict]:
        vectors = await self.embeddings.encode([keyword])
        hits = await self.vectors.search(vectors[0], self.top_k)
        accepted = [hit for hit in hits if hit.score >= self.threshold]
        if not accepted:
            return []
        rows = await self.repo.load_done([hit.id for hit in accepted])
        items = []
        for hit in accepted:
            row = rows.get(hit.id)
            if row is None or row.content_hash != hit.content_hash:
                logger.info("knowledge candidate rejected id=%s reason=stale", hit.id)
                continue
            items.append({
                "id": row.id,
                "question": row.draft.questions,
                "answer": row.draft.answer,
                "category": row.draft.category,
            })
        return items

    async def find_for_model(self, query: str, confirmed_model: str) -> list[dict]:
        vectors = await self.embeddings.encode([query])
        hits = await self.vectors.search(vectors[0], 20)
        accepted = [hit for hit in hits if hit.score >= self.threshold]
        if not accepted:
            return []
        rows = await self.repo.load_done([hit.id for hit in accepted])
        items = []
        for hit in accepted:
            row = rows.get(hit.id)
            if row is None or row.content_hash != hit.content_hash:
                continue
            draft = row.draft
            if draft.product_model != confirmed_model or not draft.source_url:
                continue
            items.append({"id": row.id, "answer": draft.answer, "product_model": draft.product_model,
                          "source_title": draft.source_title, "source_url": draft.source_url})
        return items


def _outer_size(payload: dict) -> int:
    outer = {"ok": True, "data": payload, "error": None}
    return len(json.dumps(outer, ensure_ascii=False).encode())


def pack_faq_result(keyword: str, items: list[dict], limit: int = 2048) -> dict:
    payload = {"keyword": keyword, "found": False, "items": []}
    if not items:
        return payload
    for item in items:
        candidate = {"keyword": keyword, "found": True, "items": [*payload["items"], item]}
        if _outer_size(candidate) <= limit:
            payload = candidate
        else:
            logger.info("knowledge candidate rejected id=%s reason=result_budget", item.get("id"))
    if not payload["items"]:
        raise KnowledgeError("knowledge_budget_exceeded")
    return payload
