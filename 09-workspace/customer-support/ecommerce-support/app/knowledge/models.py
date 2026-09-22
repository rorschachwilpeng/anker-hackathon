from sqlalchemy import Column, ForeignKey, Index, MetaData, Table, Text, String, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, ENUM, JSON, TINYINT

metadata = MetaData()
options = dict(mysql_engine='InnoDB', mysql_charset='utf8mb4')

def ident():
    return Column('id', BIGINT(unsigned=True), primary_key=True, autoincrement=True)

def created():
    return Column('created_at', DATETIME(), nullable=False, server_default=text('CURRENT_TIMESTAMP'))

def updated():
    return Column('updated_at', DATETIME(), nullable=False, server_default=text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'))

knowledge_chunks = Table('knowledge_chunks', metadata, ident(),
    Column('category', String(255), nullable=False), Column('questions', Text, nullable=False),
    Column('answer', Text, nullable=False), Column('section_path', String(512)),
    Column('content_type', String(32)), Column('is_key_clause', TINYINT(display_width=1), nullable=False, server_default=text('0')),
    Column('prev_chunk_id', BIGINT(unsigned=True), ForeignKey('knowledge_chunks.id', name='fk_chunks_prev', ondelete='SET NULL')),
    Column('next_chunk_id', BIGINT(unsigned=True), ForeignKey('knowledge_chunks.id', name='fk_chunks_next', ondelete='SET NULL')),
    Column('vector_id', String(64)), Column('vectorize_status', ENUM('pending','done'), nullable=False, server_default='pending'),
    created(), updated(), Index('idx_category','category'), Index('idx_vectorize_status','vectorize_status'), **options)
qa_extraction_staging = Table('qa_extraction_staging', metadata, ident(),
    Column('batch_no', String(64), nullable=False), Column('source_ref', String(255)),
    Column('question', Text, nullable=False), Column('answer', Text, nullable=False),
    Column('status', ENUM('extracted','kept','discarded'), nullable=False, server_default='extracted'), created(),
    Index('idx_batch_no','batch_no'), Index('idx_status','status'), **options)
knowledge_ingestion_state = Table('knowledge_ingestion_state', metadata,
    Column('state_key', String(191), primary_key=True), Column('payload', JSON, nullable=False), created(), updated(), **options)
