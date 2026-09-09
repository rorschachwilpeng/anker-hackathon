import json

import httpx
import pytest
from langchain_core.exceptions import OutputParserException
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from app.config import Settings
from app.schemas import AfterSales
from app.service import CustomerSupportService, FinishChunk, TokenChunk


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openai_base_url": "https://provider.test/v1",
        "openai_model": "test-model",
        "openai_api_key": "test-key",
        "context_window_tokens": 8192,
        "max_output_tokens": 64,
        "context_margin_tokens": 32,
        "request_timeout": 5.0,
    }
    values.update(overrides)
    return Settings(**values)


def test_settings_reject_invalid_budget_timeout_and_structured_method() -> None:
    with pytest.raises(ValidationError):
        settings(context_window_tokens=96)
    with pytest.raises(ValidationError):
        settings(request_timeout=float("inf"))
    with pytest.raises(ValidationError):
        settings(structured_output_method="function_calling")


def make_model(client: httpx.AsyncClient) -> ChatOpenAI:
    config = settings()
    return ChatOpenAI(
        base_url=str(config.openai_base_url),
        model=config.openai_model,
        api_key=config.openai_api_key.get_secret_value(),
        max_tokens=config.max_output_tokens,
        timeout=config.request_timeout,
        max_retries=0,
        stream_usage=False,
        http_async_client=client,
    )


@pytest.mark.asyncio
async def test_chat_uses_real_langchain_serialization_and_finish_metadata() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        body = (
            'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,'
            '"model":"test-model","choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}\n\n'
            'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1,'
            '"model":"test-model","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = CustomerSupportService(settings(), model=make_model(client))
        events = [event async for event in service.stream("问题", [])]

    assert events == [TokenChunk("你"), TokenChunk("好"), FinishChunk("stop")]
    assert captured["stream"] is True
    assert captured["stream_options"] is None if "stream_options" in captured else True
    assert captured["max_completion_tokens"] == 64
    assert [message["role"] for message in captured["messages"]] == ["system", "user"]


@pytest.mark.asyncio
async def test_extraction_uses_json_mode_and_pydantic_parsing_over_http_boundary() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        response = {
            "id": "chatcmpl-2",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "order_id": "TEST123",
                                "request_type": "exchange",
                                "expected_solution": "换大一码",
                            }
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = CustomerSupportService(settings(), model=make_model(client))
        result = await service.extract("订单 TEST123 的衣服小了，想换大一码")

    assert result == AfterSales(order_id="TEST123", request_type="exchange", expected_solution="换大一码")
    assert captured["response_format"] == {"type": "json_object"}
    assert "order_id" in captured["messages"][0]["content"]


@pytest.mark.asyncio
async def test_structured_output_rejects_missing_or_extra_fields() -> None:
    responses = [
        {"order_id": None, "request_type": "unknown"},
        {"order_id": None, "request_type": "unknown", "expected_solution": None, "extra": "no"},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = responses.pop(0)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-3",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(payload)},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = CustomerSupportService(settings(), model=make_model(client))
        with pytest.raises(OutputParserException):
            await service.extract("没有明确诉求")
        with pytest.raises(OutputParserException):
            await service.extract("没有明确诉求")
