from sqlalchemy import Column, ForeignKey, Index, Integer, MetaData, Numeric, Table, Text, String, text
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
    Column('task_state', JSON),
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

demo_orders = Table('demo_orders',metadata,
    Column('order_no',String(64),primary_key=True),
    Column('placed_at',DATETIME(),nullable=False),
    Column('purchase_channel',String(64),nullable=False),
    Column('payment_status',String(32),nullable=False),
    Column('paid_amount',Numeric(10,2),nullable=False),
    Column('currency',String(3),nullable=False),
    Column('shipping_status',String(32),nullable=False),
    Column('estimated_delivery_at',DATETIME(),nullable=True),
    Column('source',String(16),nullable=False,server_default='demo'),
    created(),updated(),**options)
demo_order_items = Table('demo_order_items',metadata,ident(),
    Column('order_no',String(64),ForeignKey('demo_orders.order_no',name='fk_demo_order_items_order'),nullable=False),
    Column('product_name',String(128),nullable=False),
    Column('product_line',String(64),nullable=False),
    Column('category',String(64),nullable=False),
    Column('model_code',String(32),nullable=False),
    Column('quantity',Integer(),nullable=False),
    created(),Index('idx_demo_order_items_order','order_no'),**options)
demo_order_events = Table('demo_order_events',metadata,ident(),
    Column('order_no',String(64),ForeignKey('demo_orders.order_no',name='fk_demo_order_events_order'),nullable=False),
    Column('occurred_at',DATETIME(),nullable=False),
    Column('location',String(128),nullable=False),
    Column('description',String(255),nullable=False),
    created(),Index('uq_demo_order_event','order_no','occurred_at',unique=True),**options)
conversation_order_context = Table('conversation_order_context',metadata,
    Column('conversation_id',BIGINT(unsigned=True),ForeignKey('conversations.id',name='fk_order_context_conversation'),primary_key=True),
    Column('order_no',String(64),ForeignKey('demo_orders.order_no',name='fk_order_context_order'),nullable=False),
    updated(),**options)
