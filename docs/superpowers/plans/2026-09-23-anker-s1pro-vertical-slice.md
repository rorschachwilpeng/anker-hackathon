# 安克 S1 Pro 售后垂直切片实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本项目只由现有 Technical Builder 执行代码；不要据此新建 Agent。

**Goal:** 在已验收的客服底座上，跑通「确认 eufy S1 Pro → 引用对应官方知识指导排障 → 记录反馈 → 用户确认后建单 → 客服查看」的单产品售后链路。

**Architecture:** 保留原电商 `/chat` 和回归用例，新增独立的 S1 Pro 服务路径，先把有型号、标题和 URL 的知识纳入现有 MySQL/Milvus 检索，再用持久化任务状态控制追问、检索、反馈和建单。模型负责理解用户表达与组织回复，程序负责型号、来源、确认和幂等门禁；不引入多 Agent 或完整订单系统。

**Tech Stack:** Python 3.12+、FastAPI、LangChain、SQLAlchemy/MySQL、Milvus、硅基流动 BGE-M3 嵌入、pytest。

## 先看这里：这份计划做成什么

这份计划要把已有的客服项目，改造成一个**可演示的 S1 Pro 售后咨询链路**。用户说“我的 S1 Pro 不吸了”，系统先确认是哪款产品，再查有出处的官方资料，给出一步排查建议并询问效果。如果问题解决，就确认结束；如果没解决，经用户同意创建工单，让客服看到此前的情况和已尝试步骤。

```text
用户求助 → 确认产品 → 查官方知识 → 指导一步并收集反馈
                                      ├─ 已解决 → 确认结果
                                      └─ 未解决 → 征得同意 → 建工单 → 客服查看
```

这里的“闭环”是**从咨询到自助解决，或到人工接手的交接闭环**；建单不等于故障已经解决，也不包含后续维修、退换和保修审批。本计划须在[底座迁入计划](2026-09-23-import-validated-support-base.md)验收后才能开始实施。

人来验收时，重点看四件事：产品没确认前不乱给专属排障建议；建议能看到正确的官方来源；用户说“好了”后不建单；用户未解决并明确同意后，工单只创建一次，客服能看到完整摘要。旧电商案例也必须继续可用。

## 交付顺序

| 阶段 | 做完后能看到什么 | 对应下方技术任务 |
| --- | --- | --- |
| 先验收旧底座 | 原客服项目仍能咨询、查知识、建工单 | 单独的底座迁入计划；未通过就不开始本计划 |
| 准备可信知识 | T2080 资料有型号和来源；检索不会混入其他型号或旧电商知识 | Task 1–3 |
| 让 Agent 推进服务 | 会追问产品、记住已做步骤、按反馈继续；建单前征得同意 | Task 4–5 |
| 完成可演示交接 | 用户页面能走完整条链，客服页面能查看工单，并有回归结果 | Task 6 |

下面是给 Technical Builder 执行和验收用的细化清单。第一次阅读只需看上面的结果和交付顺序；具体文件、字段与测试命令在下方查阅。

## 附录：技术执行清单（审阅后才派单）

### 全局约束

- 先完成并由用户验收 [底座迁入计划](2026-09-23-import-validated-support-base.md)。本计划的代码只在 `codex/anker-s1pro-implementation` 实施分支开发，不修改 `codex/customer-support-import` 原始基线。
- 本计划只覆盖 eufy Robot Vacuum Omni S1 Pro（T2080）的吸力变弱演示；同名产品未确认前不得输出型号专属排障。
- 只收录可重新打开并核对的 eufy 官方资料。网页打不开或型号、步骤无法核实，就停止对应知识入库并报告，不根据标题补写内容。
- 不删除旧电商知识或现有 MySQL/Milvus 数据；旧知识保留供底座回归，但不能进入 S1 Pro 答案。
- `.env`、密钥、用户未提交文件和运行日志不进入 Git；不执行 `docker compose down -v`。
- 每轮最多执行一个工具。需要多步时通过多轮会话推进；高风险操作由程序校验确认，不以提示词充当安全门禁。
- 本轮不做图片理解、订单/保修判断、混合检索、重排序、SLA、自动分派或完整人工接管。

