# ch05 Workflow 骨架与主力 ReAct Agent 设计

状态：brainstorm 已定稿，书面 spec 待用户评审；实施计划和产品代码尚未开始。

## 目标与范围

把现有客服聊天从单次 Function Calling 服务升级为“确定性 Workflow 做骨架、受控 ReAct Agent 做核心节点”的架构。FastAPI 继续提供现有聊天页、SSE 对话接口和售后结构化提取；MySQL 继续保存业务数据，Milvus 继续提供知识检索。新增 LangGraph StateGraph、PostgreSQL checkpointer 和受控多步工具循环。

本章先保留一个不依赖 LangGraph 的裸 Agent 循环作为教学与验收产物，用同一组行为测试说明 Agent 的本质是“模型决定工具 → 执行工具 → 结果回灌 → 继续或收敛”。线上聊天只使用 LangGraph 版本，不引用裸循环脚本。

本章不实现正式指代消解、正式意图模型、上下文策略升级、MCP、数据飞轮消费或真人客服系统。指代消解原样透传；意图识别只做七类结构化分类。聊天页的转人工仍是前端模拟。现有 ch02 五个业务工具不新增同类能力；主力 Agent 只可使用查询类工具，建工单只能由用户点击建议按钮触发。

## 已确认的技术决策

- 外层使用确定性 StateGraph，主力 Agent 使用显式的 ReAct 子图，不使用黑盒式预构建 Agent 代替核心控制逻辑。
- 新增 PostgreSQL，仅保存 LangGraph checkpoint；MySQL 仍是会话、消息、工单、知识原文和低置信度问题的权威库。
- LangGraph 版本按实现时 Context7 返回的官方当前稳定接口锁定；设计阶段查证的是 LangGraph 1.0.8 的 StateGraph、条件边、checkpointer、thread_id 和 ToolNode。
- checkpointer 使用官方 AsyncPostgresSaver。PostgreSQL 与 MySQL 分别健康检查，不把 checkpointer 数据写回 MySQL。
- ch04 当前活动集合即使仍是旧 dense `knowledge`，ch05 也通过现有活动检索器接口工作；以后切到 `knowledge_hybrid_v1` 无需改变图结构。
- 明确闲聊表达先由代码白名单识别并直接返回固定话术，真正做到零模型调用。未命中白名单的请求才进入模型意图识别；这是对“所有意图用 Prompt 分类”和“闲聊不花模型调用”冲突的确认解决方案。
- 当前 `Repository.create_ticket_once` 会顺带把会话状态改成“已转人工”，与本章“转人工、建工单两件事”冲突。实施时必须拆开：建工单只写 tickets，不修改 conversations.status；本章前端模拟转人工也不写数据库。

