import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.database import Database
from app.repositories import Repository
from app.chat_service import ChatService
from app.routes.chat import stateful_chat
from app.config import Settings
from app.context import ContextBudgetExceeded
from app.schemas import AfterSales, ChatRequest, ExtractionRequest, TicketStatusUpdate
from app.service import CustomerSupportService, FinishChunk, TokenChunk
from app.knowledge.embeddings import SiliconFlowEmbeddings
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.retrieval import KnowledgeSearch
from app.knowledge.state import StateStore
from app.knowledge.vectors import MilvusVectors
from app.s1pro.flow import S1ProFlow
from app.s1pro.understanding import S1ProUnderstanding
from app.s1pro.vision import VisionClient, VisionError, VisualObservation


UPSTREAM_ERROR = {"code": "upstream_error", "message": "模型服务暂时不可用，请稍后重试。"}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _sse(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n"


def create_app(
    settings: Settings | None = None,
    service: CustomerSupportService | None = None,
    chat_service: ChatService | None = None,
    s1pro_flow: S1ProFlow | None = None,
    vision_client: VisionClient | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.chat_service = chat_service
        application.state.s1pro_flow = s1pro_flow
        application.state.vision_client = vision_client
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
                vectors = MilvusVectors(milvus, 'knowledge')
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
            application.state.s1pro_flow = s1pro_flow or S1ProFlow(
                Repository(database.sessions),
                S1ProUnderstanding(application.state.service.model,resolved_settings.structured_output_method),
                knowledge_search,
            )
            application.state.vision_client = vision_client or VisionClient(
                resolved_settings, http_client=client
            )
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

    def order_repository():
        chat=application.state.chat_service
        repo=getattr(chat,'repo',None)
        if repo is None:raise HTTPException(503,'订单服务暂不可用。')
        return repo

    @application.get('/api/orders/{order_no}')
    async def order_detail(order_no: str) -> dict[str, Any]:
        result=await order_repository().get_order_detail(order_no)
        if result is None:raise HTTPException(404,'演示订单不存在。')
        return result

    @application.get('/orders/{order_no}',include_in_schema=False)
    async def order_detail_page(order_no: str) -> FileResponse:
        result=await order_repository().get_order_detail(order_no)
        if result is None:raise HTTPException(404,'演示订单不存在。')
        return FileResponse(static_dir/'order.html',headers={'Cache-Control':'no-cache'})

    @application.post('/s1pro/chat')
    async def s1pro_chat(request: ChatRequest) -> StreamingResponse:
        flow=application.state.s1pro_flow
        if flow is None:raise HTTPException(503,'S1 Pro service is unavailable')
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(payload: dict[str, Any]) -> None:
            await queue.put(payload)

        async def run() -> None:
            try:
                result = await flow.handle(request.message, request.conversation_id, events=emit)
                await queue.put({'type': 'result', 'data': result})
            except LookupError:
                await queue.put({'type': 'error', 'message': '会话不存在。', 'code': 'conversation_not_found'})
            except Exception:
                await queue.put({'type': 'error', **UPSTREAM_ERROR})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())

        async def events():
            try:
                while True:
                    payload = await queue.get()
                    if payload is None:
                        return
                    kind = payload.get('type')
                    if kind == 'result':
                        yield _sse('result', payload['data'])
                    elif kind == 'stage':
                        yield _sse('stage', payload)
                    elif kind == 'search_done':
                        yield _sse('search_done', payload)
                    else:
                        yield _sse('error', payload)
            finally:
                if not task.done():
                    task.cancel()

        return StreamingResponse(
            events(),
            media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache'},
        )

    @application.post('/s1pro/chat/image')
    async def s1pro_chat_image(
        message: str = Form(default=""),
        conversation_id: int | None = Form(default=None),
        image: UploadFile = File(...),
    ) -> dict[str, Any]:
        flow = application.state.s1pro_flow
        if flow is None:
            raise HTTPException(503, 'S1 Pro service is unavailable')

        image_bytes = await image.read(MAX_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(413, '图片不能超过 5 MB。')

        mime_type = image.content_type or ''
        valid_png = mime_type == 'image/png' and image_bytes.startswith(PNG_MAGIC)
        valid_jpeg = mime_type == 'image/jpeg' and image_bytes.startswith(JPEG_MAGIC)
        if not (valid_png or valid_jpeg):
            raise HTTPException(415, '仅支持格式与文件内容一致的 JPEG 或 PNG 图片。')

        observation: VisualObservation | None = None
        vision = application.state.vision_client
        image_failed = vision is None
        if vision is not None:
            try:
                observation = await vision.analyze(image_bytes, mime_type)
            except VisionError:
                image_failed = True

        try:
            return await flow.handle(
                message,
                conversation_id,
                observation=observation,
                image_failed=image_failed,
            )
        except LookupError:
            raise HTTPException(404, '会话不存在。') from None

    @application.get('/s1pro/tickets')
    async def s1pro_tickets() -> list[dict[str, Any]]:
        flow=application.state.s1pro_flow
        if flow is None:raise HTTPException(503,'S1 Pro service is unavailable')
        return await flow.repo.list_s1pro_tickets()

    @application.get('/s1pro/tickets/{ticket_no}')
    async def s1pro_ticket(ticket_no: str) -> dict[str, Any]:
        flow=application.state.s1pro_flow
        if flow is None:raise HTTPException(503,'S1 Pro service is unavailable')
        result=await flow.repo.get_s1pro_ticket(ticket_no)
        if result is None:raise HTTPException(404,'工单不存在。')
        return result

    @application.post('/s1pro/tickets/{ticket_no}/status')
    async def s1pro_ticket_status(ticket_no: str, payload: TicketStatusUpdate) -> dict[str, Any]:
        flow=application.state.s1pro_flow
        if flow is None:raise HTTPException(503,'S1 Pro service is unavailable')
        result=await flow.repo.update_s1pro_ticket_status(ticket_no,payload.status)
        if result is None:raise HTTPException(404,'工单不存在。')
        return result

    @application.get('/s1pro/workbench',include_in_schema=False)
    async def s1pro_workbench() -> FileResponse:
        return FileResponse(static_dir/'s1pro-workbench.html',headers={'Cache-Control':'no-cache'})

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
