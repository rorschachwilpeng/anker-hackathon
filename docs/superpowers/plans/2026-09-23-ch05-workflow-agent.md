# ch05 Workflow 骨架与主力 ReAct Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 LangGraph 确定性工作流承载七类客服分流，并在知识闸门或业务路由之后运行可流式、可停止、可恢复的多步 ReAct Agent。

**Architecture:** FastAPI 继续作为 SSE 传输层；外层 StateGraph 固定执行闲聊短路、指代透传、意图识别、代码分流、知识检索/闸门、Agent 或固定回复、持久化和日志。主力 Agent 是显式的模型/工具循环子图，PostgreSQL AsyncPostgresSaver 保存图状态，MySQL继续保存业务记录；工单只由前端建议按钮触发。

**Tech Stack:** Python 3.12+、FastAPI、LangChain、LangGraph、langgraph-checkpoint-postgres、PostgreSQL 17、SQLAlchemy/MySQL、Milvus、原生 HTML/CSS/JavaScript。

**Spec:** `docs/superpowers/specs/2026-09-23-ch05-workflow-agent-design.md`

## Global Constraints

- 固定使用 LangGraph StateGraph + State + 官方 PostgreSQL checkpointer，不改用非官方 MySQL saver。
- 实施前已用 Context7 查 StateGraph、条件边、ToolNode、custom stream、thread_id、AsyncPostgresSaver；Context7 目录只列到 1.0.8，而 2026-09-23 官方 PyPI 当前稳定版是 `langgraph==1.2.12`、`langgraph-checkpoint-postgres==3.1.2`。Task 1 必须用本地探针验证两者接口兼容，失败就停止并询问用户，不自行降级。
- PostgreSQL只保存 checkpoint；MySQL继续作为 conversations/messages/tickets/knowledge/low-confidence 的权威源。
- 主力 Agent 不得拥有 `create_ticket`；建工单只允许经用户点击受控动作接口执行。
- `create_ticket_once` 不再修改 conversations.status；转人工仅做前端模拟。
- 明确闲聊代码规则命中后不得发出任何模型请求；带业务内容的问候不得被短路。
- 商品咨询、退款退货必须经过知识检索和置信度闸；物流、订单、售后不经过知识闸。
- Agent 最多四次工具执行，禁止并行工具调用，受 token 预算、重复调用检测和 recursion_limit 共同约束。
- `query_faq(keyword: str)` 的公开入参出参契约保持不变；知识证据分数只走内部接口。
- 前端按 Vibe Coding 例外实施；后端、图、脚本和数据处理继续走 TDD、评审与验证。
- 每完成一个任务立刻向 `dev-notes/ch05.md` 追加四项记录并提交，不在收尾补写。
- 不运行 ch04 真实混合集合迁移；ch05 同时兼容当前 dense 和以后激活的 hybrid。

## File Map

- Create `ecommerce-support/app/workflow/state.py`: 可 checkpoint 的 State、意图/路由/动作类型和 reducer。
- Create `ecommerce-support/app/workflow/routing.py`: 明确闲聊规则、七类到四出口映射、工具白名单。
- Create `ecommerce-support/app/workflow/intent.py`: 结构化意图模型与一次重试。
- Create `ecommerce-support/app/workflow/knowledge.py`: 统一 dense/hybrid 证据与确定性置信度闸。
- Create `ecommerce-support/app/workflow/raw_agent.py`: 不依赖 LangGraph 的最裸 Agent 循环。
- Create `ecommerce-support/app/workflow/agent.py`: 显式 ReAct Agent 子图及停止策略。
- Create `ecommerce-support/app/workflow/graph.py`: 外层 StateGraph 节点和条件边。
- Create `ecommerce-support/app/workflow/service.py`: prepare/stream、checkpoint config、MySQL持久化和事件翻译。
- Create `ecommerce-support/app/workflow/actions.py`: checkpoint 动作读取、过期/归属校验和幂等建单。
- Create `ecommerce-support/scripts/demo_raw_agent_loop.py`: 裸循环命令入口。
- Create `ecommerce-support/scripts/evaluate_ch05_intent.py`: 七类意图评估入口。
- Create `ecommerce-support/evals/ch05/intent.jsonl`: 意图标注集。
- Modify `ecommerce-support/app/main.py`: PostgreSQL checkpointer 生命周期、图装配、健康状态和动作接口。
- Modify `ecommerce-support/app/config.py`: orchestrator、PostgreSQL、Agent预算和动作 TTL。
- Modify `ecommerce-support/app/repositories.py`: 工单与转人工解耦。
- Modify `ecommerce-support/app/knowledge/retrieval.py`: dense 内部带分数证据接口。
- Modify `ecommerce-support/app/knowledge/hybrid_retrieval.py`: 与统一证据适配器对齐。
- Modify `ecommerce-support/app/knowledge/quality_repository.py`: retrieval_low_conf 幂等入池。
- Modify `ecommerce-support/app/routes/chat.py`: WorkflowService 通用错误和取消处理。
- Modify `ecommerce-support/app/static/chat.js`: workflow 状态和独立动作按钮。
- Modify `ecommerce-support/app/static/chat.css`: 动作按钮及状态样式。
- Modify `ecommerce-support/compose.yaml`, `pyproject.toml`, `.env.example`: 固定依赖与配置。
- Create/modify `ecommerce-support/tests/workflow/*.py`, `tests/test_api.py`, `tests/test_repository.py`: 分层回归和真实 PostgreSQL 集成。

## Review Focus

