import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.s1pro.vision import VisionError, VisualObservation


PNG = b"\x89PNG\r\n\x1a\nsynthetic-png"
JPEG = b"\xff\xd8\xffsynthetic-jpeg"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class Flow:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def handle(
        self,
        message: str,
        conversation_id: int | None = None,
        *,
        observation: VisualObservation | None = None,
        image_failed: bool = False,
        events=None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "message": message,
                "conversation_id": conversation_id,
                "observation": observation,
                "image_failed": image_failed,
            }
        )
        return {
            "conversation_id": conversation_id or 7,
            "action": "clarify_product",
            "reply": "请确认产品",
            "source": None,
            "ticket_no": None,
        }


class Vision:
    def __init__(self, observation: VisualObservation | Exception | None = None) -> None:
        self.observation = observation or VisualObservation(
            candidate_model="T2080",
            visible_text=["T2080"],
            visible_clues=["synthetic label"],
            uncertainties=[],
        )
        self.calls: list[tuple[bytes, str]] = []

    async def analyze(self, image_bytes: bytes, mime_type: str) -> VisualObservation:
        self.calls.append((image_bytes, mime_type))
        if isinstance(self.observation, Exception):
            raise self.observation
        assert self.observation is not None
        return self.observation


def make_client(flow: Flow, vision: Vision | None = None) -> TestClient:
    return TestClient(
        create_app(service=object(), s1pro_flow=flow, vision_client=vision)
    )


def test_accepts_png_and_returns_existing_s1pro_response_shape() -> None:
    flow = Flow()
    vision = Vision()

    with make_client(flow, vision) as client:
        response = client.post(
            "/s1pro/chat/image",
            data={"message": "吸力变弱", "conversation_id": "7"},
            files={"image": ("label.jpg", PNG, "image/png")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": 7,
        "action": "clarify_product",
        "reply": "请确认产品",
        "source": None,
        "ticket_no": None,
    }
    assert vision.calls == [(PNG, "image/png")]
    assert flow.calls[0]["message"] == "吸力变弱"
    assert flow.calls[0]["conversation_id"] == 7
    assert flow.calls[0]["observation"] == vision.observation
    assert flow.calls[0]["image_failed"] is False


def test_accepts_jpeg_with_empty_text() -> None:
    flow = Flow()
    vision = Vision()

    with make_client(flow, vision) as client:
        response = client.post(
            "/s1pro/chat/image",
            data={"message": ""},
            files={"image": ("label.jpeg", JPEG, "image/jpeg")},
        )

    assert response.status_code == 200
    assert vision.calls == [(JPEG, "image/jpeg")]
    assert flow.calls[0]["message"] == ""


def test_rejects_forged_or_mismatched_image_before_services() -> None:
    flow = Flow()
    vision = Vision()

    with make_client(flow, vision) as client:
        forged = client.post(
            "/s1pro/chat/image",
            data={"message": "看下图片"},
            files={"image": ("label.png", b"not-a-png", "image/png")},
        )
        mismatch = client.post(
            "/s1pro/chat/image",
            data={"message": "看下图片"},
            files={"image": ("label.jpg", PNG, "image/jpeg")},
        )

    assert forged.status_code == 415
    assert mismatch.status_code == 415
    assert vision.calls == []
    assert flow.calls == []


def test_rejects_oversized_image_before_services() -> None:
    flow = Flow()
    vision = Vision()
    png_magic = b"\x89PNG\r\n\x1a\n"
    oversized = png_magic + b"x" * (MAX_IMAGE_BYTES + 1 - len(png_magic))

    with make_client(flow, vision) as client:
        response = client.post(
            "/s1pro/chat/image",
            data={"message": "看下图片"},
            files={"image": ("large.png", oversized, "image/png")},
        )

    assert response.status_code == 413
    assert vision.calls == []
    assert flow.calls == []


def test_vision_failure_keeps_text_and_degrades_to_flow() -> None:
    flow = Flow()
    vision = Vision(VisionError("vision service request failed"))

    with make_client(flow, vision) as client:
        response = client.post(
            "/s1pro/chat/image",
            data={"message": "我的 S1 Pro 吸力变弱了"},
            files={"image": ("label.png", PNG, "image/png")},
        )

    assert response.status_code == 200
    assert flow.calls[0]["message"] == "我的 S1 Pro 吸力变弱了"
    assert flow.calls[0]["observation"] is None
    assert flow.calls[0]["image_failed"] is True


def test_missing_vision_client_degrades_to_flow() -> None:
    flow = Flow()

    with make_client(flow) as client:
        response = client.post(
            "/s1pro/chat/image",
            data={"message": "我的 S1 Pro 吸力变弱了"},
            files={"image": ("label.png", PNG, "image/png")},
        )

    assert response.status_code == 200
    assert flow.calls[0]["image_failed"] is True


def test_s1pro_chat_streams_result_event_with_response_fields() -> None:
    flow = Flow()

    with make_client(flow) as client:
        response = client.post("/s1pro/chat", json={"message": "S1 Pro 不吸了"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    result = None
    for block in response.text.split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) >= 2 and lines[0] == "event: result":
            result = json.loads(lines[1][5:].strip())
    assert result is not None
    assert {
        "conversation_id",
        "action",
        "reply",
        "source",
        "ticket_no",
    } <= set(result)
    assert len(flow.calls) == 1