官方参考：
- [LangGraph StateGraph](https://github.com/langchain-ai/langgraph/tree/1.0.8)
- [LangGraph PostgreSQL checkpointer](https://github.com/langchain-ai/langgraph/tree/1.0.8/libs/checkpoint-postgres)
- [LangGraph SQLite/PostgreSQL persistence](https://github.com/langchain-ai/langgraph/tree/1.0.8/libs)

## 组件边界

### FastAPI 传输层

`POST /chat` 的请求结构和 SSE 传输继续兼容当前页面。路由只负责：

1. 校验输入与会话归属；
2. 创建或读取 MySQL conversation；
3. 保存本轮 user 消息并生成稳定 run_id；
4. 调用 WorkflowService；
5. 将 WorkflowEvent 转成 SSE；
6. 客户端断开时取消当前流，不把未完成答案写成最终 assistant 消息。

`POST /chat/actions/ticket` 是新的受控动作接口，只接受 `conversation_id` 和 `action_id`。描述、工单类型和稳定 ticket_no 从服务端 checkpoint 中取，浏览器不能自报工单内容。

### WorkflowService

WorkflowService 隔离 FastAPI 与 LangGraph，负责构造输入状态、传入 checkpointer config、转译 LangGraph 流事件以及执行恢复策略。它不直接实现意图规则、检索或工具逻辑。

默认 `CHAT_ORCHESTRATOR=langgraph`。保留 `legacy` 配置作为本章回滚入口，旧 ChatService 暂不删除。回滚只改变新请求的入口，不迁移或删除 checkpoint、MySQL 消息和工单。

### WorkflowState

State 使用 TypedDict 或等价的显式类型，并把长期会话字段与本轮临时字段区分清楚。至少包含：

- 标识：conversation_id、run_id、user_message_id；
- 输入：raw_question、resolved_question；
- 路由：intent、route；
- 知识：retrieval_query、ranked_chunks、evidence、citation_map、gate_result；
- Agent：messages、agent_step_count、agent_token_used、tool_fingerprints、stop_reason；
- 输出：answer、finish_reason、suggested_actions、error；
- 审计：visited_nodes、node_timings。

新一轮初始化节点必须覆盖上一轮临时字段，不能让旧证据、动作建议或停止原因泄漏到新问题。对话消息由 reducer 追加；run_id 用于区分同一个 thread 下的不同请求。

checkpointer config 使用：

```python
{"configurable": {"thread_id": f"conversation:{conversation_id}"}}
```

PostgreSQL checkpoint 是图执行、恢复和建议动作校验的权威源；MySQL messages 是用户可见消息与工具流水的权威源。两库不做分布式事务，通过 run_id、消息 ID、工具指纹和幂等写避免重放副作用。如果 checkpoint 丢失，服务可以从 MySQL 完整轮次重建对话输入，但不能假装恢复到中间节点。

## 外层图

主路径为：

```text
START
  → initialize_turn
  → explicit_smalltalk_guard
      ├─ explicit smalltalk → fixed_smalltalk
      └─ otherwise → resolve_reference
                       → classify_intent
                       → route_intent
                           ├─ knowledge → knowledge_retrieval
                           │               → confidence_gate
                           │                   ├─ pass → agent_subgraph
                           │                   └─ fail → low_confidence_fallback
                           ├─ business → agent_subgraph
                           ├─ complaint → complaint_response
                           └─ smalltalk → fixed_smalltalk
  → action_advice（仅 Agent 路径）
  → persist_final
  → log_completion
  → END
```

每个出口最终都经过持久化和日志节点。节点只做一个明确职责，返回最小状态增量；路由函数只看已经验证的枚举或布尔字段。

### 明确闲聊规则

代码规则只覆盖小而明确的集合，例如问候、致谢、告别和“你是谁”。规则采用规范化后的完整短句匹配或受控正则，不用宽泛关键词吞掉“你好，我的订单没到”等业务问题。命中后直接返回固定客服话术，不调用指代消解、意图模型或 Agent。规则未命中时才走完整图。

### 指代消解

本章 `resolved_question = raw_question`，保留节点、日志和接口位置，后续可替换而不影响下游。节点不能自行读取或改写历史。

### 意图识别

使用同一个上游聊天模型和 `with_structured_output`，输出严格枚举：

- 物流
- 订单
- 商品咨询
- 退款退货
- 售后
- 投诉
- 闲聊

Prompt 只做分类，不回答用户问题。解析或枚举失败重试一次；仍失败发服务错误，不静默猜测。Prompt 和纯数据通过七类标注集评估，不为 Prompt 写实现镜像式单元测试。

### 固定分流

代码表完整覆盖七类意图：

| 意图 | 出口 |
| --- | --- |
| 商品咨询、退款退货 | knowledge |
| 物流、订单、售后 | business |
| 投诉 | complaint |
| 闲聊 | smalltalk |

投诉节点返回固定安抚话术，并建议 `transfer_human` 与 `create_ticket` 两个独立动作，不进入 Agent。闲聊节点返回固定话术，不进入 Agent。

## 知识检索与置信度闸

知识出口必须先调用现有活动知识检索器，模型不能跳过。图内使用内部证据适配器，保留 chunk ID、问题、答案、分类、章节路径和检索/重排分数；`query_faq(keyword: str)` 的公开入参出参不改变。

最简确定性闸在 Agent 前执行：

1. 至少存在一个已在 MySQL 标记 done 且 hash 匹配的权威 chunk；
2. 最佳结果达到活动检索器对应的校准阈值：旧 dense 使用 knowledge_score_threshold，混合重排使用 rerank_score_threshold；
3. 明确型号、订单限定词或政策主题没有在证据中完全缺失。

RRF 分数不当作概率。适配器必须明确分数来自 dense、rerank 或其他受支持策略；无法比较的分数视为未通过，而不是默认放行。

闸失败时：

- 不进入 Agent；
- 返回统一知识不足话术；
- 使用 `source=retrieval_low_conf` 将用户原话、会话、原因写入 low_confidence_questions；
- 以 user_message_id/run_id 幂等，恢复或重试不重复入池。

业务数据类没有预检索证据，直接进入 Agent，不走此闸。

## 裸 Agent 循环

`scripts/demo_raw_agent_loop.py` 保留最小实现：

1. 将消息交给绑定查询工具的模型；
2. 没有工具申请则输出答案并结束；
3. 有工具申请则验证名称、参数和调用 ID；
4. 顺序执行允许的第一个工具并保存 AIMessage/ToolMessage；
5. 把结果回灌模型并进入下一轮；
6. 达到停止条件时安全结束。

脚本复用现有 ToolExecutor、工具 schema 和错误语义，不复制业务工具实现。默认使用 mock 演示数据；显式实时选项才访问配置的上游模型。输出每轮步骤、工具名、参数摘要和停止原因，不输出隐藏推理或密钥。

## ReAct Agent 子图

最终 Agent 使用显式子图：

```text
agent_model
  ├─ no tool call → agent_finish
  ├─ missing information → agent_finish（追问）
  └─ valid tool call → agent_tools → agent_model
```

Agent 不拥有 create_ticket。工具按外层意图收窄：

- 商品咨询：query_product、query_order、query_logistics；
- 退款退货：query_order、query_logistics、query_product；
- 物流：query_logistics、query_order；
- 订单：query_order、query_logistics、query_product；
- 售后：query_order、query_logistics、query_product。

知识路径把通过闸门的证据作为受控数据注入 Agent System 内容，并沿用 ch04 引用校验。Agent 可根据用户提供的订单号继续查订单或物流，但不能重新决定是否执行 RAG。

每一轮最多接收一个工具调用，`parallel_tool_calls=False`。停止条件：

- 无工具调用：正常答案；
- 信息不足：明确追问；
- 最多四次工具执行；
- 达到可配置 `AGENT_TOKEN_BUDGET`；
- 同一工具和规范化参数重复申请；
- ToolExecutor 超时重试后仍失败；
- LangGraph recursion_limit 作为最后一道硬保护。

token 优先读取模型 usage_metadata；提供者缺失 usage 时，用现有保守消息字节估算，不能记成零。达到限制、重复调用或工具失败时返回受控说明，不保存或展示未经核验的候选答案。

所有工具状态继续通过 SSE `tool_status` 推送。复杂问题的日志和消息流水应能看到 query_order 与 query_logistics 分两步执行。

## Agent 动作建议

投诉分支固定建议两个动作。Agent 路径在答案正常收敛后，使用同一模型做一个小型结构化 ActionAdvice 判断，输入仅含当前意图、用户问题、工具结果状态、停止原因和最终答案，输出：

```json
{"actions": ["transfer_human", "create_ticket"]}
```

actions 可为空、一个或两个。该调用不能改写答案、不能执行动作，也不读取隐式推理。若 ActionAdvice 失败，答案仍可正常完成，只是不显示可选动作，并记录结构化错误。

建议动作写入 checkpoint 的有界 action_options（最近 20 条、24 小时有效）。每条包含随机 action_id、run_id、类型、服务端 description、ticket_type、created_at 和 consumed 状态。浏览器只收到 action_id、类型和按钮文案。

## 建工单与转人工分离

### 转人工

`transfer_human` 完全由前端模拟。点击后：

1. 只锁定该按钮；
2. 显示“已转接人工客服”；
3. 插入客服消息“您好，我是客服小猫，请问有什么可以帮您的”。

它不调用后端、不修改 conversations.status，也不影响建工单按钮。刷新后是否保留模拟状态沿用现有 sessionStorage 范围，不伪装真人系统状态。

### 建工单

点击 `create_ticket` 调用受控动作接口。后端：

1. 按 conversation_id 读取对应 thread checkpoint；
2. 校验 action_id 存在、类型正确、未过期；
3. 使用 checkpoint 中的 description/ticket_type 构造现有 create_ticket 工具上下文；
4. 以 action_id 派生稳定 ticket_no；
5. 调用现有 ToolExecutor；
6. 重复请求返回同一工单结果；
7. 更新 action 状态并返回 ticket_no。

投诉对应 ticket_type=投诉；退款退货/售后建议对应售后；其他咨询对应咨询。浏览器不能提交或覆盖这些字段。

`Repository.create_ticket_once` 改为只创建 tickets，不再把 conversation 状态更新为已转人工。两个按钮分别锁定，用户可以按任意顺序点一个、两个都点或都不点；继续发送消息不会触发任何隐式动作。

## SSE 与前端协议

保留：`session`、`tool_status`、`citations`、`answer_reset`、`token`、`done`、`error`。

新增：

- `workflow_status`：`{node, label, status}`，只暴露允许的节点名称和用户可读状态；
- `action_options`：`{items: [{action_id, type, label}]}`。

不向浏览器发送完整 State、Prompt、工具密钥、checkpoint 内容或模型隐藏推理。Agent 答案按上游 token 流式传输；ActionAdvice 在答案后执行，动作帧在 done 前发送。

前端继续以 textContent 和安全 DOM 节点渲染，不把模型输出或动作 label 当 HTML。建工单按钮显示进行中、成功工单号或可重试错误；转人工按钮只做本地模拟。旧页面已经出现的消息不追溯增加动作。

## 日志与可观测性

每个节点发结构化应用日志，字段至少包括：

- timestamp、run_id、conversation_id；
- node、intent、route、status；
- duration_ms；
- retrieval_count、gate_reason；
- agent_step_count、tool_name、tool_status；
- stop_reason、error_code。

日志不得记录密钥、完整 Prompt、模型隐藏推理或未经必要脱敏的工具返回。验收“政策问题经过强制检索”以 `node=knowledge_retrieval,status=completed` 的日志和图测试为证据，不以模型回答内容推断。

`log_completion` 覆盖所有正常出口；图外异常由 WorkflowService 的统一异常日志补齐。健康检查应区分应用、MySQL、PostgreSQL checkpointer 与活动知识依赖，避免仅返回进程存活。

## 依赖、Docker 与配置

`compose.yaml` 新增固定大版本的 PostgreSQL 服务、持久卷、仅本机端口和健康检查。应用配置新增：

- `POSTGRES_CHECKPOINT_URL`；
- `CHAT_ORCHESTRATOR=langgraph|legacy`；
- `AGENT_MAX_TOOL_STEPS=4`；
- `AGENT_TOKEN_BUDGET`；
- `ACTION_TTL_SECONDS=86400`。

依赖增加与锁定 LangGraph、官方 PostgreSQL checkpointer 包及 async PostgreSQL 驱动。具体包名、版本、连接参数、setup 和 streaming API 在实施前再次用 Context7 查询，并以本地最小探针验证；若当前官方接口与固定方案冲突，停止询问，不换非官方 MySQL saver。

启动时执行 checkpointer 官方 setup，要求幂等。若默认 orchestrator=langgraph 且 PostgreSQL不可用，应用启动失败或 readiness 失败，不能静默退回 legacy。只有显式配置 legacy 才使用旧 ChatService。

## 验证策略

### TDD

- 七类意图到四出口的穷举映射；
- 明确闲聊规则不误吞带业务内容的问候；
- 意图结构化失败重试及失败出口；
- 知识类必经 retrieval/gate，业务类不经过 gate；
- 低置信度入池幂等；
- 裸循环一次工具、多次工具、追问、重复调用与预算停止；
- Agent 子图工具白名单、四步上限、recursion_limit、usage fallback；
- 投诉永不进入 Agent；
- create_ticket 不再改变会话为已转人工；
- action_id 校验、过期、跨会话、重复请求和工单幂等；
- SSE 事件顺序、取消和未完成答案不落库；
- PostgreSQL checkpoint 节点中断恢复且不重复工具副作用；
- legacy 切换保持当前回归。

### Prompt 与评估集

建立七类意图标注集，包含明确样例、边界样例和容易混淆的退款/售后、订单/物流、投诉/普通负面情绪。报告每类准确率、总准确率和混淆矩阵。指代消解本章透传，用固定样例验证不修改文本。

### 集成与浏览器验收

使用本机 MySQL、PostgreSQL、Milvus、真实上游模型完成：

1. 政策问题日志出现强制检索节点；
2. “订单 1001 的物流到哪了”由 Agent 调物流/订单工具并流式回答；
3. “我要投诉”出现两个独立按钮；转人工只做本地模拟，建工单才新增 ticket；
4. 明确闲聊命中代码规则，模型请求计数为零；
5. 复杂问题出现至少两次不同工具步骤；
6. 服务中断后从 PostgreSQL checkpoint 恢复，不重复已成功副作用。

前端按用户指定的 Vibe Coding 例外直接实现和浏览器验证，不单独套 brainstorm、TDD、code review 门禁。后端、图和脚本仍完整走 TDD、评审与完成验证。

## 阶段交付

brainstorm 定稿、计划评审通过、每个计划任务完成、code review 结论和 finish 后立即追加 `dev-notes/ch05.md`，每段记录用户关键原话、关键产出/路径/评审结论、用户拒绝或纠偏、翻车与返工。不得在收尾时补写历史阶段。

最终交付：

- 裸循环与 LangGraph 演示命令；
- Docker、数据库和服务启动命令；
- 五项验收命令/操作；
- 自动测试、意图评估和真实浏览器结果；
- `dev-notes/ch05.md` 路径；
- 尚未完成或受外部服务限制的项目。

