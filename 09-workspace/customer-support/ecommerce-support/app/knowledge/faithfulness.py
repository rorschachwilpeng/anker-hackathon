"""Structured, evidence-only faithfulness judgment for offline evaluation."""
import json
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class Verdict(BaseModel):
    model_config=ConfigDict(extra='forbid')
    faithful: StrictBool
    reason: str = Field(min_length=1)
    claims: list[str]


class FaithJudge:
    def __init__(self,model,method='json_mode'):
        self.model,self.method=model,method

    async def score(self,query,answer,evidence):
        payload={'query':query,'answer':answer,'evidence':evidence}
        result=await self.model.with_structured_output(Verdict,method=self.method).ainvoke([
            SystemMessage(content='逐事实判断回答是否被给定证据支持；无法从证据推出的承诺、数字或事实即为不忠实。只输出 faithful、reason、claims；claims 列出无依据断言。拒答且无事实断言视为忠实。'),
            HumanMessage(content=json.dumps(payload,ensure_ascii=False)),
        ])
        verdict=result if isinstance(result,Verdict) else Verdict.model_validate(result)
        return verdict.model_dump()
