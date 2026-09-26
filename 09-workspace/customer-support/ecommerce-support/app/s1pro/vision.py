import base64
import json
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import Settings


class VisualObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_model: Literal["T2080", "other", "unknown"]
    visible_text: list[str]
    visible_clues: list[str]
    uncertainties: list[str]


class VisionError(Exception):
    pass


class VisionClient:
    def __init__(
        self,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.http_client = http_client

    async def analyze(self, image_bytes: bytes, mime_type: str) -> VisualObservation:
        key = self.settings.vision_api_key
        if not key or not key.get_secret_value().strip():
            raise VisionError("vision service is not configured")
        if not image_bytes:
            raise VisionError("image is empty")

        image_url = (
            f"data:{mime_type};base64,"
            f"{base64.b64encode(image_bytes).decode('ascii')}"
        )
        payload = {
            "model": self.settings.vision_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                    "Inspect only what is visibly present in the image. Do not infer an internal fault "
                    "or treat a product candidate as confirmed. Use T2080 only when visible text identifies "
                    "model T2080 or S1 Pro as a robot vacuum. Use other when the visible text identifies a "
                    "different product category or model; use unknown when the model/category is unreadable. "
                    "Return json matching this example: "
                        '{"candidate_model":"unknown","visible_text":[],"visible_clues":[],"uncertainties":[]}. '
                        'candidate_model must be "T2080", "other", or "unknown".'
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "List visible text, visible product clues, and uncertainties.",
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 512,
            "thinking": {"type": "disabled"},
            "stream": False,
        }
        url = f"{self.settings.vision_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {key.get_secret_value()}"}

        try:
            if self.http_client is None:
                async with httpx.AsyncClient(
                    timeout=self.settings.vision_timeout_seconds
                ) as client:
                    response = await client.post(url, headers=headers, json=payload)
            else:
                response = await self.http_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.settings.vision_timeout_seconds,
                )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty response content")
            observation = json.loads(content)
            return VisualObservation.model_validate(observation)
        except (httpx.TimeoutException, httpx.HTTPError):
            raise VisionError("vision service request failed") from None
        except (KeyError, IndexError, TypeError, ValueError, ValidationError):
            raise VisionError("vision service returned an invalid observation") from None
