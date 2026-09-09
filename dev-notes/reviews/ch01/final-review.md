# Task 2 与全分支最终审查

审查版本：基线 `b91dc1f`，HEAD `0587038`。结论：**Task 2 需要修复；整个分支暂不建议宣布最终就绪。** 未发现新的后端阻塞问题；阻塞点是评估器可以把违反协议的响应计为通过。

## Important findings

### [P2] 提取评测把漏字段当作显式 null，且忽略额外字段

- 位置：`ecommerce-support/scripts/evaluate.py:119–124`（核心为第 122 行）。
- `actual.get(field) == expected` 不能区分字段缺失与显式 null；响应仅检查是 dict，没有验证完整、禁止额外字段的公开 schema。
- 已用现有 `missing` 标注样例和 `httpx.MockTransport` 定向复现：HTTP 200 返回 `{"request_type":"unknown"}`，报告仍为 `total=1, passed=1, failed=0`。返回完整三字段并增加 `unexpected` 字段也全部通过。
- 影响：评测可以在 `order_id`、`expected_solution` 丢失或响应 schema 回归时继续给出全绿结果，不符合任务中的字段严格性与拒绝畸形成功要求。当前后端确有 Pydantic 防护，因此这是独立验收能力缺陷，不是已证实的线上提取故障。
- 修复：比较语义标注之前先严格校验且仅允许 `order_id`、`request_type`、`expected_solution` 三个必需字段及字段类型/枚举，保留显式 null 语义；加入漏字段、额外字段、错误类型的定向回归。

### [P2] SSE 解析器接受 EOF 处未结束的事件，掩盖截断响应

- 位置：`ecommerce-support/scripts/evaluate.py:35–37`、`ecommerce-support/scripts/evaluate.py:57`。
- `text.split("\n\n")` 的最后一个非空片段不保证拥有终止空行，但代码照常解析并加入 events。
- 定向复现：完整的 token 事件后附 `event: done\ndata: {"finish_reason":"stop"}`，没有事件终止空行，`_chat_text(parse_sse(...))` 返回 `hello`，没有报错。
- 影响：在最终事件尚未完整传输时连接结束，评测器仍可按正常 done 通过；这削弱了任务明确要求的缺失完成事件/畸形 SSE 验收。
- 修复：仅接受由空行终止的完整事件；EOF 若仍有非空事件残片则报解析失败，并覆盖完整 JSON 但缺终止空行的回归，保留 CRLF 与分片 UTF-8 支持。

## Task 2 合规与质量

- 已交付规定的 README、标注样例、HTTP 评测器、MockTransport 测试；6 个提取样例与两项聊天样例覆盖要求的标签和历史传回。
- 脚本默认访问本机 API，不读取模型配置或密钥；按标准记录结果和耗时，有非零失败退出码、不自动重试。
- README 给出锁定依赖安装、新安装才复制 `.env.example`、本机启动、两轮 curl、提取、测试和评测命令，并准确限定无状态、无订单操作、提供商兼容和字节估算。
- 任务报告包含具体 RED/GREEN 证据。没有把 MockTransport 通过冒充实时模型验证。
- **质量结论：上述两项修复后再通过 Task 2。** 小型文档补充：README 的事件语义可再说明 `finish_reason=length` 表示输出截断；这不单独构成阻塞。

## 全分支就绪性

- 范围符合获批设计：Python/FastAPI/LangChain；没有工具、Agent、检索、数据库或前端扩张。
- ChatOpenAI 显式使用 Settings 的 base URL、模型、密钥与有限 timeout，`max_retries=0`；提取使用 `with_structured_output` 的 json_mode/json_schema 配置，禁止 function_calling 与静默 fallback。
- 请求 schema 限制完整 user/assistant 对、内容长度与历史数量；预算在 SSE 开始前检查，按完整最旧轮次裁剪，并保留 system 与当前输入，不修改原历史。
- 后端直接转发上游增量，保留 finish reason；异常/空流/缺少结束元数据不发成功 done，公开错误文案不含原始异常正文。API 和 service 两层均有 upstream `aclose()`，取消异常继续传播；未发现对既有闭合修复的回归。
- AfterSales 的三个字段必需、nullable 字段没有默认值、extra forbidden；HTTP 模型解析异常映射脱敏 502。
- 锁文件为固定版本，README 命令与项目路径/入口一致。未发现秘密提交：`git ls-files '*env*'` 仅列 `.env.example`，`git check-ignore ecommerce-support/.env` 确认忽略；没有读取 `.env`。
- 读取实际 `evals/latest-results.json` 确认为 8/8，但受以上评测器盲点限制，只能保留为既有样例结果。控制器提供 43 tests passed 的证据，本审查未重复运行整套测试或实时模型请求。
- 审查时 `dev-notes/ch01.md` 有工作区修改，`evals/latest-results.json` 未跟踪；这些是待控制器完成的交付收尾，不在 HEAD `0587038` 内。最终提交需包含要求交付的合成评测结果并保留秘密忽略规则。

## 验证范围

读取设计、Task 2 brief/report、分支差异与实际 app/scripts/tests/README/锁文件；仅运行两个无外网、无配置读取的定向 Python 复现（提取 schema 误报及未终止 SSE）。未编辑源码、索引、分支或环境文件；仅写本报告。
