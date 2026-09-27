from dataclasses import asdict
import uuid
from sqlalchemy import delete, select, insert, update
from sqlalchemy.exc import IntegrityError
from app.models import (conversations, messages, faq, tickets, demo_orders,
                        demo_order_items, demo_order_events, conversation_order_context)


def _order_datetime(value):
    return value.strftime('%Y-%m-%d %H:%M') if value else None


class Repository:
    def __init__(self, sessions):
        self.sessions = sessions

    async def new_conversation(self, user_id):
        async with self.sessions.begin() as session:
            result = await session.execute(insert(conversations).values(user_id=user_id))
            return result.inserted_primary_key[0]

    async def get_conversation(self, conversation_id):
        async with self.sessions() as session:
            row = (await session.execute(select(conversations).where(conversations.c.id==conversation_id))).mappings().first()
            return dict(row) if row else None

    async def load_messages(self, conversation_id):
        async with self.sessions() as session:
            # Bound the read; a partial oldest turn is discarded by context assembly.
            rows = (await session.execute(select(messages).where(messages.c.conversation_id==conversation_id).order_by(messages.c.id.desc()).limit(200))).mappings().all()
            return [dict(row) for row in reversed(rows)]

    async def append_message(self, conversation_id, role, content=None, tool_calls=None, tool_call_id=None):
        async with self.sessions.begin() as session:
            result = await session.execute(insert(messages).values(conversation_id=conversation_id,role=role,content=content,tool_calls=tool_calls,tool_call_id=tool_call_id))
            return result.inserted_primary_key[0]

    async def find_faq(self, keyword):
        pattern = '%' + keyword.replace('!','!!').replace('%','!%').replace('_','!_') + '%'
        async with self.sessions() as session:
            rows = (await session.execute(select(faq.c.id,faq.c.question,faq.c.answer,faq.c.category).where(faq.c.question.like(pattern,escape='!')).order_by(faq.c.id).limit(5))).mappings().all()
            return [dict(row) for row in rows]

    async def _load_order(self, session, order_no):
        order=(await session.execute(select(demo_orders).where(demo_orders.c.order_no==order_no))).mappings().first()
        if order is None:return None
        items=(await session.execute(select(demo_order_items).where(
            demo_order_items.c.order_no==order_no).order_by(demo_order_items.c.id))).mappings().all()
        event_rows=(await session.execute(select(demo_order_events).where(
            demo_order_events.c.order_no==order_no).order_by(demo_order_events.c.occurred_at))).mappings().all()
        events=[{'occurred_at':_order_datetime(row['occurred_at']),'location':row['location'],
                 'description':row['description']} for row in event_rows]
        return {'found':True,'source':order['source'],'order':{
                    'order_no':order['order_no'],'placed_at':_order_datetime(order['placed_at']),
                    'purchase_channel':order['purchase_channel'],'payment_status':order['payment_status'],
                    'paid_amount':f"{order['paid_amount']:.2f}",'currency':order['currency'],
                    'shipping_status':order['shipping_status'],
                    'estimated_delivery_at':_order_datetime(order['estimated_delivery_at']),
                    'source':order['source']},
                'items':[{'product_name':row['product_name'],'product_line':row['product_line'],
                          'category':row['category'],'model_code':row['model_code'],
                          'quantity':row['quantity']} for row in items],
                'events':events,'latest_event':events[-1] if events else None}

    async def query_order(self, conversation_id, order_no):
        try:
            async with self.sessions.begin() as session:
                data=await self._load_order(session,order_no)
                await session.execute(delete(conversation_order_context).where(
                    conversation_order_context.c.conversation_id==conversation_id))
                if data is None:return {'found':False,'order_no':order_no}
                await session.execute(insert(conversation_order_context).values(
                    conversation_id=conversation_id,order_no=order_no))
                return {'found':True,'source':data['source'],'order':data['order'],
                        'items':data['items'],'latest_event':data['latest_event']}
        except Exception:
            try:
                async with self.sessions.begin() as session:
                    await session.execute(delete(conversation_order_context).where(
                        conversation_order_context.c.conversation_id==conversation_id))
            except Exception:
                pass
            raise

    async def get_order_detail(self, order_no):
        async with self.sessions() as session:
            return await self._load_order(session,order_no)

    async def get_current_order_detail(self, conversation_id):
        async with self.sessions() as session:
            order_no=(await session.execute(select(conversation_order_context.c.order_no).where(
                conversation_order_context.c.conversation_id==conversation_id))).scalar_one_or_none()
            return await self._load_order(session,order_no) if order_no else None

    async def get_current_order_product_model(self, conversation_id):
        async with self.sessions() as session:
            order_no=(await session.execute(select(conversation_order_context.c.order_no).where(
                conversation_order_context.c.conversation_id==conversation_id))).scalar_one_or_none()
            if order_no is None:return {'found':False,'reason':'no_current_order'}
            rows=(await session.execute(select(demo_order_items.c.model_code).where(
                demo_order_items.c.order_no==order_no).order_by(demo_order_items.c.id))).scalars().all()
            if not rows:return {'found':False,'reason':'no_order_product'}
            models=set(rows)
            if len(rows)!=1 or len(models)!=1:return {'found':False,'reason':'ambiguous_order_products'}
            return {'found':True,'model_code':rows[0]}

    async def create_ticket_once(self, ticket_no, conversation_id, description, ticket_type):
        values=dict(ticket_no=ticket_no,conversation_id=conversation_id,description=description,ticket_type=ticket_type)
        try:
            async with self.sessions.begin() as session:
                await session.execute(insert(tickets).values(**values))
                await session.execute(update(conversations).where(conversations.c.id==conversation_id).values(status='已转人工'))
        except IntegrityError:
            async with self.sessions() as session:
                row = (await session.execute(select(tickets).where(tickets.c.ticket_no==ticket_no))).mappings().first()
                if not row or any(row[k]!=v for k,v in values.items()):
                    raise ValueError('ticket conflict') from None
        return {**values,'status':'待处理'}

    async def load_s1pro_state(self, conversation_id):
        from app.s1pro.state import S1ProState
        async with self.sessions() as session:
            value=(await session.execute(select(conversations.c.task_state).where(conversations.c.id==conversation_id))).scalar_one()
        return S1ProState.from_dict(value)

    async def save_s1pro_state(self, conversation_id, state):
        async with self.sessions.begin() as session:
            await session.execute(update(conversations).where(conversations.c.id==conversation_id).values(task_state=asdict(state)))

    async def reserve_s1pro_ticket(self, conversation_id):
        from app.s1pro.state import S1ProState
        async with self.sessions.begin() as session:
            value=(await session.execute(select(conversations.c.task_state).where(conversations.c.id==conversation_id).with_for_update())).scalar_one()
            state=S1ProState.from_dict(value)
            if state.ticket_no is None:
                state.ticket_no='T'+uuid.uuid4().hex[:30]
                await session.execute(update(conversations).where(conversations.c.id==conversation_id).values(task_state=asdict(state)))
            return state.ticket_no

    async def list_s1pro_tickets(self):
        from app.s1pro.state import S1ProState
        async with self.sessions() as session:
            rows=(await session.execute(select(tickets,conversations.c.task_state).join(
                conversations,tickets.c.conversation_id==conversations.c.id
            ).order_by(tickets.c.created_at.desc()).limit(50))).mappings().all()
        result=[]
        for row in rows:
            state=S1ProState.from_dict(row['task_state'])
            if state.confirmed_model=='T2080':
                result.append({'ticket_no':row['ticket_no'],'product_model':state.confirmed_model,
                               'symptom':state.symptom,'status':row['status']})
        return result

    async def get_s1pro_ticket(self, number):
        from app.s1pro.state import S1ProState
        async with self.sessions() as session:
            row=(await session.execute(select(tickets,conversations.c.task_state).join(
                conversations,tickets.c.conversation_id==conversations.c.id
            ).where(tickets.c.ticket_no==number))).mappings().first()
        if row is None:return None
        state=S1ProState.from_dict(row['task_state'])
        if state.confirmed_model!='T2080':return None
        return {'ticket_no':row['ticket_no'],'conversation_id':row['conversation_id'],
                'product_model':state.confirmed_model,'symptom':state.symptom,
                'suggested_steps':state.suggested_steps,'completed_steps':state.completed_steps,
                'feedback':state.feedback,'sources':state.sources,
                'description':row['description'],'status':row['status']}

    async def update_s1pro_ticket_status(self, number, status):
        from app.s1pro.state import S1ProState
        async with self.sessions.begin() as session:
            row=(await session.execute(select(tickets.c.ticket_no,conversations.c.task_state).join(
                conversations,tickets.c.conversation_id==conversations.c.id
            ).where(tickets.c.ticket_no==number))).mappings().first()
            if row is None:return None
            state=S1ProState.from_dict(row['task_state'])
            if state.confirmed_model!='T2080':return None
            await session.execute(update(tickets).where(tickets.c.ticket_no==number).values(status=status))
        return {'ticket_no':number,'status':status}
