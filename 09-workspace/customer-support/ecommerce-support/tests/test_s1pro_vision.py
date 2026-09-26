import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openai_base_url": "https://example.invalid/v1",
        "openai_model": "text-test-model",
        "openai_api_key": "text-test-key",
        "vision_api_key": "vision-test-key",
    }
    values.update(overrides)
    return Settings(**values)


def response(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


async def test_analyze_sends_image_and_parses_visual_observation() -> None:
    from app.s1pro.vision import VisionClient

    expected = {
        "candidate_model": "T2080",
        "visible_text": ["T2080"],
        "visible_clues": ["robot vacuum label"],
        "uncertainties": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer vision-test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-flash"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["max_tokens"] == 512
        assert payload["thinking"] == {"type": "disabled"}
        assert "json" in payload["messages"][0]["content"].lower()
        image_part = payload["messages"][1]["content"][1]
        assert image_part["type"] == "image_url"
        assert image_part["image_url"]["url"] == "data:image/jpeg;base64,/9g="
        return httpx.Response(200, json=response(json.dumps(expected)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await VisionClient(make_settings(), client).analyze(b"\xff\xd8", "image/jpeg")

    assert observation.model_dump() == expected


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "not-json"}}]},
        {"choices": [{"message": {"content": '{"candidate_model":"guessed-T2080"}'}}]},
    ],
)
async def test_invalid_response_is_sanitized_as_vision_error(body: dict[str, object]) -> None:
    from app.s1pro.vision import VisionClient, VisionError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    ) as client:
        with pytest.raises(VisionError):
            await VisionClient(make_settings(), client).analyze(b"image", "image/jpeg")


async def test_timeout_is_sanitized_as_vision_error() -> None:
    from app.s1pro.vision import VisionClient, VisionError

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("vision-test-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VisionError) as error:
            await VisionClient(make_settings(), client).analyze(b"image", "image/png")

    assert "vision-test-key" not in str(error.value)


async def test_http_error_is_sanitized_as_vision_error() -> None:
    from app.s1pro.vision import VisionClient, VisionError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, text="vision-test-key")
        )
    ) as client:
        with pytest.raises(VisionError) as error:
            await VisionClient(make_settings(), client).analyze(b"image", "image/png")

    assert "vision-test-key" not in str(error.value)


async def test_other_candidate_is_valid_and_never_promoted_to_t2080() -> None:
    from app.s1pro.vision import VisionClient

    result = {
        "candidate_model": "other",
        "visible_text": ["S1 Pro"],
        "visible_clues": ["non-robot appliance"],
        "uncertainties": [],
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response(json.dumps(result)))
        )
    ) as client:
        observation = await VisionClient(make_settings(), client).analyze(b"image", "image/png")

    assert observation.candidate_model == "other"
    assert observation.candidate_model != "T2080"


def test_observation_rejects_unexpected_candidate() -> None:
    from app.s1pro.vision import VisualObservation

    with pytest.raises(ValidationError):
        VisualObservation(
            candidate_model="guessed-T2080",
            visible_text=[],
            visible_clues=[],
            uncertainties=[],
        )


def test_vision_settings_are_optional_and_leave_text_config_unchanged() -> None:
    settings = make_settings(vision_api_key=None)

    assert settings.vision_base_url == "https://api.deepseek.com"
    assert settings.vision_model == "deepseek-flash"
    assert settings.vision_api_key is None
    assert settings.openai_base_url == "https://example.invalid/v1"
    assert settings.openai_model == "text-test-model"
