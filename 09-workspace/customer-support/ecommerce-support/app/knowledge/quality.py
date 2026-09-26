"""Judge whether this turn's actual evidence can answer the original question."""
import json

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from app.knowledge.types import KnowledgeError


REFUSAL = '目前的知识资料不足以确认这个问题，建议联系人工客服核实。'


class Useful(BaseModel):
    model_config = ConfigDict(extra='forbid')
    useful: StrictBool
    reason: str = Field(min_length=1,max_length=500)


class EvidenceGate:
    def __init__(self, model, method='json_mode'):
        self.model, self.method = model, method

    async def check(self, question, evidence):
        payload = {'question':question,'evidence':[{'n':e.n,'question':e.question,'answer':e.answer} for e in evidence]}
        try:
            output = await self.model.with_structured_output(Useful,method=self.method).ainvoke([
                SystemMessage(content='你是客服证据核验器。只依据提供的知识判断是否足够完整回答用户原话；空证据或只含部分事实必须 useful=false，不可借常识补全。只输出 JSON 的 useful 与 reason。'),
                HumanMessage(content=json.dumps(payload,ensure_ascii=False)),
            ])
            return output if isinstance(output,Useful) else Useful.model_validate(output)
        except Exception:
            raise KnowledgeError('self_check_unavailable',retryable=True) from None
