from app.knowledge.types import ChunkDraft, ChunkSnapshot


def test_best_is_first_second_is_last_and_whole_chunks_only():
    from app.knowledge.evidence import pack_evidence
    from app.knowledge.reranker import RankedChunk
    rows = [RankedChunk(ChunkSnapshot(i,ChunkDraft('商品',f'型号{i}',f'完整回答{i}。'),'a'*64), 1-i/10)
            for i in range(1,5)]
    evidence = pack_evidence(rows,byte_budget=2000)
    assert [e.chunk_id for e in evidence] == [1,3,4,2]
    assert [e.n for e in evidence] == [1,2,3,4]
    small = pack_evidence(rows,byte_budget=75)
    assert all('完整回答' in e.answer for e in small)
    assert len(small) < len(evidence)
