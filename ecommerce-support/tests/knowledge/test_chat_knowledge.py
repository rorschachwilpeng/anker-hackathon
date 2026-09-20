import json

from langchain_core.messages import AIMessageChunk

from tests_support import FakeModel, MemoryRepository


async def test_query_faq_uses_knowledge_service_and_never_sql_like():
    from app.tools.business import ToolContext, build_tools

    class Search:
        async def find(self, keyword):
            return [{"id": 7, "question": "运费说明", "answer": "满99元包邮。", "category": "配送"}]

    class Repo:
        async def find_faq(self, keyword):
            raise AssertionError("SQL LIKE must not run")

    tool = next(tool for tool in build_tools(Repo(), ToolContext(1, "Ttest"), Search()) if tool.name == "query_faq")
    data = await tool.ainvoke({"keyword": "邮费是多少"})
    assert data["found"] is True
    assert data["items"][0]["id"] == 7


async def test_missing_knowledge_service_is_recognizable_tool_error():
    from app.tools.business import ToolContext, build_tools
    from app.tools.executor import ToolExecutor

    tool = next(tool for tool in build_tools(object(), ToolContext(1, "Ttest")) if tool.name == "query_faq")
    events = [event async for event in ToolExecutor([tool]).execute({
        "id": "call-1", "name": "query_faq", "args": {"keyword": "邮费"}
    })]
    assert events[-1].data["result"]["error"]["code"] == "knowledge_not_configured"


async def test_chat_query_faq_streams_status_persists_tool_result_and_converges_once():
    from app.chat_service import ChatService
    from app.config import Settings

    class Search:
        async def find(self, keyword):
            return [{"id": 7, "question": "运费说明", "answer": "满99元包邮。", "category": "配送"}]

    first = [AIMessageChunk(
        content="", tool_call_chunks=[{"name": "query_faq", "args": '{"keyword":"邮费是多少"}', "id": "faq-1", "index": 0}],
        response_metadata={"finish_reason": "tool_calls"},
    )]
    final = [AIMessageChunk(content="订单满99元包邮。", response_metadata={"finish_reason": "stop"})]
    repo = MemoryRepository()
    model = FakeModel([first, final])
    settings = Settings(openai_base_url="http://example.invalid", openai_model="fake", openai_api_key="fake")
    service = ChatService(settings, model, repo, knowledge_search=Search())

    events = [event async for event in service.stream(await service.prepare("邮费是多少", None))]

    statuses = [event.data["status"] for event in events if event.event == "tool_status"]
    assert statuses == ["running", "succeeded"]
    tool_row = next(row for row in repo.rows if row["role"] == "tool")
    assert json.loads(tool_row["content"])["data"]["items"][0]["answer"] == "满99元包邮。"
    assert [event.event for event in events].count("done") == 1
    assert len(model.requests) == 2

