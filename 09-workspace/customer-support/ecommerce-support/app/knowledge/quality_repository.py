from sqlalchemy import insert, select, update, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from app.knowledge.quality_models import low_confidence_questions as low, faith_cases
from app.knowledge.models import knowledge_ingestion_state as state


class QualityRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def record_low(self, message_id, conversation_id, raw_question, reason):
        if not isinstance(message_id, int) or message_id <= 0:
            raise ValueError('message id required')
        key = f'knowledge:quality:low:{message_id}'
        async with self.sessions.begin() as session:
            stmt = mysql_insert(state).values(state_key=key, payload={'schema_version':1})
            await session.execute(stmt.on_duplicate_key_update(state_key=stmt.inserted.state_key))
            row = (await session.execute(select(state.c.payload).where(state.c.state_key==key).with_for_update())).scalar_one()
            if row.get('low_id'):
                return row['low_id']
            result = await session.execute(insert(low).values(
                conversation_id=conversation_id, raw_question=raw_question, source='self_check', reason=reason))
            ident = result.inserted_primary_key[0]
            await session.execute(update(state).where(state.c.state_key==key).values(payload={'schema_version':1,'low_id':ident}))
            return ident

    async def record_faith_case(self, case):
        fields = {'eval_id','bucket','query','strategy','answer','reason','citations','judge_model'}
        if not fields.issubset(case):
            raise ValueError('incomplete faith case')
        async with self.sessions.begin() as session:
            stmt = mysql_insert(faith_cases).values(**{k:case[k] for k in fields})
            result=await session.execute(stmt.on_duplicate_key_update(
                bucket=stmt.inserted.bucket, query=stmt.inserted.query,
                strategy=stmt.inserted.strategy, answer=stmt.inserted.answer,
                reason=stmt.inserted.reason, citations=stmt.inserted.citations,
                judge_model=stmt.inserted.judge_model, status='未解决', resolution=None,
                seen_count=faith_cases.c.seen_count+1,
                last_seen_at=func.current_timestamp()))
            return 'new' if result.rowcount == 1 else 'recurrent'

    async def resolve(self, eval_id, status, resolution):
        if status not in ('已解决','无需解决') or not resolution.strip() or len(resolution)>300:
            raise ValueError('resolution required')
        async with self.sessions.begin() as session:
            result = await session.execute(update(faith_cases).where(faith_cases.c.eval_id==eval_id).values(
                status=status, resolution=resolution, resolved_at=func.current_timestamp()))
            if result.rowcount != 1:
                raise ValueError('unknown eval id')