### 代码边界与接口

迁入后，所有下列路径均相对于 `09-workspace/customer-support/ecommerce-support/`。旧 `ChatService`、`query_faq` 和 `/chat` 继续服务底座回归；新路径复用其数据库、知识导入和工具执行基础设施，不重写通用电商行为。

| 单元 | 文件 | 对外接口 / 责任 |
| --- | --- | --- |
| 知识元数据 | `app/knowledge/types.py`、`app/knowledge/models.py`、`scripts/migrate_s1pro.py` | `ChunkDraft` 增加 `product_model`、`source_title`、`source_url`；只增量迁移现有表 |
| 官方资料导入 | `knowledge/s1pro/`、`app/knowledge/ingestion.py`、`scripts/knowledge.py` | 用来源清单给每个 Markdown 片段附元数据；不改旧 `knowledge/demo` |
| 型号限定检索 | `app/knowledge/retrieval.py`、`app/tools/business.py` | `search_support_knowledge(query, confirmed_model)`；仅返回型号完全匹配、来源 URL 非空的片段 |
| 任务状态 | `app/models.py`、`app/repositories.py`、`app/s1pro/state.py` | `conversations.task_state` 保存产品、症状、步骤、反馈、待确认和工单号 |
| 服务编排 | `app/s1pro/flow.py`、`app/main.py`、`app/prompts.py` | 独立 `/s1pro/chat`：追问 → 检索 → 单步排障 → 反馈 → 确认转人工 |
| 工单与展示 | `app/repositories.py`、`app/static/chat.js`、`app/main.py` | 确认后幂等建单；本地客服页可查看工单列表和详情 |
| 评估 | `evals/s1pro-cases.json`、`scripts/evaluate_s1pro.py`、`tests/test_s1pro_flow.py`、`tests/test_s1pro_routes.py` | 覆盖主链路、歧义、无依据、失败、重复确认和旧知识隔离 |

### Task 1：给知识加型号和来源

只给现有知识记录增加三个可选字段，让新资料带上产品型号与出处；旧资料保留原样。这一步不处理对话记忆。

**Files:** `app/knowledge/types.py`、`app/knowledge/models.py`、`scripts/migrate_s1pro.py`、`tests/knowledge/test_s1pro_schema.py`。

**Interfaces:** 在既有 `ChunkDraft` 字段末尾依次新增 `product_model: str | None = None`、`source_title: str | None = None`、`source_url: str | None = None`。元数据字段不进入嵌入文本，修改它们不强制重算原有向量。

- [ ] **先写迁移失败用例。** 对已有库增加一条旧知识；迁移运行两次后，断言旧记录仍在，`knowledge_chunks` 新增 3 列，旧行的新字段为 `NULL`。用唯一测试标识，不清表。
- [ ] **运行失败测试。** 在项目目录执行 `.venv/bin/python -m pytest tests/knowledge/test_s1pro_schema.py -q`；预期因新字段/接口不存在而失败。
- [ ] **只做增量迁移。** `scripts/migrate_s1pro.py` 使用 SQLAlchemy inspector 先检查列是否存在，再分别执行 `ALTER TABLE knowledge_chunks ADD COLUMN product_model VARCHAR(64) NULL`、`ADD COLUMN source_title VARCHAR(255) NULL`、`ADD COLUMN source_url VARCHAR(1024) NULL`。每次只加缺失列；不得重建表或覆盖既有列。Task 4 再向同一脚本追加会话状态迁移。
- [ ] **同步 ORM 和草稿结构。** 在 `ChunkDraft` 末尾加入三个可空字段，在 SQLAlchemy `knowledge_chunks` 中加入同名列；`snapshot(row)` 和 `replace_source()` 继续使用 `ChunkDraft`/`asdict`，旧导入未提供元数据时为 `None`。
- [ ] **重跑测试并提交。** 新用例通过后再跑 `.venv/bin/python -m pytest -q`；提交只包含迁移与契约代码。若数据库测试因缺少 MySQL 而跳过，不能宣布数据库迁移验收通过，必须另在已启动的本地 MySQL 上运行一次。

