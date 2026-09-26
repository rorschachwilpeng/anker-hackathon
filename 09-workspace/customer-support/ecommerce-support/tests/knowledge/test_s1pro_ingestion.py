import json

import pytest

from app.knowledge.ingestion import parse_directory
from app.knowledge.types import KnowledgeError


def test_source_manifest_applies_metadata_and_rejects_missing_url(tmp_path):
    root = tmp_path / 's1pro'
    root.mkdir()
    (root / 'product.md').write_text('# Product\nT2080 is the S1 Pro robot vacuum.\n')
    source = {'product_model': 'T2080', 'source_title': 'eufy S1 Pro', 'source_url': 'https://service.eufy.com/product-description/example', 'retrieved_on': '2026-09-23'}
    (root / 'sources.json').write_text(json.dumps({'product.md': source}))
    parsed = parse_directory(root)
    assert len(parsed) == 1
    assert all(draft.product_model == 'T2080' and draft.source_url == source['source_url'] for draft in parsed[0][1])

    (root / 'sources.json').write_text(json.dumps({'product.md': {**source, 'source_url': ''}}))
    with pytest.raises(KnowledgeError, match='invalid_source_manifest'):
        parse_directory(root)


def test_old_demo_directory_has_no_source_metadata(tmp_path):
    root = tmp_path / 'demo'
    root.mkdir()
    (root / 'policy.md').write_text('# Shipping\nDemo policy.\n')
    assert parse_directory(root)[0][1][0].product_model is None