- 输入“你好，我的订单一直没到”必须继续进入意图模型和订单/物流流程，不能被明确闲聊规则吞掉；Task 2 加边界测试。
- 同一个 `action_id` 因双击、超时重试或并发请求重复到达时只能创建一张工单并返回同一 ticket_no；Task 8 加并发/重放测试。
- 旧 checkpoint 中残留上一轮证据、动作和停止原因时，新一轮初始化必须清空本轮字段但保留消息历史；Task 6 加跨轮状态测试。
- 模型一次返回多个工具、重复相同工具参数或 usage_metadata 缺失时必须受控停止且不突破预算；Task 5 加三类停止测试。
- PostgreSQL在启动后中断、checkpoint 迁移未 setup 或 thread_id 被跨会话使用时，服务必须失败可诊断，不能静默走 legacy 或读取他人动作；Task 1、7、8 加集成测试。

---

### Task 1: 锁定 LangGraph/PostgreSQL 依赖并建立真实 checkpointer 基线

**Files:**
- Modify: `ecommerce-support/pyproject.toml`
- Modify: `ecommerce-support/compose.yaml`
- Modify: `ecommerce-support/.env.example`
- Modify: `ecommerce-support/app/config.py`
- Create: `ecommerce-support/tests/workflow/test_checkpoint_postgres.py`
- Create: `ecommerce-support/tests/workflow/test_config.py`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Produces: `Settings.postgres_checkpoint_url: SecretStr | None`、`Settings.chat_orchestrator: Literal["langgraph","legacy"]`、`Settings.agent_max_tool_steps`、`Settings.agent_token_budget`、`Settings.action_ttl_seconds`。
- Produces: 可用的 `AsyncPostgresSaver`，已执行 `await checkpointer.setup()`。
- Consumes: 无。

- [ ] **Step 1: 写配置与真实 PostgreSQL 失败测试**

```python
def test_langgraph_requires_checkpoint_url():
    base = dict(openai_base_url="http://example.invalid", openai_model="fake", openai_api_key="fake")
    with pytest.raises(ValueError, match="POSTGRES_CHECKPOINT_URL"):
        Settings(**base, chat_orchestrator="langgraph", postgres_checkpoint_url=None)

async def test_postgres_saver_round_trip(postgres_url):
    async with AsyncPostgresSaver.from_conn_string(postgres_url) as saver:
        await saver.setup()
        builder = StateGraph(State)
        builder.add_node("node", lambda s: {"value": s["value"] + 1})
        builder.add_edge(START, "node")
        builder.add_edge("node", END)
        graph = builder.compile(checkpointer=saver)
        app = graph.compile(checkpointer=saver)
        await app.ainvoke({"value": 0}, {"configurable": {"thread_id": "probe"}})
        assert (await app.aget_state({"configurable": {"thread_id": "probe"}})).values["value"] == 1
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/workflow/test_config.py tests/workflow/test_checkpoint_postgres.py -q`

Expected: FAIL，缺少配置字段、依赖和 PostgreSQL fixture。

- [ ] **Step 3: 固定依赖与 Docker 服务**

```toml
"langgraph==1.2.12",
"langgraph-checkpoint-postgres==3.1.2",
"psycopg[binary,pool]>=3.2,<3.4",
```

```yaml
postgres:
  image: postgres:17.6-alpine
  environment:
    POSTGRES_DB: ecommerce_checkpoint
    POSTGRES_USER: checkpoint
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set in .env}
  ports: ["127.0.0.1:5433:5432"]
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U checkpoint -d ecommerce_checkpoint"]
```

- [ ] **Step 4: 实现 Settings 校验**

```python
chat_orchestrator: Literal["langgraph", "legacy"] = "langgraph"
postgres_checkpoint_url: SecretStr | None = None
agent_max_tool_steps: int = Field(default=4, ge=1, le=8)
agent_token_budget: int = Field(default=4096, ge=512)
action_ttl_seconds: int = Field(default=86400, ge=60, le=604800)

@model_validator(mode="after")
def require_checkpoint(self):
    if self.chat_orchestrator == "langgraph" and self.postgres_checkpoint_url is None:
        raise ValueError("POSTGRES_CHECKPOINT_URL is required for langgraph")
    return self
```

- [ ] **Step 5: 启动 PostgreSQL并运行 GREEN/接口探针**

Run: `.venv/bin/python -m pip install -e '.[test]'`

Expected: 安装锁定的 LangGraph/PostgreSQL依赖；如果需要网络，按运行环境申请权限，不改包来源。

Run: `docker compose up -d postgres && docker compose ps postgres`

Run: `.venv/bin/python -m pytest tests/workflow/test_config.py tests/workflow/test_checkpoint_postgres.py -q`

Expected: PASS；`setup()` 可重复运行，`ainvoke/aget_state` 正常。

若 1.2.12/3.1.2 的实际签名与 Context7/官方 README 不兼容，停止任务并向用户报告；不得改用 SQLite 或 MySQL saver。

- [ ] **Step 6: 记录阶段并提交**

```bash
git add ecommerce-support/pyproject.toml ecommerce-support/compose.yaml ecommerce-support/.env.example ecommerce-support/app/config.py ecommerce-support/tests/workflow/test_config.py ecommerce-support/tests/workflow/test_checkpoint_postgres.py dev-notes/ch05.md
git commit -m "build: add LangGraph PostgreSQL checkpoint baseline"
```

### Task 2: 定义 State、闲聊短路、七类意图与评估集

