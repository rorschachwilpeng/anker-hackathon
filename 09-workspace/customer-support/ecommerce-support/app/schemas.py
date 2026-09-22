from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(max_length=16000)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be blank")
        return value


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: int | None = Field(default=None, gt=0, le=18446744073709551615)
    message: str = Field(max_length=16000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=40)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value

    @model_validator(mode="after")
    def require_complete_alternating_history(self) -> "ChatRequest":
        if self.history:
            raise ValueError("history 已迁移，请改用 conversation_id")
        if len(self.history) % 2:
            raise ValueError("history must contain complete user/assistant pairs")
        for index, item in enumerate(self.history):
            expected = "user" if index % 2 == 0 else "assistant"
            if item.role != expected:
                raise ValueError("history must alternate user and assistant")
        return self


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(max_length=16000)

    @field_validator("description")
    @classmethod
    def reject_blank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value


class AfterSales(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str | None
    request_type: Literal["refund", "return_refund", "exchange", "repair", "logistics", "other", "unknown"]
    expected_solution: str | None
