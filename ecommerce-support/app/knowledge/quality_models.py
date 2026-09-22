from sqlalchemy import Column, ForeignKey, Index, MetaData, Table, Text, String, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, ENUM, JSON, INTEGER

metadata = MetaData()
options = dict(mysql_engine='InnoDB', mysql_charset='utf8mb4')

low_confidence_questions = Table('low_confidence_questions', metadata,
    Column('id', BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment='主键'),
    Column('conversation_id', BIGINT(unsigned=True), ForeignKey('conversations.id', name='fk_lcq_conversation'), comment='来源会话'),
    Column('raw_question', Text, nullable=False, comment='用户原话,带情绪口语'),
    Column('source', ENUM('retrieval_low_conf','self_check','user_feedback'), nullable=False, comment='入池入口:检索证据低 / 生成自评不足 / 用户反馈未解决'),
    Column('reason', Text, comment='判不能的原因,留作复盘'),
    Column('created_at', DATETIME(), nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='入池时间'),
    Index('idx_source','source'), Index('idx_created_at','created_at'), comment='低置信度问题池', **options)

faith_cases = Table('faith_cases', metadata,
    Column('id', BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment='主键'),
    Column('eval_id', String(16), nullable=False, comment='评估集题号,如 A43;一题一行'),
    Column('bucket', String(24), nullable=False, comment='题目所属桶:A_policy / B_model / C_colloquial / E_multi'),
    Column('query', String(512), nullable=False, comment='用户问题原文'),
    Column('strategy', String(24), nullable=False, server_default='hybrid_rerank', comment='产出这条答案的检索策略'),
    Column('answer', Text, nullable=False, comment='被判编造的那版生成答案原文'),
    Column('reason', Text, nullable=False, comment='裁判给的理由:编在哪一句'),
    Column('citations', JSON, comment='这一轮喂给模型的 Top-K 证据全集快照:[{n,chunk_id,section_path,question,answer}];答案里的角标 [n] 就是这份列表的序号,答案通常只引用其中两三条;老数据没记为 NULL'),
    Column('judge_model', String(64), comment='判这条的裁判模型'),
    Column('status', ENUM('未解决','已解决','无需解决'), nullable=False, server_default='未解决', comment='处置状态,人工点按钮改'),
    Column('seen_count', INTEGER(unsigned=True), nullable=False, server_default=text('1'), comment='被判编造的累计次数(跨轮)'),
    Column('first_seen_at', DATETIME(), nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='第一次被判编造的时间'),
    Column('last_seen_at', DATETIME(), nullable=False, server_default=text('CURRENT_TIMESTAMP'), comment='最近一次被判编造的时间'),
    Column('resolution', String(300), comment='处置说明:标已解决要写清怎么解决的,标无需解决要写清为什么不用改;退回未解决时清空。空着的处置在台账上等于没有交代'),
    Column('resolved_at', DATETIME(), comment='最近一次被标为已解决/无需解决的时间;复发后仍保留,用来标「复发」'),
    Index('uk_eval_id','eval_id',unique=True), Index('idx_status','status'),
    Index('idx_last_seen_at','last_seen_at'), comment='ch04 忠实度编造个案台账', **options)
