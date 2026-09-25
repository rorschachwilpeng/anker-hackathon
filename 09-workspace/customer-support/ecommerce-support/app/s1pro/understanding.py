from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel


class TurnUnderstanding(BaseModel):
    product_candidate: str | None = None
    symptom_candidate: str | None = None
    feedback: Literal['resolved', 'unresolved'] | None = None
    ticket_consent: bool | None = None
    urgency: Literal['urgent'] | None = None
    human_request: bool | None = None


class S1ProUnderstanding:
    def __init__(self, model, method='json_mode'):
        self.extractor=model.with_structured_output(TurnUnderstanding,method=method)

    async def interpret(self, message, state):
        prompt=("请按指定 JSON 格式，只提取本轮用户明确表达的信息，不凭产品简称猜测型号。"
                "product_candidate 仅在用户明确说 eufy 扫地机器人 S1 Pro 时填 T2080；"
                "吸力变弱或不吸时 symptom_candidate 填吸力变弱；"
                "feedback 仅在明确说已恢复或仍未解决时填 resolved/unresolved；"
                "ticket_consent 仅在明确同意创建工单时为 true；"
                "urgency 仅在用户明确表达急迫（如“急着用”“尽快”“马上要用”“很着急”）时填 urgent；"
                "human_request 仅在用户明确要求转人工或找真人客服时为 true。")
        result=await self.extractor.ainvoke([SystemMessage(prompt),HumanMessage(message)])
        parsed=result if isinstance(result,TurnUnderstanding) else TurnUnderstanding.model_validate(result)
        return {**parsed.model_dump(),'ticket_consent':parsed.ticket_consent is True}
