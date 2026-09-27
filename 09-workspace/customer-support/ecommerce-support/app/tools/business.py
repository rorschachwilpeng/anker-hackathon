import random
from dataclasses import dataclass
from typing import Literal
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field
from app.knowledge.retrieval import pack_faq_result
from app.knowledge.types import KnowledgeError
from app.tools.errors import RetryableToolError, ToolError

@dataclass(frozen=True)
class ToolContext:
    conversation_id: int
    ticket_no: str

class StrictArgs(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class OrderArgs(StrictArgs):
    order_id: str=Field(min_length=1,max_length=64)
class ProductArgs(StrictArgs):
    product_id: str=Field(min_length=1,max_length=64)
class FaqArgs(StrictArgs):
    keyword: str=Field(min_length=1,max_length=64,description='保留用户原始表达的完整查询主题，用于语义知识检索')
    scope: Literal['general','current_order_product']=Field(default='general',description='商家政策使用 general；已查询订单商品的使用知识使用 current_order_product')
class TicketArgs(StrictArgs):
    description: str=Field(min_length=1,max_length=2000)
    ticket_type: Literal['售后','投诉','咨询']
class S1ProKnowledgeArgs(StrictArgs):
    query: str=Field(min_length=1,max_length=256)
    confirmed_model: Literal['T2080']

def build_s1pro_tools(knowledge_search):
    @tool(args_schema=S1ProKnowledgeArgs)
    async def search_support_knowledge(query: str, confirmed_model: str):
        """仅检索已确认 T2080 型号的 eufy 官方售后知识及来源。"""
        if knowledge_search is None:
            raise ToolError('knowledge_not_configured','知识库尚未配置')
        try:
            rows=await knowledge_search.find_for_model(query,confirmed_model)
            return pack_faq_result(query,rows)
        except KnowledgeError as exc:
            if exc.retryable:raise RetryableToolError('知识检索服务暂时不可用') from exc
            raise ToolError(exc.code,'知识检索失败，暂时无法确认排障依据') from exc
    return [search_support_knowledge]

def build_tools(repo,context: ToolContext,knowledge_search=None):
    mock_cache={}
    def mock_product(ident):
        key=('product',ident)
        if key not in mock_cache:
            data={'source':'mock','notice':'随机生成的演示数据，不代表真实业务信息'}
            data.update(product_id=ident,name='演示商品 '+ident,price=round(random.uniform(10,200),2),stock=random.randint(0,99))
            mock_cache[key]=data
        return mock_cache[key]
    @tool(args_schema=OrderArgs)
    async def query_order(order_id: str):
        """查询固定演示订单，一次返回订单、商品、支付、配送及最新物流；未知订单明确未找到。"""
        return await repo.query_order(context.conversation_id,order_id)
    @tool(args_schema=ProductArgs)
    async def query_product(product_id: str):
        """查询商品编号对应的价格和库存；返回模拟数据。"""
        return mock_product(product_id)
    @tool(args_schema=OrderArgs)
    async def query_logistics(order_id: str):
        """查询同一固定演示订单的订单和物流快照；订单物流问题优先调用 query_order。"""
        return await repo.query_order(context.conversation_id,order_id)
    @tool(args_schema=FaqArgs)
    async def query_faq(keyword: str,scope: str='general'):
        """语义检索商家政策；已查询订单商品的使用问题设置 scope=current_order_product，型号由当前会话订单确定。"""
        if knowledge_search is None:
            raise ToolError('knowledge_not_configured','知识库尚未配置，暂时无法查询商家政策')
        try:
            if scope=='current_order_product':
                current=await repo.get_current_order_product_model(context.conversation_id)
                if not current['found']:
                    return {'keyword':keyword,'found':False,'items':[],'reason':current['reason']}
                compact=''.join(keyword.split())
                if current['model_code']=='T2080' and any(term in compact for term in ('第一次怎么用','首次使用','第一次使用')):
                    rows=await knowledge_search.find_first_use_for_model(keyword,current['model_code'])
                else:
                    rows=await knowledge_search.find_for_model(keyword,current['model_code'])
            else:
                rows=await knowledge_search.find(keyword)
            return pack_faq_result(keyword,rows)
        except KnowledgeError as exc:
            if exc.retryable:
                raise RetryableToolError('知识检索服务暂时不可用') from exc
            code=exc.code if exc.code=='knowledge_budget_exceeded' else 'knowledge_unavailable'
            raise ToolError(code,'知识检索失败，暂时无法确认商家政策') from exc
    @tool(args_schema=TicketArgs)
    async def create_ticket(description: str,ticket_type: str):
        """用户明确要求人工协助、提交工单或投诉时创建人工工单。"""
        result=await repo.create_ticket_once(context.ticket_no,context.conversation_id,description,ticket_type)
        return {k:v for k,v in result.items() if k != 'description'}
    return [query_order,query_product,query_logistics,query_faq,create_ticket]