### Task 2：收集并导入可核实的官方资料

把实际打开并核对过的官网资料整理成少量知识片段，保留产品型号、页面标题和链接；不是整站抓取或复制整篇网页。

**Files:** `knowledge/s1pro/sources.json`、`knowledge/s1pro/product.md`、`knowledge/s1pro/suction.md`、`knowledge/s1pro/handoff.md`、`app/knowledge/ingestion.py`、`scripts/knowledge.py`、`tests/knowledge/test_s1pro_ingestion.py`。

**Interfaces:** `sources.json` 以 Markdown 文件名为键，每条记录必须包含 `product_model`（固定为 `T2080`）、官网页面标题 `source_title`、实际打开过的完整 `https://service.eufy.com/` 地址 `source_url`、ISO 日期 `retrieved_on`。`import-dir knowledge/s1pro` 读取清单并把前三个字段写入每个 `ChunkDraft`；`retrieved_on` 保留在清单用于核验，不作为数据库字段。

- [ ] **先核对来源。** 在浏览器实际打开设计稿列出的 eufy 产品支持页、吸力变弱排查页和技术问题交接信息页，记录最终可访问 URL、页面标题、产品型号及采集日期。不能访问的页面不入库；将阻塞报告给用户，不用镜像站或记忆补全文字。
- [ ] **写最小语料。** 将已核实的产品身份写入 `product.md`、可操作排查步骤写入 `suction.md`、交接所需信息写入 `handoff.md`；按一个明确步骤或一个必要事实切片，保留原文链接，禁止复制整篇网页。`sources.json` 的键必须恰好为这三个文件名，不能遗漏或出现多余条目。
- [ ] **先写导入失败用例。** 构造两份测试 Markdown：一份 `T2080` 带有效来源，一份缺少 `source_url`；断言后一份在任何数据库写入前被拒绝，前一份的所有草稿都带同一来源和型号；旧 `knowledge/demo` 导入仍得到 `None` 元数据。
- [ ] **运行失败测试。** `.venv/bin/python -m pytest tests/knowledge/test_s1pro_ingestion.py -q`，预期新清单校验尚不存在而失败。
- [ ] **实现清单导入。** `import_directory()` 先解析全部 `.md` 及清单，校验 `product_model == "T2080"`、标题非空、URL 的 scheme/host 为 `https://service.eufy.com`，再用 `dataclasses.replace` 给 `split_markdown()` 的草稿补元数据，最后进入既有 `replace_source()`/`resume()`；不得为了新目录改动旧目录删除逻辑。
- [ ] **运行导入和回归。** `.venv/bin/python scripts/knowledge.py import-dir knowledge/s1pro`，随后 `.venv/bin/python scripts/knowledge.py status`；检查 MySQL 中 S1 Pro 行的型号、标题、URL 与 Milvus `done` 状态，确认旧电商行仍在。跑知识库测试后提交资料清单和导入代码。

### Task 3：只检索已确认型号的知识

用户确认 T2080 后，知识工具只返回这个型号且有来源的资料；没有可靠结果时明确说查不到。

**Files:** `app/knowledge/retrieval.py`、`app/tools/business.py`、`tests/knowledge/test_s1pro_retrieval.py`、`tests/test_s1pro_tools.py`。

**Interfaces:** `KnowledgeSearch.find_for_model(query: str, confirmed_model: str)` 是异步方法，返回 `list[dict]`；每项严格包含 `id`、`answer`、`product_model`、`source_title`、`source_url`。无匹配返回空列表，基础设施故障沿用 `KnowledgeError`。`build_s1pro_tools(knowledge_search)` 只注册异步工具 `search_support_knowledge(query: str, confirmed_model: str)`，其返回值含 `found`、`items`，且只允许 `confirmed_model == "T2080"`；旧 `build_tools()` 不变。

