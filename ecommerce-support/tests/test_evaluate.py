import json

import pytest

import httpx

import scripts.evaluate as evaluator
from scripts.evaluate import SSEParseError, evaluate_cases, exit_code_for_report, parse_sse


def test_parse_sse_handles_crlf_fragmented_utf8_and_json_newline():
    payload = (
        'event: token\r\ndata: {"text":"你好\\n客服"}\r\n\r\n'
        'event: done\r\ndata: {"finish_reason":"stop"}\r\n\r\n'
    ).encode("utf-8")
    split_inside_unicode = payload.index("你".encode("utf-8")) + 1

    events = parse_sse(
        [payload[:split_inside_unicode], payload[split_inside_unicode : split_inside_unicode + 2], payload[split_inside_unicode + 2 :]]
    )

    assert events == [
        ("token", {"text": "你好\n客服"}),
        ("done", {"finish_reason": "stop"}),
    ]


@pytest.mark.parametrize(
    "body",
    [
        'event: token\ndata: not-json\n\n',
        'event: token\ndata: ["not", "an", "object"]\n\n',
        'data: {"text":"missing event"}\n\n',
    ],
)
def test_parse_sse_rejects_malformed_events(body):
    with pytest.raises(SSEParseError):
        parse_sse(body)


@pytest.mark.parametrize(
    "body, expected_error",
    [
        (
            'event: error\ndata: {"code":"upstream_error","message":"暂时不可用"}\n\n',
            "upstream_error",
        ),
        ('event: token\ndata: {"text":"未结束"}\n\n', "missing done"),
        ('event: done\ndata: {"finish_reason":"stop"}\n\n', "empty successful response"),
        ('event: token\ndata: not-json\n\n', "not valid JSON"),
        (
            'event: token\ndata: {"text":"看似成功"}\n\nevent: done\ndata: {}\n\n',
            "finish_reason",
        ),
    ],
)
def test_chat_error_or_missing_done_is_a_failed_report(body, expected_error):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )
    )
    cases = {
        "extraction_cases": [],
        "chat_cases": [
            {
                "id": "chat_failure",
                "turns": [{"message": "你好", "expect": {}}],
            }
        ],
    }

    with httpx.Client(transport=transport, base_url="http://test") as client:
        report = evaluate_cases(cases, client)

    assert report["summary"] == {"total": 1, "passed": 0, "failed": 1}
    assert report["results"][0]["passed"] is False
    assert expected_error in report["results"][0]["error"]


def test_evaluator_checks_extraction_and_two_turn_chat_history():
    def handler(request):
        payload = json.loads(request.content)
        if request.url.path == "/after-sales/extract":
            return httpx.Response(
                200,
                json={
                    "order_id": "DEMO20260910001",
                    "request_type": "exchange",
                    "expected_solution": "换大一码",
                },
            )
        if not payload["history"]:
            answer = "收到订单 DEMO20260910005，请问需要什么帮助？"
        elif payload["history"] == [
            {"role": "user", "content": "我的订单号是 DEMO20260910005。"},
            {"role": "assistant", "content": "收到订单 DEMO20260910005，请问需要什么帮助？"},
        ]:
            answer = "您刚才提供的是 DEMO20260910005。"
        else:
            answer = "没有收到历史。"
        body = (
            f'event: token\ndata: {json.dumps({"text": answer}, ensure_ascii=False)}\n\n'
            'event: done\ndata: {"finish_reason":"stop"}\n\n'
        )
        return httpx.Response(200, content=body.encode())

    cases = {
        "extraction_cases": [
            {
                "id": "exchange",
                "description": "订单 DEMO20260910001 的衣服太小了，我想换大一码。",
                "expected": {
                    "order_id": "DEMO20260910001",
                    "request_type": "exchange",
                },
                "solution_contains": ["大一码"],
            }
        ],
        "chat_cases": [
            {
                "id": "recall",
                "turns": [
                    {"message": "我的订单号是 DEMO20260910005。", "expect": {}},
                    {
                        "message": "刚才的订单号是什么？",
                        "expect": {
                            "contains": ["DEMO20260910005"],
                            "not_contains": ["没有收到历史"],
                        },
                    },
                ],
            }
        ],
    }

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        report = evaluate_cases(cases, client)

    assert report["summary"] == {"total": 2, "passed": 2, "failed": 0}
    assert all(criterion["passed"] for result in report["results"] for criterion in result["criteria"])


def test_failed_expectation_has_nonzero_exit_code():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"order_id": None, "request_type": "unknown", "expected_solution": None},
        )
    )
    cases = {
        "extraction_cases": [
            {
                "id": "wrong_result",
                "description": "订单 DEMO20260910002，请退款。",
                "expected": {"request_type": "refund"},
            }
        ],
        "chat_cases": [],
    }

    with httpx.Client(transport=transport, base_url="http://test") as client:
        report = evaluate_cases(cases, client)

    assert report["summary"]["failed"] == 1
    assert report["results"][0]["criteria"] == [
        {"name": "request_type equals expected", "passed": False}
    ]
    assert exit_code_for_report(report) == 1


def test_cli_writes_failed_report_and_returns_nonzero(tmp_path, monkeypatch):
    cases_path = tmp_path / "cases.json"
    output_path = tmp_path / "results.json"
    cases_path.write_text(
        json.dumps(
            {
                "extraction_cases": [
                    {
                        "id": "refund",
                        "description": "订单 DEMO1，请退款。",
                        "expected": {"request_type": "refund"},
                    }
                ],
                "chat_cases": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                json={"detail": "模型暂时不可用"},
            )
        ),
        base_url="http://test",
    )
    monkeypatch.setattr(evaluator.httpx, "Client", lambda **kwargs: client)

    exit_code = evaluator.main(
        ["--base-url", "http://test", "--cases", str(cases_path), "--output", str(output_path)]
    )

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert saved["summary"] == {"total": 1, "passed": 0, "failed": 1}
    assert saved["results"][0]["error"].startswith("HTTPStatusError:")


def test_http_failure_is_not_retried():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"order_id": None, "request_type": "refund", "expected_solution": "退款"},
        )

    cases = {
        "extraction_cases": [
            {
                "id": "no_retry",
                "description": "请退款。",
                "expected": {"request_type": "refund"},
            }
        ],
        "chat_cases": [],
    }
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        report = evaluate_cases(cases, client)

    assert report["summary"]["failed"] == 1
    assert attempts == 1