**Files:**
- Create: `ecommerce-support/app/workflow/__init__.py`
- Create: `ecommerce-support/app/workflow/state.py`
- Create: `ecommerce-support/app/workflow/routing.py`
- Create: `ecommerce-support/app/workflow/intent.py`
- Create: `ecommerce-support/app/workflow/prompts.py`
- Create: `ecommerce-support/evals/ch05/intent.jsonl`
- Create: `ecommerce-support/scripts/evaluate_ch05_intent.py`
- Create: `ecommerce-support/tests/workflow/test_routing.py`
- Create: `ecommerce-support/tests/workflow/test_intent.py`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Produces: `Intent`、`Route`、`ActionType`、`ActionOption`、`WorkflowState`。
- Produces: `explicit_smalltalk(text: str) -> bool`、`route_intent(intent: Intent) -> Route`、`allowed_tool_names(intent: Intent) -> Sequence[str]`。
- Produces: `IntentClassifier.classify(text: str) -> Intent`。
- Consumes: Task 1 Settings 的 `structured_output_method`。

- [ ] **Step 1: 写路由与跨轮 reducer 的 RED 测试**

```python
@pytest.mark.parametrize(("intent","route"), [
    ("商品咨询","knowledge"), ("退款退货","knowledge"),
    ("物流","business"), ("订单","business"), ("售后","business"),
    ("投诉","complaint"), ("闲聊","smalltalk"),
])
def test_all_intents_have_fixed_route(intent, route):
    assert route_intent(intent) == route

def test_greeting_with_business_content_is_not_short_circuited():
    assert explicit_smalltalk("你好，我的订单一直没到") is False
    assert explicit_smalltalk("你好") is True

def test_new_turn_clears_transient_state():
    assert initialize_turn(old_state, "新问题")["citation_map"] == {}
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/workflow/test_routing.py -q`

Expected: FAIL，workflow 模块不存在。

- [ ] **Step 3: 实现可 checkpoint 的类型和纯函数**

```python
Intent = Literal["物流","订单","商品咨询","退款退货","售后","投诉","闲聊"]
Route = Literal["knowledge","business","complaint","smalltalk"]
ActionType = Literal["transfer_human","create_ticket"]

ROUTES: dict[Intent, Route] = {
    "商品咨询":"knowledge", "退款退货":"knowledge",
    "物流":"business", "订单":"business", "售后":"business",
    "投诉":"complaint", "闲聊":"smalltalk",
}
EXPLICIT_SMALLTALK = {"你好","您好","谢谢","再见","你是谁"}
```

State 仅使用 LangGraph 可序列化类型；`merge_action_options` 以 action_id 去重并只保留最近 20 条。

- [ ] **Step 4: 写意图结构化重试 RED 测试**

```python
async def test_classifier_retries_once_then_succeeds():
    model = StructuredFake([ValueError("bad"), IntentDecision(intent="订单")])
    assert await IntentClassifier(model, "json_mode").classify("查订单") == "订单"
    assert model.calls == 2

async def test_classifier_fails_after_one_retry():
    with pytest.raises(IntentClassificationError):
        await IntentClassifier(StructuredFake([ValueError(), ValueError()]), "json_mode").classify("x")
```

- [ ] **Step 5: 实现 IntentClassifier**

```python
class IntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Intent

class IntentClassifier:
    async def classify(self, text: str) -> Intent:
        chain = self.model.with_structured_output(IntentDecision, method=self.method)
        for attempt in range(2):
            try:
                return (await chain.ainvoke(INTENT_PROMPT.format(question=text))).intent
            except Exception:
                if attempt == 1:
                    raise IntentClassificationError("intent_invalid") from None
```

- [ ] **Step 6: 建立 35 条标注样例与评估脚本**

每类至少 5 条，必须含“你好，我的订单没到”“物流太慢了但我只是抱怨”“我要投诉”“衣服不合身怎么办”等边界题。脚本输出总准确率、每类准确率和 7×7 混淆矩阵，任何结构化失败计错而不是跳过。

- [ ] **Step 7: 运行 GREEN**

Run: `.venv/bin/python -m pytest tests/workflow/test_routing.py tests/workflow/test_intent.py -q`

Expected: PASS。

- [ ] **Step 8: 记录阶段并提交**

```bash
git add ecommerce-support/app/workflow ecommerce-support/evals/ch05 ecommerce-support/scripts/evaluate_ch05_intent.py ecommerce-support/tests/workflow/test_routing.py ecommerce-support/tests/workflow/test_intent.py dev-notes/ch05.md
git commit -m "feat: add workflow state and intent routing"
```

### Task 3: 用现有工具实现最裸 Agent 循环

**Files:**
- Create: `ecommerce-support/app/workflow/raw_agent.py`
- Create: `ecommerce-support/scripts/demo_raw_agent_loop.py`
- Create: `ecommerce-support/tests/workflow/test_raw_agent.py`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Produces: `RawAgentResult(answer, messages, tool_steps, token_used, stop_reason)`。
- Produces: `run_raw_agent(model, tools, messages, *, max_tool_steps, token_budget, on_event) -> RawAgentResult`。
- Consumes: 现有 `ToolExecutor`、LangChain AIMessage/ToolMessage、Task 2 工具白名单。

- [ ] **Step 1: 写一次工具、两次工具和停止条件 RED 测试**

