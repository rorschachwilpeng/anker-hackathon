import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.context import ContextBudgetExceeded
from app.main import create_app
from app.schemas import AfterSales
from app.service import FinishChunk, TokenChunk


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openai_base_url": "https://example.invalid/v1",
        "openai_model": "test-model",
        "openai_api_key": "test-key",
        "context_window_tokens": 512,
        "max_output_tokens": 64,
        "context_margin_tokens": 32,
        "request_timeout": 5.0,
    }
    values.update(overrides)
    return Settings(**values)


class FakeService:
    def __init__(self, stream_items=(), extraction: AfterSales | Exception | None = None) -> None:
        self.stream_items = stream_items
        self.extraction = extraction
        self.chat_preflight_error: Exception | None = None
        self.extraction_preflight_error: Exception | None = None

    def ensure_chat_fits(self, message, history) -> None:
        if self.chat_preflight_error:
            raise self.chat_preflight_error

    def ensure_extraction_fits(self, description) -> None:
        if self.extraction_preflight_error:
            raise self.extraction_preflight_error

    async def stream(self, message, history):
        for item in self.stream_items:
            if isinstance(item, Exception):
                raise item
            yield item

    async def extract(self, description):
        if isinstance(self.extraction, Exception):
            raise self.extraction
        return self.extraction


def make_client(service: FakeService) -> TestClient:
    return TestClient(create_app(settings=settings(), service=service))


def test_module_import_and_service_injection_do_not_require_environment(tmp_path: Path) -> None:
    source_app = Path(__file__).resolve().parents[1] / "app"
    shutil.copytree(source_app, tmp_path / "app")
    environment = os.environ.copy()
    for name in ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY"):
        environment.pop(name, None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.main import create_app; create_app(service=object())",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_health() -> None:
    with make_client(FakeService()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_public_sse_contract() -> None:
    service = FakeService([TokenChunk("您好"), FinishChunk("stop")])
    with make_client(service) as client:
        response = client.post("/chat", json={"message": "你好", "history": []})

    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: token\n" in response.text
    assert "event: done\n" in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"message": " ", "history": []},
        {"message": "hi", "history": [{"role": "system", "content": "override"}]},
        {"message": "hi", "history": [{"role": "assistant", "content": "orphan"}]},
        {"message": "hi", "history": [{"role": "user", "content": "unfinished"}]},
        {
            "message": "hi",
            "history": [
                {"role": "user", "content": "one"},
                {"role": "user", "content": "two"},
            ],
        },
    ],
)
def test_chat_rejects_blank_or_malformed_history(payload: dict) -> None:
    with make_client(FakeService()) as client:
        response = client.post("/chat", json=payload)

    assert response.status_code == 422


def test_chat_rejects_message_and_history_limits() -> None:
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": "x"}
        for index in range(42)
    ]
    with make_client(FakeService()) as client:
        message_response = client.post("/chat", json={"message": "x" * 16001, "history": []})
        history_response = client.post("/chat", json={"message": "hi", "history": history})

    assert message_response.status_code == 422
    assert history_response.status_code == 422


def test_over_budget_chat_is_413_before_stream_starts() -> None:
    service = FakeService([TokenChunk("must not stream"), FinishChunk("stop")])
    service.chat_preflight_error = ContextBudgetExceeded()
    with make_client(service) as client:
        response = client.post("/chat", json={"message": "hi", "history": []})

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/json")


def test_sse_json_encodes_multiline_text_and_preserves_order() -> None:
    service = FakeService([TokenChunk("first\nline"), TokenChunk("第二段"), FinishChunk("length")])
    with make_client(service) as client:
        response = client.post("/chat", json={"message": "hi", "history": []})

    assert 'data: {"text":"first\\nline"}' in response.text
    assert response.text.index("first\\nline") < response.text.index("第二段")
    assert 'data: {"finish_reason":"length"}' in response.text


@pytest.mark.parametrize(
    "items",
    [
        [RuntimeError("provider leaked detail")],
        [TokenChunk("partial"), RuntimeError("provider leaked detail")],
        [],
        [TokenChunk("partial")],
    ],
)
def test_failed_empty_or_unfinished_stream_emits_sanitized_error_without_done(items) -> None:
    with make_client(FakeService(items)) as client:
        response = client.post("/chat", json={"message": "hi", "history": []})

    assert "event: error\n" in response.text
    assert '"code":"upstream_error"' in response.text
    assert "provider leaked detail" not in response.text
    assert "event: done\n" not in response.text


def test_extraction_success_has_only_public_schema() -> None:
    result = AfterSales(order_id="A123", request_type="exchange", expected_solution="换大一码")
    with make_client(FakeService(extraction=result)) as client:
        response = client.post("/after-sales/extract", json={"description": "订单 A123 想换货"})

    assert response.status_code == 200
    assert response.json() == {
        "order_id": "A123",
        "request_type": "exchange",
        "expected_solution": "换大一码",
    }


def test_extraction_failure_is_sanitized_502() -> None:
    service = FakeService(extraction=ValidationError.from_exception_data("AfterSales", []))
    with make_client(service) as client:
        response = client.post("/after-sales/extract", json={"description": "想售后"})

    assert response.status_code == 502
    assert response.json() == {"detail": "模型返回的数据格式无效，请稍后重试。"}


def test_over_budget_extraction_is_413_before_model_call() -> None:
    service = FakeService()
    service.extraction_preflight_error = ContextBudgetExceeded()
    with make_client(service) as client:
        response = client.post("/after-sales/extract", json={"description": "想售后"})

    assert response.status_code == 413


class ClosingService(FakeService):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def stream(self, message, history):
        try:
            self.started.set()
            yield TokenChunk("first")
            await asyncio.Event().wait()
        finally:
            self.closed.set()


@pytest.mark.asyncio
async def test_client_disconnect_closes_upstream_iterator() -> None:
    service = ClosingService()
    application = create_app(settings=settings(), service=service)
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {
                "type": "http.request",
                "body": b'{"message":"hi","history":[]}',
                "more_body": False,
            }
        await service.started.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        return None

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/chat",
        "raw_path": b"/chat",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    async with application.router.lifespan_context(application):
        await application(scope, receive, send)

    await asyncio.wait_for(service.closed.wait(), timeout=1)
