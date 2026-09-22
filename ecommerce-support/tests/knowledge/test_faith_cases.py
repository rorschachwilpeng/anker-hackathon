import pytest


async def test_judge_malformed_response_is_error():
    from app.knowledge.faithfulness import FaithJudge
    class Model:
        def with_structured_output(self,*args,**kwargs):return self
        async def ainvoke(self,messages):return {'faithful':'yes','reason':'好','claims':[]}
    with pytest.raises(ValueError):
        await FaithJudge(Model()).score('问题','答案',[])
