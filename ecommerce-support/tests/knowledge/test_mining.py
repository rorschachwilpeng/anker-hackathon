import pytest
from sqlalchemy import func, select


class FakeExtractor:
    def __init__(self, outputs=None, fail=False):
        self.outputs = list(outputs or [])
        self.fail = fail
        self.calls = []

    async def extract(self, conversations):
        from app.knowledge.extraction import ExtractionBatch

        self.calls.append(conversations)
        if self.fail is True or self.fail == len(self.calls):
            raise RuntimeError("upstream failed")
        return self.outputs.pop(0) if self.outputs else ExtractionBatch(items=[])


class FakeRepository:
    def __init__(self, sources, processed=None):
        self.sources = sources
        self.processed = processed or {}
        self.prepared = []
        self.commits = []
        self.completed_batches = set()
        self.finalized = []
        self.owned_checks = 0
        self.runs = {}

    async def completed_sources(self):
        return self.sources

    async def processed_fingerprints(self):
        return self.processed

    async def prepare_mining_run(self, run_id, plan):
        self.prepared.append((run_id, plan))
        active = self.runs.get(run_id)
        if active and active["status"] == "planned":
            return active
        self.runs[run_id] = plan
        return plan

    async def assert_writer_owned(self):
        self.owned_checks += 1

    async def completed_mining_batches(self, batch_nos):
        return self.completed_batches.intersection(batch_nos)

    async def commit_mining_batch(self, run_id, batch_no, accepted, rejected):
        self.commits.append((run_id, accepted, rejected, [], batch_no))
        self.completed_batches.add(batch_no)

    async def finalize_mining_run(self, run_id, sources, *, skipped=None):
        self.finalized.append((run_id, sources, skipped or []))
        self.runs[run_id]["status"] = "committed"

    async def commit_mining_run(self, run_id, accepted, rejected, sources):
        self.commits.append((run_id, accepted, rejected, sources))


def source(conversation_id, fingerprint, question="退货政策是什么？", answer="七天内可退货。"):
    return {
        "id": conversation_id,
        "fingerprint": fingerprint,
        "messages": [
            {"id": conversation_id * 10, "conversation_id": conversation_id, "role": "user", "content": question, "tool_calls": None},
            {"id": conversation_id * 10 + 1, "conversation_id": conversation_id, "role": "assistant", "content": answer, "tool_calls": None},
        ],
    }


@pytest.mark.asyncio
async def test_old_id_that_ended_late_is_processed_by_fingerprint_not_global_cursor():
    from app.knowledge.mining import MiningService

    repo = FakeRepository([source(2, "new"), source(50, "same")], {2: "old", 50: "same"})
    extractor = FakeExtractor()

    report = await MiningService(repo, extractor).extract_all("run-1")

    assert [part[0]["id"] for part in extractor.calls] == [2]
    assert report == {"scanned": 2, "staged": 0, "rejected": 0, "skipped": 0}
    assert [row["id"] for row in repo.finalized[0][1]] == [2]


@pytest.mark.asyncio
async def test_extraction_failure_does_not_mark_source_processed():
    from app.knowledge.mining import MiningService

    repo = FakeRepository([source(1, "v1")])
    with pytest.raises(RuntimeError, match="upstream failed"):
        await MiningService(repo, FakeExtractor(fail=True)).extract_all("run-2")
    assert repo.commits == []


@pytest.mark.asyncio
async def test_invalid_items_are_rejected_before_atomic_commit():
    from app.knowledge.extraction import ExtractedQA, ExtractionBatch
    from app.knowledge.mining import MiningService

    item = ExtractedQA(
        conversation_id=1,
        question="别的问法",
        answer="七天内可退货。",
        category="售后",
        evidence_message_ids=[10, 11],
    )
    repo = FakeRepository([source(1, "v1")])
    report = await MiningService(repo, FakeExtractor([ExtractionBatch(items=[item])])).extract_all("run-3")

    assert report == {"scanned": 1, "staged": 0, "rejected": 1, "skipped": 0}
    assert repo.commits[0][1] == []
    assert len(repo.commits[0][2]) == 1