- [ ] **先写检索失败用例。** 同一向量候选列表同时放入 `T2080` 官方片段、另一个型号片段、无型号的旧电商片段和缺 URL 片段；断言只返回 `T2080` 且有 URL 的片段。再断言无匹配为 `[]`，过期哈希仍被拒绝，异常不会伪装成无命中。
- [ ] **运行失败测试。** `.venv/bin/python -m pytest tests/knowledge/test_s1pro_retrieval.py tests/test_s1pro_tools.py -q`，预期新接口不存在而失败。
- [ ] **实现限定检索。** 调用现有 embedding 和 Milvus search，候选数固定为 20；按分数阈值与哈希一致性加载 MySQL 行，再以 `row.draft.product_model == confirmed_model and row.draft.source_url` 做程序级过滤；只将必要字段打包在现有 2048 字节工具结果预算内。普通 `find()` 不变。
- [ ] **增加独立工具。** 用 Pydantic 参数模型定义 `query`（1–256 字）、`confirmed_model`（只接受 `T2080`）；工具调用 `find_for_model()`，无匹配回 `found: false`，失败沿用现有工具错误路径。`build_s1pro_tools()` 不暴露旧 `query_faq`、模拟订单或物流工具；建单由 Task 5 的确认门禁直接调用仓储。
- [ ] **通过测试并提交。** 跑新测试、原 `tests/knowledge/test_retrieval.py` 和 `tests/test_chat_tools.py`，全部通过后提交。向用户展示一个正确召回、一个跨型号被拒绝和一个无答案的工具结果。

### Task 4：保存排障进度（短期 Memory）

这一任务才处理对话记忆：保存已确认产品、症状、已试步骤、反馈和待确认事项，使同一会话重新打开后不必从头说起。

**Files:** `app/s1pro/__init__.py`、`app/s1pro/state.py`、`app/models.py`、`app/repositories.py`、`scripts/migrate_s1pro.py`、`tests/test_s1pro_state_repository.py`。

**Interfaces:** `conversations.task_state` 是可空 JSON，旧会话读作空状态。`S1ProState` 包含 `confirmed_model: str | None`、`symptom: str | None`、`suggested_steps: list[str]`、`completed_steps: list[str]`、`feedback: str | None`、`awaiting: Literal["product", "feedback", "ticket_confirmation"] | None`、`ticket_no: str | None`。仓储提供三个异步方法：`load_s1pro_state(conversation_id: int) -> S1ProState`、`save_s1pro_state(conversation_id: int, state: S1ProState) -> None`、`reserve_s1pro_ticket(conversation_id: int) -> str`。

- [ ] **先写迁移与持久化失败用例。** 对已有库增加一个测试会话；迁移运行两次后，断言旧会话仍在、`conversations` 新增 `task_state` 列且旧行为 `NULL`。新会话读到空状态；保存 `confirmed_model="T2080"` 和已试步骤后重新创建 Repository，仍可读回；同一会话两次预留工单号得到相同值；旧会话 `task_state IS NULL` 不报错。
- [ ] **运行失败测试。** `.venv/bin/python -m pytest tests/test_s1pro_state_repository.py -q`。
- [ ] **增量迁移会话状态。** 在 `scripts/migrate_s1pro.py` 中检查 `conversations.task_state` 是否存在，只在缺失时执行 `ALTER TABLE conversations ADD COLUMN task_state JSON NULL`；在 `app/models.py` 同步列定义，不重建会话表，不覆盖旧记录。
- [ ] **实现状态编码和仓储。** 状态只存用户已确认事实与服务进度，不把模型推断的产品候选写成已确认型号；JSON 只包含上述字段。`reserve_s1pro_ticket()` 在 `SELECT ... FOR UPDATE` 的事务中只在缺号时生成一次 `T` 加 UUID 前 30 位，并写回 `task_state`。建单失败后保留该号供同一请求重试。
- [ ] **通过测试并提交。** 跑仓储测试及原 `tests/test_repository.py`；实际 MySQL 中确认迁移后旧会话仍在，并检查同一会话的状态与工单号在新进程下可恢复。若数据库测试因缺少 MySQL 而跳过，不得宣布迁移验收通过。