```python
async def test_order_then_logistics_takes_two_steps():
    model = FakeModel([tool_call("query_order", {"order_id":"1001"}),
                       tool_call("query_logistics", {"order_id":"1001"}),
                       final("订单已发货")])
    result = await run_raw_agent(model, tools, [HumanMessage("先查订单再查物流")],
                                 max_tool_steps=4, token_budget=4096, on_event=events.append)
    assert [s.name for s in result.tool_steps] == ["query_order","query_logistics"]
    assert result.stop_reason == "completed"

@pytest.mark.parametrize("case", ["duplicate","multiple_calls","budget","max_steps"])
async def test_raw_loop_stops_on_duplicate_call():
    repeated = tool_call("query_order", {"order_id":"1001"})
    result = await run_raw_agent(FakeModel([repeated, repeated]), tools, [HumanMessage("查订单")],
                                 max_tool_steps=4, token_budget=4096, on_event=lambda event: None)
    assert result.stop_reason == "repeated_tool_call"

async def test_raw_loop_rejects_multiple_calls_in_one_turn():
    result = await run_raw_agent(FakeModel([two_tool_calls()]), tools, [HumanMessage("查两个")],
                                 max_tool_steps=4, token_budget=4096, on_event=lambda event: None)
    assert result.stop_reason == "parallel_tool_calls"

async def test_raw_loop_enforces_budget_and_step_limit():
    assert (await run_case(token_budget=10)).stop_reason == "token_budget"
    assert (await run_case(max_tool_steps=1)).stop_reason == "max_tool_steps"
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/workflow/test_raw_agent.py -q`

Expected: FAIL，`run_raw_agent` 不存在。

- [ ] **Step 3: 实现最小显式循环**

```python
while True:
    response = await bound_model.ainvoke(messages)
    token_used += usage_or_estimate(response)
    if token_used > token_budget:
        return stopped("token_budget")
    if not response.tool_calls:
        return completed(response.text)
    if len(response.tool_calls) != 1:
        return stopped("parallel_tool_calls")
    call = validate_call(response.tool_calls[0], allowed)
    fingerprint = canonical_fingerprint(call)
    if fingerprint in seen:
        return stopped("repeated_tool_call")
    if len(steps) >= max_tool_steps:
        return stopped("max_tool_steps")
    result = await execute_one(call)
    messages.extend([response, ToolMessage(content=json.dumps(result, ensure_ascii=False),
                                           tool_call_id=call["id"])])
```

绑定模型必须传 `parallel_tool_calls=False`；工具执行仍经过 ToolExecutor 的 schema、超时和重试。

- [ ] **Step 4: 实现演示脚本**

```bash
.venv/bin/python scripts/demo_raw_agent_loop.py "先查订单 1001，再告诉我物流到哪了"
```

脚本从 `.env` 使用真实上游模型，默认只启用 query_order/query_product/query_logistics；输出步骤 JSON，不输出密钥或隐藏推理。

- [ ] **Step 5: 运行 GREEN 和脚本 mock 模式**

Run: `.venv/bin/python -m pytest tests/workflow/test_raw_agent.py -q`

Run: `.venv/bin/python scripts/demo_raw_agent_loop.py --scripted "先查订单再查物流"`

Expected: PASS；脚本显示两个工具步骤和 completed。

- [ ] **Step 6: 记录阶段并提交**

```bash
git add ecommerce-support/app/workflow/raw_agent.py ecommerce-support/scripts/demo_raw_agent_loop.py ecommerce-support/tests/workflow/test_raw_agent.py dev-notes/ch05.md
git commit -m "feat: demonstrate the bare agent loop"
```

### Task 4: 暴露权威证据并实现知识置信度闸

**Files:**
- Modify: `ecommerce-support/app/knowledge/retrieval.py`
- Modify: `ecommerce-support/app/knowledge/hybrid_retrieval.py`
- Modify: `ecommerce-support/app/knowledge/quality_repository.py`
- Create: `ecommerce-support/app/workflow/knowledge.py`
- Create: `ecommerce-support/tests/workflow/test_knowledge_gate.py`
- Modify: `ecommerce-support/tests/knowledge/test_retrieval.py`
- Modify: `ecommerce-support/tests/knowledge/test_hybrid_retrieval.py`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Produces: `KnowledgeEvidence(id, question, answer, category, section_path, score, score_kind)`。
- Produces: `retrieve_evidence(search, query) -> list[KnowledgeEvidence]`。
- Produces: `confidence_gate(question, evidence, threshold) -> GateDecision(passed, reason)`。
- Produces: `QualityRepository.record_retrieval_low(user_message_id, conversation_id, raw_question, reason)`。
- Consumes: dense `KnowledgeSearch`、hybrid `HybridKnowledgeSearch` 和 Task 2 State。

- [ ] **Step 1: 写旧公开契约与带分数内部接口 RED 测试**

```python
async def test_dense_find_contract_unchanged_and_internal_score_kept():
    evidence = await search.find_evidence("邮费")
    assert evidence[0][1] == 0.91
    assert await search.find("邮费") == [{"id":1,"question":"运费","answer":"满99元包邮","category":"配送"}]

def test_gate_rejects_missing_model_token():
    decision = confidence_gate("A3370 的保修多久", [evidence_without_a3370], 0.5)
    assert decision == GateDecision(False, "key_terms_uncovered")
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/workflow/test_knowledge_gate.py tests/knowledge/test_retrieval.py tests/knowledge/test_hybrid_retrieval.py -q`

Expected: FAIL，dense 无 `find_evidence` 且 gate 不存在。

- [ ] **Step 3: 实现统一证据转换和纯函数闸**

