from dataclasses import replace

def test_metadata_does_not_change_embedding():
    from app.knowledge.types import ChunkDraft
    from app.knowledge.text import embedding_text, content_hash
    a = ChunkDraft('配送','运费说明','满99元包邮。')
    b = replace(a, section_path='内部', content_type='manual', is_key_clause=True)
    assert embedding_text(a) == 'category: 配送\nquestions: 运费说明\nanswer: 满99元包邮。'
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash(replace(a,answer='不包邮。'))
