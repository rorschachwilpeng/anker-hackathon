import pytest
from app.config import Settings


def test_hybrid_requires_calibrated_threshold_and_cloud_key():
    settings=Settings(openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake',
        embedding_api_key='fake',knowledge_score_threshold=.2)
    with pytest.raises(ValueError,match='rerank'):
        settings.ensure_hybrid_ready()
