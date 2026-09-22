import asyncio
import json
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from app.events import StreamEvent
from app.tools.errors import RetryableToolError, ToolError


def failure(code,message):
    return {'ok':False,'data':None,'error':{'code':code,'message':message}}

class ToolExecutor:
    def __init__(self,tools,timeout_seconds=5,max_attempts=2):
        self.tools={t.name:t for t in tools}
        self.timeout_seconds=timeout_seconds
        self.max_attempts=max_attempts

    async def execute(self,call):
        name=call['name']; ident=call['id']
        def event(status,attempt,result=None):
            data={'name':name,'tool_call_id':ident,'status':status,'attempt':attempt}
            if result is not None:data['result']=result
            return StreamEvent('tool_status',data)
        tool=self.tools.get(name)
        if tool is None:
            yield event('failed',0,failure('unknown_tool','工具不存在'))
            return
        try:
            args=tool.args_schema.model_validate(call['args']).model_dump()
        except (ValidationError,TypeError,ValueError):
            yield event('failed',0,failure('validation_error','工具参数不符合要求，请补充必要信息'))
            return
        loop=asyncio.get_running_loop();deadline=loop.time()+self.timeout_seconds
        for attempt in range(1,self.max_attempts+1):
            remaining=deadline-loop.time()
            if remaining<=0:
                yield event('failed',attempt-1,failure('timeout','工具暂时不可用，未能确认结果；请稍后查询'))
                return
            yield event('running',attempt)
            try:
                async with asyncio.timeout(remaining):
                    data=await tool.ainvoke(args)
                result={'ok':True,'data':data,'error':None}
                if len(json.dumps(result,ensure_ascii=False).encode())>2048:
                    result=failure('result_too_large','工具返回内容超出预算，请缩小查询范围')
                yield event('succeeded' if result['ok'] else 'failed',attempt,result)
                return
            except TimeoutError:
                result=failure('timeout','工具暂时不可用，未能确认结果；请稍后查询')
            except (RetryableToolError,OperationalError):
                remaining=deadline-loop.time()
                if attempt<self.max_attempts and remaining>.01:
                    yield event('retrying',attempt)
                    await asyncio.sleep(min(.05,remaining/2))
                    continue
                result=failure('unavailable','工具暂时不可用，未能确认结果；请稍后查询')
            except ToolError as exc:
                result=failure(exc.code,exc.message)
            except Exception:
                result=failure('execution_error','工具执行失败，未能确认结果')
            yield event('failed',attempt,result)
            return
