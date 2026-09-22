import pytest


def test_wrong_source_fails_even_when_request_succeeded():
    from scripts.evaluate_ch03 import judge_case

    case = {"case_id": "x", "query": "邮费", "expected": "满99元包邮"}
    hits = [{"id": 9, "score": .9, "answer": "这是退货政策"}]
    result = judge_case(case, hits, .5)
    assert result["passed"] is False
    assert result["http_status"] == 200


def test_negative_case_fails_on_any_above_threshold_hit():
    from scripts.evaluate_ch03 import judge_case

    result = judge_case({"case_id": "n", "query": "天气", "expected": None}, [{"id": 1, "score": .8, "answer": "运费"}], .7)
    assert result["passed"] is False


def test_calibration_prioritizes_zero_negative_hits():
    from scripts.evaluate_ch03 import choose_threshold

    rows = [
        ({"expected": "运费"}, [{"id": 1, "score": .82, "answer": "运费"}]),
        ({"expected": None}, [{"id": 2, "score": .70, "answer": "退货"}]),
    ]
    threshold = choose_threshold(rows)
    assert .70 < threshold <= .82


def test_zero_vector_is_rejected():
    from scripts.evaluate_ch03 import require_vector
    from app.knowledge.types import KnowledgeError

    with pytest.raises(KnowledgeError):
        require_vector([0.0] * 1024)


@pytest.mark.parametrize("report", [
    {"failpoints": {"after-mysql": {"triggered": False}, "after-milvus": {"triggered": True}}},
    {"failpoints": {"after-mysql": {"triggered": True, "pending_after_resume": 1}, "after-milvus": {"triggered": True, "pending_after_resume": 0}}},
    {"failpoints": {"after-mysql": {"triggered": True, "pending_after_resume": 0}, "after-milvus": {"triggered": True, "pending_after_resume": 0, "unique_ids": False}}},
])
def test_recovery_requires_real_trigger_no_pending_and_unique_ids(report):
    from scripts.evaluate_ch03 import validate_recovery_report

    assert validate_recovery_report(report) is False
