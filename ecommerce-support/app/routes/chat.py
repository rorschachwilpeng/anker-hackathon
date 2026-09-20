import asyncio
import json
import logging
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from sqlalchemy.exc import SQLAlchemyError
from app.chat_service import ChatError, ChatProtocolError
from app.context import ContextBudgetExceeded

log=logging.getLogger(__name__)

async def stateful_chat(service,request):
    try:
        turn=await service.prepare(request.message,request.conversation_id)
    except ChatError as exc:raise HTTPException(exc.status,exc.message) from None
    except ContextBudgetExceeded:raise HTTPException(413,'消息超过当前上下文预算。') from None
    except SQLAlchemyError:raise HTTPException(503,'数据库暂时不可用。') from None
    async def events():
        upstream=service.stream(turn)
        try:
            async for item in upstream:
                yield 'event: '+item.event+'\ndata: '+json.dumps(item.data,ensure_ascii=False,separators=(',',':'))+'\n\n'
        except asyncio.CancelledError:
            log.info('chat cancelled conversation=%s',turn.conversation_id)
            raise
        except Exception as exc:
            log.warning('chat failed conversation=%s kind=%s code=%s',turn.conversation_id,type(exc).__name__,exc.code if isinstance(exc,ChatProtocolError) else 'unclassified')
            yield 'event: error\ndata: {"code":"chat_error","message":"本轮回复未完成，请稍后重试；已提交的工单不会撤销。"}\n\n'
        finally:
            await upstream.aclose()
            turn.close()
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'},background=BackgroundTask(turn.close))
