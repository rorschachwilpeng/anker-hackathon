import pytest
from app.config import Settings


def test_hybrid_requires_calibrated_threshold_and_cloud_key():
    settings=Settings(_env_file=None,openai_base_url='http://example.invalid',openai_model='fake',openai_api_key='fake',
        embedding_api_key='fake',knowledge_score_threshold=.2,rerank_score_threshold=None)
    with pytest.raises(ValueError,match='rerank'):
        settings.ensure_hybrid_ready()
