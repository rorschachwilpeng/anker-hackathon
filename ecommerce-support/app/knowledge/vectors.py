from app.knowledge.types import DIMENSIONS, Hit, VectorError
from app.knowledge.embeddings import valid_vector


class MilvusVectors:
    def __init__(self, client, collection='knowledge'):
        self.client=client
        self.collection=collection

    async def ensure_collection(self):
        from pymilvus import DataType
        if not await self.client.has_collection(self.collection,timeout=10):
            schema=self.client.create_schema(auto_id=False,enable_dynamic_field=False)
            schema.add_field('id',DataType.INT64,is_primary=True)
            schema.add_field('vector',DataType.FLOAT_VECTOR,dim=DIMENSIONS)
            schema.add_field('content_hash',DataType.VARCHAR,max_length=64)
            index=self.client.prepare_index_params()
            index.add_index(field_name='vector',index_type='FLAT',metric_type='COSINE')
            await self.client.create_collection(self.collection,schema=schema,index_params=index,consistency_level='Strong',timeout=30)
        description=await self.client.describe_collection(self.collection,timeout=10)
        fields={f['name']:f for f in description['fields']}
        if set(fields)!={'id','vector','content_hash'} or description.get('auto_id') or description.get('enable_dynamic_field'):
            raise VectorError('vector_schema_mismatch')
        if fields['id']['type']!=DataType.INT64 or not fields['id'].get('is_primary') or fields['vector']['type']!=DataType.FLOAT_VECTOR or int(fields['vector']['params']['dim'])!=DIMENSIONS or fields['content_hash']['type']!=DataType.VARCHAR:
            raise VectorError('vector_schema_mismatch')
        index=await self.client.describe_index(self.collection,'vector',timeout=10)
        if index.get('metric_type')!='COSINE': raise VectorError('vector_metric_mismatch')
        await self.client.load_collection(self.collection,timeout=30)

    async def upsert(self, rows):
        if not rows:return []
        ids=[r.id for r,v in rows]
        if len(set(ids))!=len(ids) or any(type(i) is not int or not 0<i<2**63 for i in ids):
            raise VectorError('vector_invalid_id')
        if any(not valid_vector(v) for r,v in rows):raise VectorError('vector_invalid_embedding')
        data=[{'id':r.id,'vector':v,'content_hash':r.content_hash} for r,v in rows]
        result=await self.client.upsert(collection_name=self.collection,data=data,timeout=15)
        if sorted(result.get('ids',[]))!=sorted(ids):raise VectorError('upsert_ids_mismatch')
        return ids

    async def search(self, vector, top_k):
        if not valid_vector(vector):raise VectorError('vector_invalid_embedding')
        results=await self.client.search(self.collection,data=[vector],anns_field='vector',limit=top_k,
            output_fields=['content_hash'],search_params={'metric_type':'COSINE'},consistency_level='Strong',timeout=10)
        return [Hit(int(r['id']),float(r['distance']),r['entity']['content_hash']) for r in results[0]]

    async def delete(self, ids):
        if ids: await self.client.delete(self.collection,ids=ids,timeout=15)