def test_batches_keep_complete_tool_turns_and_respect_twenty_conversation_limit():
    from app.knowledge.mining import build_batches

    sources = [source(index, f"v{index}") for index in range(1, 23)]
    batches = build_batches(sources, byte_budget=100_000)
    assert [len(batch) for batch in batches] == [20, 2]

    tool_source = source(30, "v30")
    tool_source["messages"] = [
        {"id": 300, "conversation_id": 30, "role": "user", "content": "查物流", "tool_calls": None},
        {"id": 301, "conversation_id": 30, "role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
        {"id": 302, "conversation_id": 30, "role": "tool", "content": "结果", "tool_call_id": "call-1", "tool_calls": None},
        {"id": 303, "conversation_id": 30, "role": "assistant", "content": "模拟数据", "tool_calls": None},
    ]
    skipped = []
    tiny = build_batches([tool_source], byte_budget=250, skipped=skipped)
    assert tiny == []
    assert skipped == [{"source_id": 30, "reason": "turn_exceeds_byte_budget"}]


def test_oversized_turn_does_not_drop_other_short_turns_from_the_same_source():
    from app.knowledge.mining import build_batches

    mixed = source(31, "v31", question="短问题", answer="短答案")
    mixed["messages"] += [
        {"id": 312, "conversation_id": 31, "role": "user", "content": "很长" * 1000, "tool_calls": None},
        {"id": 313, "conversation_id": 31, "role": "assistant", "content": "长答案", "tool_calls": None},
        {"id": 314, "conversation_id": 31, "role": "user", "content": "另一个短问题", "tool_calls": None},
        {"id": 315, "conversation_id": 31, "role": "assistant", "content": "另一个短答案", "tool_calls": None},
    ]
    skipped = []
    batches = build_batches([mixed], byte_budget=500, skipped=skipped)

    assert [[message["id"] for message in part["messages"]] for batch in batches for part in batch] == [[310, 311], [314, 315]]
    assert skipped == [{"source_id": 31, "reason": "turn_exceeds_byte_budget"}]


@pytest.mark.asyncio
async def test_successful_batch_is_checkpointed_before_a_later_batch_fails():
    from app.knowledge.extraction import ExtractionBatch
    from app.knowledge.mining import MiningService

    sources = [source(index, f"v{index}") for index in range(1, 22)]
    repo = FakeRepository(sources)
    extractor = FakeExtractor([ExtractionBatch(items=[])], fail=2)

    with pytest.raises(RuntimeError, match="upstream failed"):
        await MiningService(repo, extractor, byte_budget=100_000).extract_all("run-checkpoint")

    assert len(repo.commits) == 1
    assert repo.finalized == []
    first_batch_no = repo.commits[0][4]

    resumed = FakeExtractor([ExtractionBatch(items=[])])
    report = await MiningService(repo, resumed, byte_budget=100_000).extract_all("run-resume")
    assert len(resumed.calls) == 1
    assert first_batch_no in repo.completed_batches
    assert report["staged"] == 0
    assert len(repo.finalized) == 1


@pytest.mark.asyncio
async def test_resume_reuses_original_batch_snapshot_when_source_changes():
    from app.knowledge.extraction import ExtractionBatch
    from app.knowledge.mining import MiningService

    sources = [source(index, f"v{index}") for index in range(1, 22)]
    repo = FakeRepository(sources)
    first = FakeExtractor([ExtractionBatch(items=[])], fail=2)
    with pytest.raises(RuntimeError):
        await MiningService(repo, first, byte_budget=100_000).extract_all("fixed-run")

    repo.sources[0] = source(1, "changed", question="新增消息", answer="新增答案")
    resumed = FakeExtractor([ExtractionBatch(items=[])])
    await MiningService(repo, resumed, byte_budget=100_000).extract_all("fixed-run")

    assert len(resumed.calls) == 1
    assert repo.finalized[-1][1][0]["fingerprint"] == "v1"


@pytest.mark.asyncio
async def test_staging_and_source_state_roll_back_together(knowledge_repo, monkeypatch):
    from app.knowledge.models import qa_extraction_staging

    run_id = "rollback-mining-run"
    original_put = knowledge_repo.state.put
    calls = 0

    async def fail_on_source(key, payload, session=None):
        nonlocal calls
        calls += 1
        if key.startswith("mining:source:"):
            raise RuntimeError("commit interrupted")
        return await original_put(key, payload, session)

    monkeypatch.setattr(knowledge_repo.state, "put", fail_on_source)
    item = {
        "conversation_id": 999999,
        "question": "测试问题",
        "answer": "测试答案",
        "category": "测试",
        "evidence_message_ids": [1, 2],
    }
    with pytest.raises(RuntimeError, match="commit interrupted"):
        await knowledge_repo.commit_mining_run(
            run_id,
            [item],
            [],
            [{"id": 999999, "fingerprint": "v1", "messages": []}],
        )

    batch_no = __import__("hashlib").sha256(run_id.encode()).hexdigest()[:32]
    async with knowledge_repo.sessions() as session:
        count = (await session.execute(
            select(func.count()).select_from(qa_extraction_staging).where(
                qa_extraction_staging.c.batch_no == batch_no
            )
        )).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_valid_evidence_upgrades_an_earlier_discarded_duplicate(knowledge_repo):
    from app.knowledge.models import qa_extraction_staging
    from sqlalchemy import delete

    item = {
        "conversation_id": 990001,
        "question": "补全证据后的问题",
        "answer": "补全证据后的答案",
        "category": "测试",
        "evidence_message_ids": [91, 92],
    }
    try:
        await knowledge_repo.commit_mining_batch("invalid-first", "invalid-first-batch", [], [item])
        await knowledge_repo.commit_mining_batch("valid-later", "valid-later-batch", [item], [])
        await knowledge_repo.commit_mining_batch("valid-retry", "valid-retry-batch", [item], [])
        async with knowledge_repo.sessions() as session:
            rows = (await session.execute(select(qa_extraction_staging).where(
                qa_extraction_staging.c.source_ref == "990001",
                qa_extraction_staging.c.question == item["question"],
            ))).mappings().all()
        assert len(rows) == 1
        assert rows[0]["status"] == "extracted"
        metadata = await knowledge_repo.state.get("mining:qa:" + str(rows[0]["id"]))
        assert metadata["evidence_message_ids"] == [91, 92]
    finally:
        async with knowledge_repo.sessions.begin() as session:
            await session.execute(delete(qa_extraction_staging).where(
                qa_extraction_staging.c.source_ref == "990001"
            ))


@pytest.mark.asyncio
async def test_run_orders_extract_dedup_then_vectorize(monkeypatch):
    from app.knowledge.mining import MiningService

    events = []

    class Dedup:
        async def run(self):
            events.append("dedup")
            return {"candidates": 0}

    class Ingestion:
        async def resume(self):
            events.append("resume")
            return {"pending": 0}

    service = MiningService(FakeRepository([]), FakeExtractor(), dedup_service=Dedup(), ingestion=Ingestion())
    original = service.extract_all

    async def extract(run_id):
        events.append("extract")
        return await original(run_id)

    monkeypatch.setattr(service, "extract_all", extract)
    report = await service.run("ordered-run")
    assert events == ["extract", "dedup", "resume"]
    assert report["run_id"] == "ordered-run"
