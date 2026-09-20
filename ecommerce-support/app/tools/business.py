import random
from dataclasses import dataclass
from typing import Literal
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

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
    keyword: str=Field(min_length=1,max_length=64,description='用户原话中的主题关键词，去掉问句成分；禁止同义词替换，如邮费必须保留邮费，不得改成运费')
class TicketArgs(StrictArgs):
    description: str=Field(min_length=1,max_length=2000)
    ticket_type: Literal['售后','投诉','咨询']

def build_tools(repo,context: ToolContext):
    mock_cache={}
    def mock(kind,ident):
        key=(kind,ident)
        if key not in mock_cache:
            data={'source':'mock','notice':'随机生成的演示数据，不代表真实业务信息'}
            if kind=='order':data.update(order_id=ident,status=random.choice(['已支付','已发货','已完成']),amount=round(random.uniform(50,300),2))
            if kind=='product':data.update(product_id=ident,name='演示商品 '+ident,price=round(random.uniform(10,200),2),stock=random.randint(0,99))
            if kind=='logistics':data.update(order_id=ident,status=random.choice(['运输中','派送中','已签收']),location=random.choice(['上海转运中心','杭州配送站','北京分拨中心']))
            mock_cache[key]=data
        return mock_cache[key]
    @tool(args_schema=OrderArgs)
    async def query_order(order_id: str):
        """查询指定订单的状态和金额；返回模拟数据。物流位置请用 query_logistics。"""
        return mock('order',order_id)
    @tool(args_schema=ProductArgs)
    async def query_product(product_id: str):
        """查询商品编号对应的价格和库存；返回模拟数据。"""
        return mock('product',product_id)
    @tool(args_schema=OrderArgs)
    async def query_logistics(order_id: str):
        """根据订单号直接查询物流到哪里、配送状态；无需先查订单，返回模拟数据。"""
        return mock('logistics',order_id)
    @tool(args_schema=FaqArgs)
    async def query_faq(keyword: str):
        """按用户原词查询商家政策和常见问题。只支持字面匹配，禁止同义改写。"""
        rows=await repo.find_faq(keyword)
        return {'keyword':keyword,'found':bool(rows),'items':rows}
    @tool(args_schema=TicketArgs)
    async def create_ticket(description: str,ticket_type: str):
        """用户明确要求人工协助、提交工单或投诉时创建人工工单。"""
        result=await repo.create_ticket_once(context.ticket_no,context.conversation_id,description,ticket_type)
        return {k:v for k,v in result.items() if k != 'description'}
    return [query_order,query_product,query_logistics,query_faq,create_ticket]
