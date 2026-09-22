"""Single-question normalization; no history and no index-side duplication."""
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    canonical: str = Field(min_length=1,max_length=128)
    synonyms: list[str] = Field(default_factory=list,max_length=3)
    category: str | None = None


class QueryUnderstanding:
    def __init__(self, model, method='json_mode'):
        self.model = model
        self.method = method

    async def normalize(self, raw):
        original = QueryPlan(canonical=raw[:128],synonyms=[],category=None)
        try:
            result = await self.model.with_structured_output(QueryPlan,method=self.method).ainvoke([
                SystemMessage(content='只改写当前一条电商客服问题，不使用历史；给出标准问法、最多三个同义词和仅在明确提到时的品类。保留商品型号、数字和否定词。只输出符合 schema 的 JSON。'),
                HumanMessage(content=raw),
            ])
            plan = result if isinstance(result,QueryPlan) else QueryPlan.model_validate(result)
            tokens = re.findall(r'[A-Za-z][\w-]*\d[\w-]*|\d+(?:\.\d+)?',raw)
            if any(token.casefold() not in plan.canonical.casefold() for token in tokens):
                return original
            if any(word in raw and word not in plan.canonical for word in ('不','没','未','不能')):
                return original
            return plan
        except Exception:
            return original

    @staticmethod
    def lexical(raw, plan):
        return ' '.join(dict.fromkeys([raw,plan.canonical,*plan.synonyms]))
