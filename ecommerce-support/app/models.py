from sqlalchemy import Column, ForeignKey, Index, MetaData, Table, Text, String, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, ENUM, JSON

metadata = MetaData()

def ident():
    return Column('id', BIGINT(unsigned=True), primary_key=True, autoincrement=True)

def created():
    return Column('created_at', DATETIME(), nullable=False, server_default=text('CURRENT_TIMESTAMP'))

def updated():
    return Column('updated_at', DATETIME(), nullable=False, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))

options = dict(mysql_engine='InnoDB', mysql_charset='utf8mb4')
conversations = Table('conversations', metadata, ident(),
    Column('user_id', String(64), nullable=False),
    Column('status', ENUM('进行中','已转人工','已结束'), nullable=False, server_default='进行中'),
    created(), updated(), Index('idx_user_id','user_id'), **options)
messages = Table('messages', metadata, ident(),
    Column('conversation_id', BIGINT(unsigned=True), ForeignKey('conversations.id',name='fk_messages_conversation'),nullable=False),
    Column('role',ENUM('user','assistant','tool'),nullable=False),
    Column('content',Text),Column('tool_calls',JSON),Column('tool_call_id',String(64)),created(),
    Index('idx_conversation_id','conversation_id'),**options)
faq = Table('faq',metadata,ident(),Column('question',String(512),nullable=False),
    Column('answer',Text,nullable=False),Column('category',String(64),nullable=False),created(),updated(),Index('idx_category','category'),**options)
tickets = Table('tickets',metadata,
    Column('ticket_no',String(32),primary_key=True),
    Column('conversation_id',BIGINT(unsigned=True),ForeignKey('conversations.id',name='fk_tickets_conversation'),nullable=False),
    Column('description',Text,nullable=False),Column('ticket_type',ENUM('售后','投诉','咨询'),nullable=False),
    Column('status',ENUM('待处理','已处理'),nullable=False,server_default='待处理'),created(),Index('idx_conversation_id','conversation_id'),**options)