```python
@dataclass(frozen=True)
class KnowledgeEvidence:
    id: int
    question: str
    answer: str
    category: str
    section_path: str | None
    score: float
    score_kind: Literal["dense","rerank"]

def confidence_gate(question, evidence, threshold):
    if not evidence: return GateDecision(False, "no_evidence")
    if evidence[0].score < threshold: return GateDecision(False, "score_below_threshold")
    if not key_terms(question).issubset(evidence_terms(evidence)):
        return GateDecision(False, "key_terms_uncovered")
    return GateDecision(True, "passed")
```

`key_terms` 只抽取型号/数字串和受控政策主题，不要求每个中文词面完全匹配。

- [ ] **Step 4: 实现 retrieval_low_conf 幂等写**

复用 `knowledge_ingestion_state` 锁行模式，key 使用 `knowledge:quality:retrieval-low:{user_message_id}`，插入 `source='retrieval_low_conf'`。并发重试返回同一个 low_id。

- [ ] **Step 5: 运行 GREEN 和公开契约回归**

Run: `.venv/bin/python -m pytest tests/workflow/test_knowledge_gate.py tests/knowledge/test_retrieval.py tests/knowledge/test_hybrid_retrieval.py tests/knowledge/test_chat_knowledge.py -q`

Expected: PASS，`query_faq` 结果字段未增加。

- [ ] **Step 6: 记录阶段并提交**

```bash
git add ecommerce-support/app/knowledge/retrieval.py ecommerce-support/app/knowledge/hybrid_retrieval.py ecommerce-support/app/knowledge/quality_repository.py ecommerce-support/app/workflow/knowledge.py ecommerce-support/tests/workflow/test_knowledge_gate.py ecommerce-support/tests/knowledge/test_retrieval.py ecommerce-support/tests/knowledge/test_hybrid_retrieval.py dev-notes/ch05.md
git commit -m "feat: add deterministic knowledge confidence gate"
```

### Task 5: 将裸循环重构成可流式 ReAct Agent 子图

**Files:**
- Create: `ecommerce-support/app/workflow/agent.py`
- Create: `ecommerce-support/tests/workflow/test_agent_graph.py`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Produces: `build_agent_subgraph(model, tool_factory, settings)`，输入/输出 Task 2 `WorkflowState`。
- Produces custom stream payload：`{"event":"token"|"answer_reset"|"tool_status","data":dict[str, object]}`。
- Consumes: Task 3 的 fingerprint/usage/停止纯函数、Task 2 allowed_tool_names、现有 ToolExecutor。

- [ ] **Step 1: 写图轨迹和四类停止 RED 测试**

```python
async def test_agent_graph_order_then_logistics():
    graph = build_agent_subgraph(model=model, tool_factory=tool_factory, settings=settings)
    result = await graph.ainvoke(state_for("订单", "先查订单再查物流"))
    assert [m.name for m in result["tool_steps"]] == ["query_order","query_logistics"]
    assert result["agent_step_count"] == 2

async def test_multiple_tool_calls_stop_without_execution():
    result = await run_agent(two_tool_calls())
    assert result["stop_reason"] == "parallel_tool_calls"
    assert executor.calls == []

async def test_repeated_fingerprint_stop():
    result = await run_agent(repeated_order_calls())
    assert result["stop_reason"] == "repeated_tool_call"
    assert executor.calls == ["query_order"]

async def test_missing_usage_uses_conservative_estimate():
    result = await run_agent(chunks_without_usage(), token_budget=32)
    assert result["agent_token_used"] > 0
    assert result["stop_reason"] == "token_budget"

async def test_recursion_limit_is_last_resort():
    with pytest.raises(GraphRecursionError):
        await graph.ainvoke(looping_state, {"recursion_limit": 2})
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/workflow/test_agent_graph.py -q`

Expected: FAIL，Agent 子图不存在。

- [ ] **Step 3: 实现 `agent_model → agent_tools → agent_model`**

```python
builder = StateGraph(WorkflowState)
builder.add_node("agent_model", agent_model)
builder.add_node("agent_tools", agent_tools)
builder.add_node("agent_finish", agent_finish)
builder.add_edge(START, "agent_model")
builder.add_conditional_edges("agent_model", after_model, {
    "tools":"agent_tools", "finish":"agent_finish", "stop":"agent_finish",
})
builder.add_edge("agent_tools", "agent_model")
builder.add_edge("agent_finish", END)
```

`agent_model` 使用 `get_stream_writer()` 推送 token；如果最终出现工具调用，先推 `answer_reset` 再进入工具节点，避免把工具前言当最终回答。每轮只接受一个工具调用。

- [ ] **Step 4: 实现工具白名单与持久化消息**

按 intent 从 Task 2 取得工具名，用现有 `build_tools` 生成工具后再筛选。每次 AI 工具申请和 ToolMessage 继续写 MySQL messages；查询工具是只读副作用，checkpoint 恢复可依据 tool fingerprint/result 避免重复执行。

- [ ] **Step 5: 运行 GREEN**

Run: `.venv/bin/python -m pytest tests/workflow/test_agent_graph.py tests/test_tools.py tests/test_chat_tools.py -q`

Expected: PASS；旧工具基础设施无回归。

- [ ] **Step 6: 记录阶段并提交**

```bash
git add ecommerce-support/app/workflow/agent.py ecommerce-support/tests/workflow/test_agent_graph.py dev-notes/ch05.md
git commit -m "feat: add bounded ReAct agent subgraph"
```

### Task 6: 组装确定性外层 StateGraph 与结构化节点日志

