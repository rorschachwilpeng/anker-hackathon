from dataclasses import dataclass

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.context import trim_context
from app.prompts import render_chat_system_prompt, render_extraction_system_prompt
from app.schemas import AfterSales, HistoryMessage


@dataclass(frozen=True)
class TokenChunk:
    text: str


@dataclass(frozen=True)
class FinishChunk:
    finish_reason: str


class CustomerSupportService:
    def __init__(
        self,
        settings: Settings,
        model: ChatOpenAI | None = None,
        http_async_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.model = model or ChatOpenAI(
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            api_key=settings.openai_api_key.get_secret_value(),
            max_tokens=settings.max_output_tokens,
            timeout=settings.request_timeout,
            max_retries=0,
            stream_usage=False,
            http_async_client=http_async_client,
        )
        self.extractor = self.model.with_structured_output(
            AfterSales,
            method=settings.structured_output_method,
        )
        self._chat_system = render_chat_system_prompt()
        self._extraction_system = render_extraction_system_prompt()

    @staticmethod
    def _history_messages(history: list[HistoryMessage]) -> list[BaseMessage]:
        return [
            HumanMessage(item.content) if item.role == "user" else AIMessage(item.content)
            for item in history
        ]

    def _chat_messages(self, message: str, history: list[HistoryMessage]) -> list[BaseMessage]:
        return trim_context(
            self._chat_system,
            self._history_messages(history),
            message,
            self.settings.input_budget,
        )

    def _extraction_messages(self, description: str) -> list[BaseMessage]:
        return trim_context(
            self._extraction_system,
            [],
            description,
            self.settings.input_budget,
        )

    def ensure_chat_fits(self, message: str, history: list[HistoryMessage]) -> None:
        self._chat_messages(message, history)

    def ensure_extraction_fits(self, description: str) -> None:
        self._extraction_messages(description)

    async def stream(self, message: str, history: list[HistoryMessage]):
        messages = self._chat_messages(message, history)
        finish_reason: str | None = None
        emitted_text = False

        async for chunk in self.model.astream(messages):
            text = chunk.text
            if text:
                emitted_text = True
                yield TokenChunk(text)
            candidate = chunk.response_metadata.get("finish_reason")
            if candidate:
                finish_reason = str(candidate)

        if not emitted_text or finish_reason is None:
            raise RuntimeError("upstream stream ended without content or finish metadata")
        yield FinishChunk(finish_reason)

    async def extract(self, description: str) -> AfterSales:
        messages = self._extraction_messages(description)
        result = await self.extractor.ainvoke(messages)
        return result if isinstance(result, AfterSales) else AfterSales.model_validate(result)
