"""Retrieve candidates from Milvus; return only current MySQL originals."""
import logging
from app.knowledge.query import QueryUnderstanding

logger = logging.getLogger(__name__)


class HybridKnowledgeSearch:
    def __init__(self, repo, embeddings, vectors, rewriter):
        self.repo, self.embeddings, self.vectors, self.rewriter = repo, embeddings, vectors, rewriter

    async def find_evidence(self, keyword, category=None, strategy='hybrid'):
        plan = await self.rewriter.normalize(keyword)
        available = await self.repo.categories()
        selected = category if category is not None else plan.category
        selected = selected if selected in available else None
        vector = (await self.embeddings.encode([plan.canonical]))[0]
        hits = await self.vectors.retrieve(plan.canonical,QueryUnderstanding.lexical(keyword,plan),vector,
                                           selected,strategy,limit=50)
        rows = await self.repo.load_done([hit.id for hit in hits])
        valid = []
        for hit in hits:
            row = rows.get(hit.id)
            if row is None or row.content_hash != hit.content_hash:
                logger.info('knowledge candidate rejected id=%s reason=stale',hit.id)
                continue
            valid.append((row,hit.score))
        return valid

    async def find(self, keyword):
        rows = await self.find_evidence(keyword)
        return [{'id':row.id,'question':row.draft.questions,'answer':row.draft.answer,
                 'category':row.draft.category} for row,_ in rows]
