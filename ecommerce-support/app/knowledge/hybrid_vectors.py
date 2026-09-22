"""Milvus 2.6 native BM25 and dense retrieval over one authoritative chunk ID."""
from dataclasses import dataclass
import json
import math
import re

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, RRFRanker
from app.knowledge.embeddings import valid_vector
from app.knowledge.text import embedding_text
from app.knowledge.types import VectorError


@dataclass(frozen=True)
class Candidate:
    id: int
    score: float
    content_hash: str
    rank: int


class HybridVectors:
    def __init__(self, client, collection='knowledge_hybrid_v1'):
        self.client = client
        self.collection = collection

    async def ensure_collection(self):
        if not await self.client.has_collection(self.collection, timeout=10):
            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field('id', DataType.INT64, is_primary=True)
            schema.add_field('vector', DataType.FLOAT_VECTOR, dim=1024)
            schema.add_field('text', DataType.VARCHAR, max_length=6000,
                             enable_analyzer=True, analyzer_params={'type':'chinese'})
            schema.add_field('sparse', DataType.SPARSE_FLOAT_VECTOR)
            schema.add_field('category', DataType.VARCHAR, max_length=1024)
            schema.add_field('content_hash', DataType.VARCHAR, max_length=64)
            schema.add_function(Function(name='text_bm25', function_type=FunctionType.BM25,
                input_field_names=['text'], output_field_names=['sparse']))
            indexes = self.client.prepare_index_params()
            indexes.add_index(field_name='vector', index_type='FLAT', metric_type='COSINE')
            indexes.add_index(field_name='sparse', index_type='SPARSE_INVERTED_INDEX', metric_type='BM25')
            await self.client.create_collection(self.collection, schema=schema, index_params=indexes,
                                                consistency_level='Strong', timeout=30)
        description = await self.client.describe_collection(self.collection, timeout=10)
        fields = {f['name']:f for f in description['fields']}
        if (set(fields) != {'id','vector','text','sparse','category','content_hash'}
                or description.get('auto_id') or description.get('enable_dynamic_field')):
            raise VectorError('hybrid_schema_mismatch')
        if (fields['id']['type'] != DataType.INT64 or not fields['id'].get('is_primary')
                or fields['vector']['type'] != DataType.FLOAT_VECTOR
                or int(fields['vector']['params']['dim']) != 1024
                or fields['text']['type'] != DataType.VARCHAR
                or fields['sparse']['type'] != DataType.SPARSE_FLOAT_VECTOR
                or fields['category']['type'] != DataType.VARCHAR
                or fields['content_hash']['type'] != DataType.VARCHAR):
            raise VectorError('hybrid_schema_mismatch')
        text_params = fields['text'].get('params', {})
        analyzer = text_params.get('analyzer_params')
        if isinstance(analyzer, str):
            try:
                analyzer = json.loads(analyzer)
            except ValueError:
                raise VectorError('hybrid_analyzer_mismatch') from None
        if (str(text_params.get('enable_analyzer')).lower() != 'true'
                or analyzer != {'type':'chinese'}):
            raise VectorError('hybrid_analyzer_mismatch')
        funcs = description.get('functions', [])
        if len(funcs) != 1 or funcs[0].get('input_field_names') != ['text'] or funcs[0].get('output_field_names') != ['sparse']:
            raise VectorError('hybrid_bm25_mismatch')
        for field, metric in [('vector','COSINE'),('sparse','BM25')]:
            index = await self.client.describe_index(self.collection, field, timeout=10)
            if index.get('metric_type') != metric:
                raise VectorError('hybrid_metric_mismatch')
        await self.client.load_collection(self.collection, timeout=30)

    async def upsert(self, rows):
        if not rows:
            return []
        ids = [row.id for row,_ in rows]
        if len(set(ids)) != len(ids) or any(type(i) is not int or not 0 < i < 2**63 for i in ids):
            raise VectorError('vector_invalid_id')
        if any(not valid_vector(vector) for _,vector in rows):
            raise VectorError('vector_invalid_embedding')
        data = [{'id': row.id, 'vector':vector, 'text':embedding_text(row.draft),
                 'category':row.draft.category,'content_hash':row.content_hash}
                for row,vector in rows]
        if any(len(r['text'].encode()) > 6000 or len(r['category'].encode()) > 1024 for r in data):
            raise VectorError('vector_text_too_large')
        result = await self.client.upsert(collection_name=self.collection, data=data, timeout=20)
        if sorted(result.get('ids',[])) != sorted(ids):
            raise VectorError('upsert_ids_mismatch')
        return ids

    async def retrieve(self, canonical, lexical, vector, category=None, strategy='hybrid', limit=50):
        if strategy not in ('dense','bm25','hybrid') or not 1 <= limit <= 50:
            raise VectorError('invalid_strategy')
        if not valid_vector(vector) or not canonical.strip() or not lexical.strip():
            raise VectorError('invalid_query')
        if category is not None and not re.fullmatch(r'[\w\u4e00-\u9fff/ ·-]{1,255}', category):
            raise VectorError('invalid_category')
        expression = f'category == "{category}"' if category else None
        dense = AnnSearchRequest(data=[vector], anns_field='vector', param={'metric_type':'COSINE'},
                                 limit=limit, expr=expression)
        sparse = AnnSearchRequest(data=[lexical], anns_field='sparse', param={'metric_type':'BM25'},
                                  limit=limit, expr=expression)
        try:
            if strategy == 'hybrid':
                result = await self.client.hybrid_search(self.collection, reqs=[dense,sparse],
                    ranker=RRFRanker(), limit=limit, output_fields=['content_hash'], timeout=15)
            else:
                field = 'vector' if strategy == 'dense' else 'sparse'
                result = await self.client.search(self.collection,
                    data=[vector] if strategy == 'dense' else [lexical], anns_field=field,
                    limit=limit, filter=expression or '', output_fields=['content_hash'],
                    search_params={'metric_type':'COSINE' if strategy == 'dense' else 'BM25'}, timeout=15)
        except Exception:
            raise VectorError('hybrid_search_unavailable', retryable=True) from None
        hits = []
        for rank, hit in enumerate(result[0], 1):
            ident, score = hit['id'], hit['distance']
            digest = hit['entity']['content_hash']
            if (type(ident) is not int or not 0 < ident < 2**63 or not isinstance(score,(int,float))
                    or not math.isfinite(score) or not isinstance(digest,str) or len(digest) != 64
                    or any(item.id == ident for item in hits)):
                raise VectorError('hybrid_invalid_hit')
            hits.append(Candidate(ident,float(score),digest,rank))
        return hits

    async def delete(self, ids):
        if ids:
            await self.client.delete(self.collection, ids=ids, timeout=15)

    async def search(self, vector, top_k):
        # Compatibility for the ch03 dense-only callers until the online hybrid path is activated.
        return await self.retrieve('dense', 'dense', vector, strategy='dense', limit=top_k)

    async def verify(self, rows):
        if not rows:
            return True
        ids = [ident for ident,_ in rows]
        actual = await self.client.query(self.collection, ids=ids,
                                         output_fields=['id','content_hash'], timeout=15)
        return ({r['id']:r['content_hash'] for r in actual} == dict(rows)
                and len(actual) == len(rows))