### Task 5：让 Agent 追问、排障并在确认后建单

模型负责理解这轮话的含义，程序按已确认事实决定能走哪一步；尤其不能把模型的一句“用户同意了”当作建单授权。

**Files:** `app/s1pro/flow.py`、`app/s1pro/understanding.py`、`app/prompts.py`、`app/main.py`、`tests/test_s1pro_flow.py`。

**Interfaces:** `POST /s1pro/chat` 接收现有 `ChatRequest` 的 `message` 和可选 `conversation_id`，返回 `{conversation_id, action, reply, source, ticket_no}`；`source` 为空或 `{title, url}`，`ticket_no` 在建单成功前为空；`action` 限定为 `clarify_product`、`search_knowledge`、`ask_feedback`、`confirm_ticket`、`ticket_created`、`resolved`、`degraded`。旧 `/chat` 不变。

| 当前条件 | 允许的下一动作 | 禁止的动作 |
| --- | --- | --- |
| 只有“S1 Pro”等模糊名称 | `clarify_product` | 检索 T2080 步骤、建单 |
| 用户明确确认 eufy 扫地机器人 S1 Pro | `search_knowledge` | 使用无型号或无来源的知识 |
| 检索命中未尝试的官方步骤 | `ask_feedback`，一次只展示一步 | 直接宣称故障解决或保修成立 |
| 用户反馈已经恢复正常 | `resolved` | 创建工单 |
| 无可用步骤且尚未确认转人工 | `confirm_ticket` | 提前调用建单工具 |
| 上轮处于 `ticket_confirmation` 且本轮明确同意 | `ticket_created`；接口失败则 `degraded` | 未写库就声称建单成功 |

- [ ] **先写流程失败用例。** 用假模型和假知识工具覆盖：只说“S1 Pro 不吸了”先追问是否 eufy 扫地机器人；用户明确确认前知识工具调用次数为零；确认后只检索 `T2080`；有来源时只给一步并等待反馈；用户说“好了”结束且不建单；未解决而无下一步时询问是否转人工；“可以建单”在前一轮没有待确认状态时不能建单。
- [ ] **运行失败测试。** `.venv/bin/python -m pytest tests/test_s1pro_flow.py -q`。
- [ ] **实现理解层。** 模型用结构化输出提取 `product_candidate`、`symptom_candidate`、`feedback`、`ticket_consent`，并建议 `next_action`（`clarify`、`search`、`reply`、`handoff` 四选一）；若模型不支持结构化输出或结果无效，保持当前已确认状态不变，改为一个关键追问。模型输出只是候选，程序按状态和规则决定最终动作，不让模型直接调用写入工具。
- [ ] **实现程序级条件边。** 顺序固定为：读状态 → 理解本轮输入 → 检查等待字段及安全风险 → 需要确认产品则追问 → 已确认且需要排障则经 `ToolExecutor` 调用 `search_support_knowledge` → 有匹配则给一个未试步骤并保存 → 读反馈 → 已解决则结束，否则继续一步或请求转人工。`next_action` 可帮助选择当前允许的分支，但不得越过型号、来源和确认门禁。不存在来源时给出限制说明与人工选项，不让模型凭常识补步骤。
- [ ] **实现建单门禁。** 仅当 `awaiting == "ticket_confirmation"` 且本轮明确同意时，先用 `reserve_s1pro_ticket()` 固定号码，再调用现有 `create_ticket_once()`；工单描述按已确认产品、原始诉求、步骤、反馈、未解事项组成。成功后回写工单号；失败时 action 为 `degraded`，不得说“已提交”。重放同意消息只读回同一工单号。
- [ ] **通过测试并提交。** 跑流程、工具、仓储、原聊天路由测试。用两轮分开执行知识查询与建单，不改旧 ChatService 的单轮单工具约束。

