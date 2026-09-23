from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel


class TurnUnderstanding(BaseModel):
    product_candidate: str | None = None
    symptom_candidate: str | None = None
    feedback: Literal['resolved', 'unresolved'] | None = None
    ticket_consent: bool = False
    next_action: Literal['clarify', 'search', 'reply', 'handoff'] = 'clarify'


class S1ProUnderstanding:
    def __init__(self, model, method='json_mode'):
        self.extractor=model.with_structured_output(TurnUnderstanding,method=method)

    async def interpret(self, message, state):
        prompt=("只提取本轮用户明确表达的信息，不凭产品简称猜测型号。"
                "product_candidate 仅在用户明确说 eufy 扫地机器人 S1 Pro 时填 T2080；"
                "吸力变弱或不吸时 symptom_candidate 填吸力变弱；"
                "feedback 仅在明确说已恢复或仍未解决时填 resolved/unresolved；"
                "ticket_consent 仅在明确同意创建工单时为 true。"
                "next_action 只是建议，程序会再次校验权限。")
        result=await self.extractor.ainvoke([SystemMessage(prompt),HumanMessage(message)])
        return result.model_dump() if isinstance(result,TurnUnderstanding) else TurnUnderstanding.model_validate(result).model_dump()