**Files:**
- Create: `ecommerce-support/app/workflow/graph.py`
- Create: `ecommerce-support/app/workflow/logging.py`
- Create: `ecommerce-support/tests/workflow/test_graph_routes.py`
- Create: `ecommerce-support/tests/workflow/test_graph_turns.py`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Produces: `build_workflow(deps, checkpointer) -> CompiledStateGraph`。
- Produces custom events：`workflow_status`、知识 `citations`、固定分支 `token`。
- Consumes: Tasks 2/4/5 分类器、路由、知识闸和 Agent 子图。

- [ ] **Step 1: 写四出口、强制检索和跨轮清理 RED 测试**

```python
async def test_policy_visits_retrieval_and_gate_before_agent():
    result = await graph.ainvoke(policy_state, config)
    assert result["visited_nodes"] == [
        "initialize_turn","explicit_smalltalk_guard","resolve_reference",
        "classify_intent","route_intent","knowledge_retrieval",
        "confidence_gate","agent_subgraph","action_advice",
        "persist_final","log_completion",
    ]

async def test_complaint_never_calls_agent():
    result = await graph.ainvoke(state_for("我要投诉"), config("complaint"))
    assert "agent_subgraph" not in result["visited_nodes"]
    assert [item["type"] for item in result["action_options"]] == ["transfer_human","create_ticket"]

async def test_explicit_smalltalk_makes_zero_model_calls():
    result = await graph.ainvoke(state_for("你好"), config("smalltalk"))
    assert result["answer"] == SMALLTALK_REPLY
    assert model.calls == 0

async def test_second_turn_clears_old_evidence_actions_and_stop_reason():
    await graph.ainvoke(first_turn_with_evidence, config("same-thread"))
    result = await graph.ainvoke(state_for("你好"), config("same-thread"))
    assert result["evidence"] == []
    assert result["action_options"] == []
    assert result["stop_reason"] is None
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/workflow/test_graph_routes.py tests/workflow/test_graph_turns.py -q`

Expected: FAIL，外层图不存在。

- [ ] **Step 3: 逐节点实现外层图**

使用 spec 中固定边；`resolve_reference` 原样复制；knowledge gate fail 节点先幂等入池再固定回复；投诉固定回复与两个动作；闲聊固定回复。每个节点通过公共包装器记录 duration 和 visited_nodes。

- [ ] **Step 4: 实现 ActionAdvice**

```python
class ActionAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[ActionType] = Field(max_length=2)

async def action_advice(state):
    decision = await advisor.ainvoke(minimal_action_context(state))
    return {"action_options": make_options(state, decision.actions)}
```

ActionAdvice 失败只记录 `action_advice_failed` 并返回空动作，不撤销已经完成的回答。投诉不调用 advisor。

- [ ] **Step 5: 实现结构化日志白名单**

只记录 run/node/route/计数/耗时/停止原因；测试用 caplog 断言日志不含 Prompt、API key 和完整工具 payload。

- [ ] **Step 6: 运行 GREEN**

Run: `.venv/bin/python -m pytest tests/workflow/test_graph_routes.py tests/workflow/test_graph_turns.py -q`

Expected: PASS。

- [ ] **Step 7: 记录阶段并提交**

```bash
git add ecommerce-support/app/workflow/graph.py ecommerce-support/app/workflow/logging.py ecommerce-support/tests/workflow/test_graph_routes.py ecommerce-support/tests/workflow/test_graph_turns.py dev-notes/ch05.md
git commit -m "feat: compose deterministic customer support workflow"
```

### Task 7: 接入 WorkflowService、PostgreSQL 生命周期和 SSE

**Files:**
- Create: `ecommerce-support/app/workflow/service.py`
- Modify: `ecommerce-support/app/main.py`
- Modify: `ecommerce-support/app/routes/chat.py`
- Modify: `ecommerce-support/tests/test_api.py`
- Create: `ecommerce-support/tests/workflow/test_service.py`
- Create: `ecommerce-support/tests/workflow/test_resume.py`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Produces: `WorkflowService.prepare(message, conversation_id) -> PreparedWorkflowTurn`。
- Produces: `WorkflowService.stream(turn) -> AsyncIterator[StreamEvent]`。
- Consumes: Task 6 compiled graph、Task 1 AsyncPostgresSaver、现有 Repository。

- [ ] **Step 1: 写 SSE 顺序、取消和 legacy 开关 RED 测试**

```python
async def test_workflow_sse_session_status_token_actions_done():
    events = [e async for e in service.stream(await service.prepare("我要投诉", None))]
    assert [e.event for e in events] == [
        "session","workflow_status","token","action_options","done"
    ]

async def test_cancel_does_not_persist_partial_answer():
    stream = service.stream(await service.prepare("查订单", None))
    await anext(stream)
    await stream.aclose()
    assert not any(row["role"] == "assistant" for row in repo.rows)

def test_langgraph_mode_does_not_silently_fallback_when_postgres_down():
    with pytest.raises(RuntimeError, match="checkpoint"):
        with TestClient(create_app(settings=langgraph_settings(broken_postgres_url))):
            pass

def test_explicit_legacy_mode_uses_chat_service():
    app = create_app(settings=legacy_settings, chat_service=legacy_service)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert app.state.chat_service is legacy_service
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/workflow/test_service.py tests/workflow/test_resume.py tests/test_api.py -q`

Expected: FAIL，WorkflowService 未装配。

- [ ] **Step 3: 实现 prepare/stream 和 custom event 翻译**

```python
config = {
    "configurable": {"thread_id": f"conversation:{turn.conversation_id}"},
    "recursion_limit": settings.agent_max_tool_steps * 3 + 12,
}
async for part in graph.astream(input_state, config, stream_mode="custom"):
    yield StreamEvent(part["event"], part["data"])
```

