import pytest
from sqlalchemy import delete, insert, select


def existing(**changes):
    value = {"id": 7, "category": "配送", "questions": "邮费是多少", "answer": "满99元免邮。", "content_type": "faq"}
    value.update(changes)
    return value


def candidate(**changes):
    value = {"staging_id": 1, "category": "配送", "question": "邮费是多少", "answer": "满99元免邮。"}
    value.update(changes)
    return value


def test_normalization_preserves_numbers_and_negation():
    from app.knowledge.dedup import normalize_text

    assert normalize_text("  满  99 元  ") == "满 99 元"
    assert normalize_text("不能退") != normalize_text("能退")
    assert normalize_text("7天") != normalize_text("15天")


@pytest.mark.asyncio
async def test_exact_duplicate_is_discarded_without_model_or_embeddings():
    from app.knowledge.dedup import Deduplicator

    class Never:
        def __getattr__(self, name):
            raise AssertionError(name)

    decision = await Deduplicator(Never(), Never()).decide(candidate(), [existing()])
    assert decision.action == "discard"
    assert decision.target_id == 7


@pytest.mark.asyncio
async def test_same_fact_new_wording_merges_without_rewriting_answer():
    from app.knowledge.dedup import Deduplicator

    class Never:
        def __getattr__(self, name):
            raise AssertionError(name)

    decision = await Deduplicator(Never(), Never()).decide(candidate(question="配送费怎么收"), [existing()])
    assert decision.action == "merge"
    assert decision.target_id == 7


@pytest.mark.asyncio
async def test_same_question_conflicting_answer_goes_to_review():
    from app.knowledge.dedup import Deduplicator

    class Never:
        def __getattr__(self, name):
            raise AssertionError(name)

    decision = await Deduplicator(Never(), Never()).decide(candidate(answer="每单10元。"), [existing()])
    assert decision.action == "review"
    assert decision.target_id == 7


@pytest.mark.asyncio
async def test_semantic_candidate_uses_top_five_and_validates_model_target():
    from app.knowledge.dedup import DedupDecision, Deduplicator

    captured = {}

    class Embeddings:
        async def encode(self, texts):
            captured["texts"] = texts
            return [[1.0, float(index)] for index, _ in enumerate(texts)]

    class Structured:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return DedupDecision(action="new", target_id=None, reason="different fact")

    class Model:
        def with_structured_output(self, schema, *, method):
            captured.update(schema=schema, method=method)
            return Structured()

    corpus = [existing(id=index, questions=f"问题{index}", answer=f"答案{index}") for index in range(1, 8)]
    decision = await Deduplicator(Model(), Embeddings()).decide(candidate(question="另一种问法", answer="另一事实"), corpus)
    assert decision.action == "new"
    assert captured["schema"] is DedupDecision
    assert captured["method"] == "json_mode"
    assert len(captured["texts"]) == 8


@pytest.mark.asyncio
async def test_retryable_embedding_failure_is_retried(monkeypatch):
    from app.knowledge.dedup import DedupDecision, Deduplicator
    from app.knowledge.types import EmbeddingError

    calls = 0

    class Embeddings:
        async def encode(self, texts):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise EmbeddingError("temporary", retryable=True)
            return [[1.0, 0.0], [1.0, 0.0]]

    class Structured:
        async def ainvoke(self, messages):
            return DedupDecision(action="new", reason="different")

    class Model:
        def with_structured_output(self, schema, *, method):
            return Structured()

    async def no_sleep(delay):
        return None

    monkeypatch.setattr("app.knowledge.dedup.asyncio.sleep", no_sleep)
    decision = await Deduplicator(Model(), Embeddings()).decide(
        candidate(question="不同问题", answer="不同答案"), [existing()]
    )
    assert decision.action == "new"
    assert calls == 2


@pytest.mark.asyncio
async def test_missing_category_is_review_without_model_call():
    from app.knowledge.dedup import Deduplicator

    decision = await Deduplicator(None, None).decide(candidate(category=""), [])
    assert decision.action == "review"


@pytest.mark.asyncio
async def test_new_knowledge_and_staging_status_commit_together(knowledge_repo):
    from app.knowledge.dedup import DedupDecision
    from app.knowledge.models import knowledge_chunks, qa_extraction_staging

    async with knowledge_repo.sessions.begin() as session:
        result = await session.execute(insert(qa_extraction_staging).values(
            batch_no="dedup-new", source_ref="900001", question="测试新问法", answer="测试新答案", status="extracted"
        ))
        staging_id = result.inserted_primary_key[0]
    await knowledge_repo.state.put("mining:qa:" + str(staging_id), {
        "conversation_id": 900001, "category": "测试分类", "evidence_message_ids": [1, 2]
    })
    try:
        rows = await knowledge_repo.staging_candidates()
        row = next(item for item in rows if item["staging_id"] == staging_id)
        created = await knowledge_repo.apply_dedup(row, DedupDecision(action="new", reason="new fact"))
        async with knowledge_repo.sessions() as session:
            status = (await session.execute(select(qa_extraction_staging.c.status).where(
                qa_extraction_staging.c.id == staging_id
            ))).scalar_one()
            vector_status = (await session.execute(select(knowledge_chunks.c.vectorize_status).where(
                knowledge_chunks.c.id == created["id"]
            ))).scalar_one()
        assert status == "kept"
        assert vector_status == "pending"
    finally:
        async with knowledge_repo.sessions.begin() as session:
            await session.execute(delete(qa_extraction_staging).where(qa_extraction_staging.c.id == staging_id))


@pytest.mark.asyncio
async def test_dedup_transaction_failure_rolls_back_chunk_and_status(knowledge_repo, monkeypatch):
    from app.knowledge.dedup import DedupDecision
    from app.knowledge.models import knowledge_chunks, qa_extraction_staging

    answer = "rollback-answer-unique"
    async with knowledge_repo.sessions.begin() as session:
        result = await session.execute(insert(qa_extraction_staging).values(
            batch_no="dedup-rollback", source_ref="900002", question="回滚问法", answer=answer, status="extracted"
        ))
        staging_id = result.inserted_primary_key[0]
    original_put = knowledge_repo.state.put

    async def fail_decision(key, payload, session=None):
        if key.startswith("dedup:staging:"):
            raise RuntimeError("dedup interrupted")
        return await original_put(key, payload, session)

    monkeypatch.setattr(knowledge_repo.state, "put", fail_decision)
    row = {"staging_id": staging_id, "conversation_id": 900002, "category": "测试", "question": "回滚问法", "answer": answer}
    try:
        with pytest.raises(RuntimeError, match="dedup interrupted"):
            await knowledge_repo.apply_dedup(row, DedupDecision(action="new", reason="new fact"))
        async with knowledge_repo.sessions() as session:
            status = (await session.execute(select(qa_extraction_staging.c.status).where(
                qa_extraction_staging.c.id == staging_id
            ))).scalar_one()
            chunk = (await session.execute(select(knowledge_chunks.c.id).where(
                knowledge_chunks.c.answer == answer
            ))).first()
        assert status == "extracted"
        assert chunk is None
    finally:
        async with knowledge_repo.sessions.begin() as session:
            await session.execute(delete(qa_extraction_staging).where(qa_extraction_staging.c.id == staging_id))
