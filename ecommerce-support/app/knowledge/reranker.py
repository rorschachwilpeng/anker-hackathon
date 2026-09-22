"""SiliconFlow BGE rerank client with bounded retries and strict index mapping."""
import asyncio
from dataclasses import dataclass
import math

import httpx
from app.knowledge.text import embedding_text
from app.knowledge.types import KnowledgeError


@dataclass(frozen=True)
class RankedChunk:
    row: object
    score: float


class SiliconFlowReranker:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings

    async def rank(self, query, rows, top_n=10):
        if not rows:
            return []
        key = self.settings.rerank_api_key or self.settings.embedding_api_key
        if key is None or not key.get_secret_value().strip():
            raise KnowledgeError('rerank_not_configured')
        payload = {'model':self.settings.rerank_model,'query':query,
                   'documents':[embedding_text(row.draft) for row in rows],
                   'top_n':min(top_n,len(rows),10),'return_documents':False}
        for attempt in range(2):
            try:
                response = await self.client.post(self.settings.rerank_base_url.rstrip('/')+'/rerank',
                    headers={'Authorization':'Bearer '+key.get_secret_value()},json=payload,timeout=20)
            except httpx.RequestError:
                if attempt == 0:
                    await asyncio.sleep(.05)
                    continue
                raise KnowledgeError('rerank_network',retryable=True) from None
            if response.status_code in (429,500,502,503,504) and attempt == 0:
                await asyncio.sleep(.05)
                continue
            if response.status_code != 200:
                raise KnowledgeError('rerank_http_'+str(response.status_code),
                                     retryable=response.status_code in (429,500,502,503,504))
            try:
                results = response.json()['results']
                if not isinstance(results,list) or not 0 < len(results) <= payload['top_n']:
                    raise ValueError()
                seen, ranked = set(), []
                for item in results:
                    idx,score = item['index'],item['relevance_score']
                    if (type(idx) is not int or not 0 <= idx < len(rows) or idx in seen
                            or type(score) not in (int,float) or not math.isfinite(score)):
                        raise ValueError()
                    seen.add(idx)
                    ranked.append(RankedChunk(rows[idx],float(score)))
                if any(ranked[i].score < ranked[i+1].score for i in range(len(ranked)-1)):
                    raise ValueError()
                return ranked
            except (ValueError,KeyError,TypeError,IndexError):
                raise KnowledgeError('rerank_invalid_response') from None
        raise KnowledgeError('rerank_unavailable',retryable=True)
