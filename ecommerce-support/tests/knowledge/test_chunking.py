import pytest


def test_heading_hierarchy_and_fences():
    from app.knowledge.chunking import split_markdown
    rows=split_markdown('# 商城\n## 配送\n### 运费\n满99元包邮。\n\n```python\n# not a title\nx=1\n```','演示','policy')
    assert all(r.questions=='运费' and r.category=='商城 / 配送' for r in rows)
    assert all(r.section_path=='商城 / 配送 / 运费' for r in rows)
    assert any('```python\n# not a title\nx=1\n```' in r.answer for r in rows)


def test_sentence_boundaries_overlap_and_progress():
    from app.knowledge.chunking import split_markdown
    sentences=[f'第{i}项'+('说明'*20)+'。' for i in range(35)]
    rows=split_markdown('## 规则\n'+''.join(sentences),'商城','policy')
    assert 2 < len(rows) < 20
    assert all(r.answer.endswith('。') for r in rows)
    for sentence in sentences:
        assert any(sentence in r.answer for r in rows)
    assert any(any(s in a.answer and s in b.answer for s in sentences) for a,b in zip(rows,rows[1:]))


def test_table_headers_repeated_rows_not_lost():
    from app.knowledge.chunking import split_markdown
    header='| 地区 | 运费 |\n| --- | --- |'
    data=[f'| 地区{i} | '+('说明'*12)+' |' for i in range(40)]
    rows=split_markdown('## 运费\n'+header+'\n'+'\n'.join(data),'商城','manual')
    assert len(rows)>1
    assert all(r.answer.startswith(header) for r in rows)
    assert [line for row in rows for line in row.answer.splitlines()[2:]]==data


@pytest.mark.parametrize('body', ['长'*2100, '|列|\n|---|\n|'+'长'*2100+'|', '```\n'+'长'*2100+'\n```'], ids=['sentence','table-row','code'])
def test_unsplittable_content_rejected(body):
    from app.knowledge.chunking import split_markdown, ChunkingError
    with pytest.raises(ChunkingError, match='too_large'):
        split_markdown('## 章节\n'+body,'商城','policy')


def test_key_clause_marker_not_embedded_and_empty_input():
    from app.knowledge.chunking import split_markdown
    assert split_markdown('','商城','faq')==[]
    rows=split_markdown('## 条款\n<!-- key-clause -->\n不支持无理由退货。\n\n其他说明。','商城','policy')
    assert rows[0].is_key_clause
    assert '<!--' not in rows[0].answer
    assert any(not r.is_key_clause for r in rows)


def test_long_paths_rejected_not_truncated():
    from app.knowledge.chunking import split_markdown, ChunkingError
    with pytest.raises(ChunkingError, match='path'):
        split_markdown('# '+('长'*260)+'\n## 子章节\n内容。','商城','policy')


def test_decimal_does_not_split_sentence():
    from app.knowledge.chunking import split_markdown
    sentence='商品价格为9.99元，不含运费。'
    rows=split_markdown('## 定价\n'+sentence*100,'商城','policy')
    assert all(r.answer.startswith('商品') and r.answer.endswith('。') for r in rows)