先发 session；只有正常 stop 才保存最终 assistant 并发 done。客户端取消时关闭图流，释放 busy 锁，不保存候选答案。

- [ ] **Step 4: 在 lifespan 持有 AsyncPostgresSaver**

```python
async with AsyncPostgresSaver.from_conn_string(url) as checkpointer:
    await checkpointer.setup()
    application.state.workflow = build_workflow(dependencies, checkpointer)
    application.state.chat_service = WorkflowService(
        resolved_settings, application.state.workflow, repository
    )
    yield
```

默认 langgraph 失败必须阻止 readiness；legacy 只有显式配置才装配旧 ChatService。health 返回依赖明细或提供独立 readiness，测试按最终接口固定。

- [ ] **Step 5: 写真实中断恢复测试**

构造一个在 query_order 后抛出可恢复中断的测试节点，使用真实 PostgreSQL thread_id 重新运行；断言已 checkpoint 的 tool fingerprint/result 被复用，工具执行计数仍为 1。

- [ ] **Step 6: 运行 GREEN 与全量回归**

Run: `.venv/bin/python -m pytest tests/workflow/test_service.py tests/workflow/test_resume.py tests/test_api.py tests/test_chat_routes.py -q`

Expected: PASS。

- [ ] **Step 7: 记录阶段并提交**

```bash
git add ecommerce-support/app/workflow/service.py ecommerce-support/app/main.py ecommerce-support/app/routes/chat.py ecommerce-support/tests/test_api.py ecommerce-support/tests/workflow/test_service.py ecommerce-support/tests/workflow/test_resume.py dev-notes/ch05.md
git commit -m "feat: serve checkpointed workflow over SSE"
```

### Task 8: 将建工单变成用户触发的受控动作

**Files:**
- Modify: `ecommerce-support/app/repositories.py`
- Create: `ecommerce-support/app/workflow/actions.py`
- Modify: `ecommerce-support/app/schemas.py`
- Modify: `ecommerce-support/app/main.py`
- Modify: `ecommerce-support/tests/test_repository.py`
- Create: `ecommerce-support/tests/workflow/test_actions.py`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Produces: `TicketActionRequest(conversation_id: int, action_id: str)`。
- Produces: `ActionService.create_ticket(conversation_id, action_id) -> TicketActionResult`。
- Consumes: compiled graph `aget_state(config)`、Task 6 checkpoint actions、现有 build_tools/ToolExecutor。

- [ ] **Step 1: 写工单与转人工解耦 RED 测试**

```python
async def test_create_ticket_does_not_mark_conversation_transferred(repo):
    result = await repo.create_ticket_once("T123", cid, "投诉内容", "投诉")
    assert result["ticket_no"] == "T123"
    assert (await repo.get_conversation(cid))["status"] == "进行中"
```

- [ ] **Step 2: 写动作安全与重放 RED 测试**

```python
async def test_cross_conversation_action_is_rejected():
    with pytest.raises(ActionNotFound):
        await service.create_ticket(other_conversation_id, action_id)

async def test_expired_action_is_rejected():
    clock.advance(settings.action_ttl_seconds + 1)
    with pytest.raises(ActionExpired):
        await service.create_ticket(conversation_id, action_id)

async def test_duplicate_action_returns_same_ticket():
    first = await service.create_ticket(conversation_id, action_id)
    second = await service.create_ticket(conversation_id, action_id)
    assert first.ticket_no == second.ticket_no

async def test_concurrent_duplicate_action_creates_one_ticket():
    results = await asyncio.gather(*[service.create_ticket(conversation_id, action_id) for _ in range(2)])
    assert results[0].ticket_no == results[1].ticket_no
    assert await ticket_count(results[0].ticket_no) == 1

def test_client_cannot_override_description_or_ticket_type():
    response = client.post("/chat/actions/ticket", json={
        "conversation_id": conversation_id, "action_id": action_id,
        "description": "伪造内容", "ticket_type": "咨询",
    })
    assert response.status_code == 422
```

- [ ] **Step 3: 运行 RED**

Run: `.venv/bin/python -m pytest tests/test_repository.py tests/workflow/test_actions.py -q`

Expected: FAIL，现有仓库仍标记已转人工且动作服务不存在。

- [ ] **Step 4: 拆开数据库语义并实现 ActionService**

```python
ticket_no = "T" + hashlib.sha256(action_id.encode()).hexdigest()[:30].upper()
snapshot = await graph.aget_state(thread_config(conversation_id))
action = require_valid_action(snapshot.values["action_options"], action_id, now, ttl)
tools = build_tools(repo, ToolContext(conversation_id, ticket_no), knowledge_search)
result = await execute_named_tool(tools, "create_ticket", {
    "description": action["description"],
    "ticket_type": action["ticket_type"],
})
```

重复请求依赖 ticket_no + `create_ticket_once` 返回同一结果；checkpoint consumed 更新是 UI/审计状态，不是唯一幂等屏障。

- [ ] **Step 5: 暴露严格动作接口**

```python
@application.post("/chat/actions/ticket", response_model=TicketActionResult)
async def create_ticket_action(request: TicketActionRequest):
    return await application.state.action_service.create_ticket(
        request.conversation_id, request.action_id
    )
```

错误映射：未知/跨会话 404，过期 409，依赖故障 503，参数 schema 422。

