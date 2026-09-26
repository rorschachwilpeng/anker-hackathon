import math
import httpx
from app.knowledge.types import DIMENSIONS, EmbeddingError


def valid_vector(vector):
    return (isinstance(vector,list) and len(vector)==DIMENSIONS
            and all(type(v) in (int,float) and math.isfinite(v) for v in vector)
            and any(v != 0 for v in vector))


class SiliconFlowEmbeddings:
    def __init__(self, client, settings):
        self.client=client
        self.settings=settings
        self.last_usage=None

    async def encode(self, texts):
        if not texts: return []
        if any(not isinstance(t,str) or not t.strip() or len(t.encode())>6000 for t in texts):
            raise EmbeddingError('embedding_invalid_input')
        if not self.settings.embedding_api_key:
            raise EmbeddingError('embedding_not_configured')
        try:
            response=await self.client.post(self.settings.embedding_base_url.rstrip('/')+'/embeddings',
                headers={'Authorization':'Bearer '+self.settings.embedding_api_key.get_secret_value()},
                json={'model':self.settings.embedding_model,'input':texts,'encoding_format':'float'},timeout=15)
        except httpx.RequestError:
            raise EmbeddingError('embedding_network',retryable=True) from None
        if response.status_code!=200:
            raise EmbeddingError(f'embedding_http_{response.status_code}',retryable=response.status_code in (429,500,502,503,504))
        try:
            body=response.json()
            if body.get('model')!=self.settings.embedding_model or len(body['data'])!=len(texts): raise ValueError()
            by_index={}
            for row in body['data']:
                index=row['index']; vector=row['embedding']
                if type(index) is not int or not 0<=index<len(texts) or index in by_index or not valid_vector(vector): raise ValueError()
                by_index[index]=vector
            self.last_usage=body.get('usage')
            return [by_index[i] for i in range(len(texts))]
        except (ValueError,KeyError,TypeError,AttributeError):
            raise EmbeddingError('embedding_invalid_response') from None
