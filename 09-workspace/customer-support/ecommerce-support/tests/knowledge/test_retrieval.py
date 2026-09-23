import json

import pytest

from app.knowledge.types import ChunkDraft, ChunkSnapshot, Hit, KnowledgeError


class Encoder:
    async def encode(self, texts):
        assert texts == ["邮费是多少"]
        return [[1.0] * 1024]


class Vectors:
    async def search(self, vector, top_k):
        assert top_k == 5
        return [
            Hit(3, 0.91, "c" * 64),
            Hit(1, 0.88, "a" * 64),
            Hit(2, 0.70, "b" * 64),
        ]


class Repo:
    async def load_done(self, ids):
        assert ids == [3, 1]
        return {
            3: ChunkSnapshot(3, ChunkDraft("配送", "运费", "旧答案。"), "stale"),
            1: ChunkSnapshot(1, ChunkDraft("配送", "运费", "满99元包邮。"), "a" * 64),
        }

    async def find_faq(self, keyword):
        raise AssertionError("SQL LIKE must not run")


async def test_dense_threshold_order_and_stale_hash_filter():
    from app.knowledge.retrieval import KnowledgeSearch

    rows = await KnowledgeSearch(Repo(), Encoder(), Vectors(), 0.8).find("邮费是多少")
    assert rows == [{"id": 1, "question": "运费", "answer": "满99元包邮。", "category": "配送"}]


async def test_no_hit_does_not_query_mysql():
    from app.knowledge.retrieval import KnowledgeSearch

    class EmptyVectors:
        async def search(self, vector, top_k):
            return [Hit(1, -0.2, "a" * 64)]

    class NoRepo:
        async def load_done(self, ids):
            raise AssertionError("no SQL lookup expected")

    assert await KnowledgeSearch(NoRepo(), Encoder(), EmptyVectors(), 0.0).find("邮费是多少") == []


def test_budget_keeps_whole_answers():
    from app.knowledge.retrieval import pack_faq_result

    result = pack_faq_result("邮费", [
        {"id": 1, "question": "运费", "answer": "完整条款。" * 1000, "category": "配送"},
        {"id": 2, "question": "运费", "answer": "满99元包邮。", "category": "配送"},
    ])
    assert [x["id"] for x in result["items"]] == [2]
    outer = {"ok": True, "data": result, "error": None}
    assert len(json.dumps(outer, ensure_ascii=False).encode()) <= 2048


def test_all_semantic_hits_over_budget_is_error():
    from app.knowledge.retrieval import pack_faq_result

    with pytest.raises(KnowledgeError, match="knowledge_budget_exceeded"):
        pack_faq_result("邮费", [{
            "id": 1, "question": "运费", "answer": "完整条款。" * 1000, "category": "配送"
        }])


def test_empty_semantic_hits_are_normal_not_found():
    from app.knowledge.retrieval import pack_faq_result

    assert pack_faq_result("不存在", []) == {"keyword": "不存在", "found": False, "items": []}
