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
from app.schemas import AfterSales, ChatRequest, ExtractionRequest
from app.service import CustomerSupportService, FinishChunk, TokenChunk
from app.knowledge.embeddings import SiliconFlowEmbeddings
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.retrieval import KnowledgeSearch
from app.knowledge.state import StateStore
from app.knowledge.vectors import MilvusVectors
from app.s1pro.flow import S1ProFlow
from app.s1pro.understanding import S1ProUnderstanding
from app.s1pro.vision import VisionClient, VisionError, VisualObservation
from app.knowledge.quality import EvidenceGate
from app.knowledge.quality_repository import QualityRepository


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
            collection = 'knowledge'
            application.state.knowledge_repo = None
            application.state.knowledge_state = None
            try:
                resolved_settings.ensure_knowledge_ready(search=True)
                from pymilvus import AsyncMilvusClient
                milvus = AsyncMilvusClient(resolved_settings.milvus_uri)
                fingerprint = hashlib.sha256(
                    f'{resolved_settings.embedding_base_url.rstrip("/")}|{resolved_settings.embedding_model}|1024|text-v1'.encode()
                ).hexdigest()
                state = StateStore(database, namespace='knowledge')
                knowledge_repo = KnowledgeRepository(database.sessions, state=state, fingerprint=fingerprint)
                application.state.knowledge_repo = knowledge_repo
                application.state.knowledge_state = state
                from app.knowledge.hybrid_vectors import HybridVectors
                from app.knowledge.migration import active_collection
                collection = await active_collection(state)
                if collection == 'knowledge_hybrid_v1':
                    resolved_settings.ensure_hybrid_ready()
                vectors = HybridVectors(milvus, collection) if collection == 'knowledge_hybrid_v1' else MilvusVectors(milvus, collection)
                await vectors.ensure_collection()
                embeddings = SiliconFlowEmbeddings(client, resolved_settings)
                if collection == 'knowledge_hybrid_v1':
                    from app.knowledge.query import QueryUnderstanding
                    from app.knowledge.hybrid_retrieval import HybridKnowledgeSearch
                    from app.knowledge.reranker import SiliconFlowReranker
                    knowledge_search = HybridKnowledgeSearch(knowledge_repo,embeddings,vectors,
                        QueryUnderstanding(application.state.service.model,resolved_settings.structured_output_method),
                        SiliconFlowReranker(client,resolved_settings),resolved_settings.rerank_score_threshold)
                else:
                    knowledge_search = KnowledgeSearch(knowledge_repo, embeddings, vectors,
                        resolved_settings.knowledge_score_threshold, resolved_settings.knowledge_top_k)
            except Exception as exc:
                if milvus is not None:
                    await milvus.close()
                    milvus = None
                if collection == 'knowledge_hybrid_v1':
                    await database.close()
                    raise RuntimeError('active hybrid knowledge unavailable; startup stopped') from exc
            application.state.chat_service = chat_service or ChatService(
                resolved_settings, application.state.service.model, Repository(database.sessions), knowledge_search,
                quality_gate=(EvidenceGate(application.state.service.model,resolved_settings.structured_output_method)
                              if collection == 'knowledge_hybrid_v1' and knowledge_search is not None else None),
                quality_repo=(QualityRepository(database.sessions)
                              if collection == 'knowledge_hybrid_v1' and knowledge_search is not None else None))
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

    @application.get('/knowledge/chunks/{chunk_id}')
    async def source_chunk(chunk_id: int):
        repo = getattr(application.state,'knowledge_repo',None)
        if repo is None or chunk_id <= 0:
            raise HTTPException(404,'知识来源不存在')
        rows = await repo.load_done([chunk_id])
        row = rows.get(chunk_id)
        if row is None:
            raise HTTPException(404,'知识来源不存在')
        source_file = None
        state = getattr(application.state,'knowledge_state',None)
        if state is not None:
            root = (Path(__file__).resolve().parents[1]/'knowledge').resolve()
            for _,payload in await state.entries('source:'):
                if chunk_id in payload.get('ids',[]):
                    source = payload.get('source','')
                    if source.startswith('doc:'):
                        path = Path(source[4:]).resolve()
                        if not path.is_relative_to(root) or not path.is_file():
                            raise HTTPException(404,'知识来源不存在')
                        source_file = str(path.relative_to(root))
                    break
        return {'chunk_id':row.id,'section_path':row.draft.section_path,
                'question':row.draft.questions,'answer':row.draft.answer,'source_file':source_file}
    @application.post('/s1pro/chat')
    async def s1pro_chat(request: ChatRequest) -> dict[str, Any]:
        flow=application.state.s1pro_flow
        if flow is None:raise HTTPException(503,'S1 Pro service is unavailable')
        try:return await flow.handle(request.message,request.conversation_id)
        except LookupError:raise HTTPException(404,'会话不存在。') from None

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
