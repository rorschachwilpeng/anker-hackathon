from sqlalchemy import select, insert, update
from sqlalchemy.exc import IntegrityError
from app.models import conversations, messages, faq, tickets


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