- [ ] **Step 6: 运行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_repository.py tests/workflow/test_actions.py -q`

Expected: PASS；查询 tickets 只有一行且 conversation.status 仍为进行中。

- [ ] **Step 7: 记录阶段并提交**

```bash
git add ecommerce-support/app/repositories.py ecommerce-support/app/workflow/actions.py ecommerce-support/app/schemas.py ecommerce-support/app/main.py ecommerce-support/tests/test_repository.py ecommerce-support/tests/workflow/test_actions.py dev-notes/ch05.md
git commit -m "feat: require user action before ticket creation"
```

### Task 9: 用 Vibe Coding 在聊天页增加两个独立动作

**Files:**
- Modify: `ecommerce-support/app/static/chat.js`
- Modify: `ecommerce-support/app/static/chat.css`
- Modify: `ecommerce-support/app/static/index.html`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Consumes SSE `workflow_status` 与 `action_options`。
- Calls `POST /chat/actions/ticket` only for create_ticket。
- Produces independent transfer/ticket button state in current browser session。

- [ ] **Step 1: 扩展 SSE 客户端状态**

在 `streamChat` 中收集动作并更新可读状态：

```javascript
if(event==='workflow_status') updateWorkflowStatus(view,data);
if(event==='action_options' && Array.isArray(data.items)) actions=data.items;
return {answer,reason,citations,messageId,actions};
```

未知事件忽略；动作在 done 前未完整到达时不渲染。

- [ ] **Step 2: 渲染两个独立按钮**

```javascript
function addActionOptions(view,items) {
  for (const item of items) {
    const button=document.createElement('button');
    button.textContent=item.label;
    button.onclick=item.type==='transfer_human'
      ? ()=>simulateTransfer(button,view)
      : ()=>createTicket(item,button,view);
  }
}
```

`simulateTransfer` 只锁当前按钮、显示“已转接人工客服”并插入客服小猫问候；不 fetch。建工单按钮只锁自身并 POST conversation_id/action_id，成功显示工单号，失败允许重试。

- [ ] **Step 3: 增加无障碍和移动端样式**

动作容器有 aria-label；进行中使用 `aria-busy`；两个按钮在窄屏换行，不覆盖引用和满意度区域。

- [ ] **Step 4: 浏览器手验四种组合**

1. 两个都不点，继续发消息；
2. 只点转人工，Network 中无动作请求；
3. 只点建工单，Network 只有一次 ticket 请求；
4. 先后都点，两个状态独立。

- [ ] **Step 5: 记录截图结果和阶段提交**

```bash
git add ecommerce-support/app/static/chat.js ecommerce-support/app/static/chat.css ecommerce-support/app/static/index.html dev-notes/ch05.md
git commit -m "feat: add independent support action buttons"
```

### Task 10: 运行意图评估、五项真实验收和整枝验证

**Files:**
- Modify: `ecommerce-support/README.md`
- Create: `ecommerce-support/reports/ch05/intent-evaluation.json`
- Create: `ecommerce-support/reports/ch05/acceptance.md`
- Modify: `dev-notes/ch05.md`

**Interfaces:**
- Consumes: Tasks 1–9 全部产物。
- Produces: 可复现演示命令、意图指标、五项验收证据和完成前 review 输入。

- [ ] **Step 1: 运行全量自动测试**

Run: `.venv/bin/python -m pytest -q`

Expected: 全部 PASS；需要本机 MySQL/PostgreSQL/Milvus 的测试必须真实连接，不能以 mock 结果替代。

- [ ] **Step 2: 运行七类意图评估**

Run: `.venv/bin/python scripts/evaluate_ch05_intent.py --dataset evals/ch05/intent.jsonl --output reports/ch05/intent-evaluation.json`

Expected: 输出 35 条逐题结果、总准确率、每类准确率和混淆矩阵；错误样例逐条列出，不只给百分比。

- [ ] **Step 3: 演示裸循环和 LangGraph 多步 Agent**

Run: `.venv/bin/python scripts/demo_raw_agent_loop.py "先查订单 1001，再告诉我物流到哪了"`

Run: `curl -sN -X POST http://127.0.0.1:8001/chat -H 'Content-Type: application/json' -d '{"message":"先查订单 1001，再告诉我物流到哪了"}'`

Expected: 两条路径都显示 query_order → query_logistics，LangGraph 最终逐 token 收敛。

- [ ] **Step 4: 验收政策、投诉、闲聊**

- 政策：日志含 `knowledge_retrieval` 和 `confidence_gate`，强证据才进入 Agent。
- 投诉：SSE 含两个 action_options；转人工无后端请求；建工单后 tickets +1。
- 闲聊：固定话术且模型 fake/计数器为 0。

把脱敏命令、日志字段、数据库计数和浏览器结果写入 `reports/ch05/acceptance.md`。

- [ ] **Step 5: 验收 checkpoint 恢复**

在测试 failpoint 后重启服务并继续相同 thread_id；确认从 PostgreSQL checkpoint 恢复、已成功步骤不重复执行、最终消息只落一次。记录 checkpoint 表存在性和 run_id，不记录完整状态/PROMPT。

- [ ] **Step 6: 完成前验证和阶段记录**

运行：

```bash
git diff --check
git status --short
.venv/bin/python -m pytest -q
```

更新 README 演示命令；在 `dev-notes/ch05.md` 记录 Task 10，随后进入 `superpowers:requesting-code-review`，整枝 review 结论另起一段记录，修复后再用 `superpowers:verification-before-completion` 和 `superpowers:finishing-a-development-branch`。

- [ ] **Step 7: 提交验收产物**

```bash
git add ecommerce-support/README.md ecommerce-support/reports/ch05 dev-notes/ch05.md
git commit -m "docs: add ch05 workflow acceptance evidence"
```
