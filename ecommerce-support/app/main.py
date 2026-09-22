import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.database import Database
from app.repositories import Repository
from app.chat_service import ChatService
from app.routes.chat import stateful_chat
from app.config import Settings
from app.context import ContextBudgetExceeded
from app.schemas import AfterSales, ChatRequest, ExtractionRequest
from app.service import CustomerSupportService, FinishChunk, TokenChunk
from app.knowledge.embeddings import SiliconFlowEmbeddings
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.retrieval import KnowledgeSearch
from app.knowledge.state import StateStore
from app.knowledge.vectors import MilvusVectors


UPSTREAM_ERROR = {"code": "upstream_error", "message": "模型服务暂时不可用，请稍后重试。"}


def _sse(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def create_app(
    settings: Settings | None = None,
    service: CustomerSupportService | None = None,
    chat_service: ChatService | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.chat_service = chat_service
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
            if resolved_settings.database_url is None:
                raise RuntimeError('DATABASE_URL is required; initialize MySQL first')
            database = Database(resolved_settings.database_url.get_secret_value())
            milvus = None
            knowledge_search = None
            try:
                resolved_settings.ensure_knowledge_ready(search=True)
                from pymilvus import AsyncMilvusClient
                milvus = AsyncMilvusClient(resolved_settings.milvus_uri)
                fingerprint = hashlib.sha256(
                    f'{resolved_settings.embedding_base_url.rstrip("/")}|{resolved_settings.embedding_model}|1024|text-v1'.encode()
                ).hexdigest()
                state = StateStore(database, namespace='knowledge')
                knowledge_repo = KnowledgeRepository(database.sessions, state=state, fingerprint=fingerprint)
                from app.knowledge.hybrid_vectors import HybridVectors
                from app.knowledge.migration import active_collection
                collection = await active_collection(state)
                vectors = HybridVectors(milvus, collection) if collection == 'knowledge_hybrid_v1' else MilvusVectors(milvus, collection)
                await vectors.ensure_collection()
                knowledge_search = KnowledgeSearch(
                    knowledge_repo,
                    SiliconFlowEmbeddings(client, resolved_settings),
                    vectors,
                    resolved_settings.knowledge_score_threshold,
                    resolved_settings.knowledge_top_k,
                )
            except Exception:
                if milvus is not None:
                    await milvus.close()
                    milvus = None
            application.state.chat_service = chat_service or ChatService(
                resolved_settings, application.state.service.model, Repository(database.sessions), knowledge_search)
            try:
                yield
            finally:
                if milvus is not None:
                    await milvus.close()
                await database.close()

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
        if application.state.chat_service is not None:
            return await stateful_chat(application.state.chat_service, request)
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
