import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.context import ContextBudgetExceeded
from app.schemas import AfterSales, ChatRequest, ExtractionRequest
from app.service import CustomerSupportService, FinishChunk, TokenChunk


UPSTREAM_ERROR = {"code": "upstream_error", "message": "模型服务暂时不可用，请稍后重试。"}


def _sse(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def create_app(
    settings: Settings | None = None,
    service: CustomerSupportService | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if service is not None:
            application.state.service = service
            yield
            return

        resolved_settings = settings or Settings()
        async with httpx.AsyncClient() as client:
            application.state.service = CustomerSupportService(
                resolved_settings,
                http_async_client=client,
            )
            yield

    application = FastAPI(lifespan=lifespan)
    static_dir = Path(__file__).resolve().parent / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")

    @application.get("/", include_in_schema=False)
    @application.get("/chat", include_in_schema=False)
    async def chat_page() -> FileResponse:
        return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-cache"})

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        chat_service = application.state.service
        try:
            chat_service.ensure_chat_fits(request.message, request.history)
        except ContextBudgetExceeded as exc:
            raise HTTPException(status_code=413, detail="消息超过当前上下文预算。") from exc

        async def events():
            upstream = chat_service.stream(request.message, request.history)
            emitted_text = False
            try:
                async for item in upstream:
                    if isinstance(item, TokenChunk):
                        if item.text:
                            emitted_text = True
                            yield _sse("token", {"text": item.text})
                            await anyio.sleep(0)
                    elif isinstance(item, FinishChunk):
                        if not emitted_text:
                            raise RuntimeError("empty stream")
                        yield _sse("done", {"finish_reason": item.finish_reason})
                        return
                raise RuntimeError("unfinished stream")
            except asyncio.CancelledError:
                raise
            except Exception:
                yield _sse("error", UPSTREAM_ERROR)
            finally:
                close = getattr(upstream, "aclose", None)
                if close is not None:
                    await close()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @application.post("/after-sales/extract", response_model=AfterSales)
    async def extract(request: ExtractionRequest) -> AfterSales:
        extraction_service = application.state.service
        try:
            extraction_service.ensure_extraction_fits(request.description)
        except ContextBudgetExceeded as exc:
            raise HTTPException(status_code=413, detail="描述超过当前上下文预算。") from exc
        try:
            return await extraction_service.extract(request.description)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="模型返回的数据格式无效，请稍后重试。",
            ) from exc

    return application


app = create_app()
