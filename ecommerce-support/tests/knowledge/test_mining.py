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
        if self.fail:
            raise RuntimeError("upstream failed")
        return self.outputs.pop(0) if self.outputs else ExtractionBatch(items=[])


class FakeRepository:
    def __init__(self, sources, processed=None):
        self.sources = sources
        self.processed = processed or {}
        self.prepared = []
        self.commits = []

    async def completed_sources(self):
        return self.sources

    async def processed_fingerprints(self):
        return self.processed

    async def prepare_mining_run(self, run_id, batches):
        self.prepared.append((run_id, batches))

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
    assert report == {"scanned": 2, "staged": 0, "rejected": 0}
    assert [row["id"] for row in repo.commits[0][3]] == [2]


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

    assert report == {"scanned": 1, "staged": 0, "rejected": 1}
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
    tiny = build_batches([tool_source], byte_budget=250)
    assert len(tiny) == 1
    assert [message["id"] for message in tiny[0][0]["messages"]] == [300, 301, 302, 303]


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