### Task 6：客服查看与整条链路验收

用户端展示处理进度与来源；客服端至少能查看工单和交接摘要。用主案例和异常案例检验整条链路，而不只测单个函数。

**Files:** `app/main.py`、`app/repositories.py`、`app/static/chat.js`、`app/static/index.html`、`evals/s1pro-cases.json`、`scripts/evaluate_s1pro.py`、`tests/test_s1pro_routes.py`。

**Interfaces:** 本地演示页展示追问、步骤来源链接和建单状态；`GET /s1pro/tickets` 返回只读列表，`GET /s1pro/tickets/{ticket_no}` 返回详情，供本地 `/s1pro/workbench` 页面展示。详情从 `tickets.conversation_id` 找回会话消息及结构化状态。无鉴权的本地客服页不得公开部署。

- [ ] **先写路由失败用例。** 确认 `/s1pro/chat` 能延续相同 `conversation_id`；只读工单页对不存在编号返回 404；列表和详情显示工单号、产品、症状、步骤和用户反馈，不泄露 `.env` 或完整模型 Prompt。
- [ ] **运行失败测试。** `.venv/bin/python -m pytest tests/test_s1pro_routes.py -q`。
- [ ] **实现最小页面和查询。** 在现有页面增加 S1 Pro 演示入口，用单独请求路径提交；只展示服务动作、来源标题/URL 和“待确认/创建成功/创建失败”三种工单状态。客服侧提供本地只读列表和详情，不做接管、分派或状态修改。
- [ ] **写不少于 10 条评估样例。** 在 `evals/s1pro-cases.json` 中分别编号 `ambiguous_product`、`confirm_product`、`correct_source`、`legacy_knowledge_excluded`、`no_answer`、`retrieval_failure`、`feedback_resolved`、`feedback_unresolved`、`ticket_without_confirmation`、`ticket_duplicate_consent`。评估脚本逐条记录期望/实际动作、来源、工具次数和工单结果；任一阻断失败退出非零。
- [ ] **完成端到端验收。** 在迁入实例的独立端口运行 `.venv/bin/python -m pytest -q`、原 `scripts/evaluate.py` 及新 `scripts/evaluate_s1pro.py`。主案例用真实 MySQL/Milvus 连续跑 3 次且不跨型号；最终回归 10 次无阻断。展示一条消息 → 状态判断 → 知识检索 → 用户反馈 → 确认 → 工单记录的实际日志/数据库证据。提交代码、测试结果和数据来源说明；由用户决定是否合并到 `codex/product-agent-development`，不自动合并。

### 技术验收与停止线

| 检查点 | 必须看到的证据 | 不通过时的决定 |
| --- | --- | --- |
| 底座迁入验收 | 原项目在实施分支、8001、单测与[修订后的行为回归](2026-09-23-import-validated-support-base.md)通过；旧评估失败如实保留 | 停止 S1 Pro 功能开发，先修实际行为或环境问题 |
| 知识检索验收（Task 1–3） | 官方来源可访问；T2080 可召回；跨型号/旧知识被拒绝 | 不进入 Agent 排障；可缩为更少的已核实片段 |
| Agent/工单验收（Task 4–5） | 刷新后续接；无确认不建单；失败不假报成功 | 固定条件编排，减少模型可选动作，不扩大范围 |
| 最终演示（Task 6） | 主链路 3 次连续成功、10 次回归无阻断、来源与工单摘要准确 | 停止新增功能，只修阻断并保留录屏 |

本计划在底座迁入完成前供用户审阅，**不构成对 Builder 的第二阶段派单**。若迁入后的实际文件结构与以上基线不同，先修订计划中的精确路径和接口，再交接执行。
