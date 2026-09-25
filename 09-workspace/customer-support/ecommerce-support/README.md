# 电商智能客服 · 第 3 章

FastAPI + SQLAlchemy + Docker MySQL/Milvus + LangChain。`query_faq` 已从 SQL LIKE 升级为硅基流动 BGE-M3 dense 向量检索；MySQL 保存权威原文，Milvus 保存向量。聊天页仍由模型自主选择一次业务工具，显示工具徽章并流式生成最终回答。

## 启动

需要 Python 3.12+ 和已启动的 Docker Desktop。当前项目已配置并启动，可直接打开 http://127.0.0.1:8000/ 。全新安装：

```bash
cd "/Users/qingyili/Documents/ChatGPT/AI客服/ecommerce-support"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
test -e .env || cp .env.example .env
```

填写 `.env` 中模型地址、模型名、密钥，以及随机的 MySQL root/应用密码；`DATABASE_URL` 的密码与 `MYSQL_PASSWORD` 一致。不要覆盖已填写的 `.env` 或提交密钥。新卷首次初始化才使用 MySQL 环境变量，修改密码不会自动修改既有数据库账户。

```bash
docker compose up -d mysql etcd minio milvus
.venv/bin/python scripts/init_db.py
.venv/bin/python scripts/init_knowledge.py
.venv/bin/python scripts/knowledge.py import-dir knowledge/demo
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

MySQL 8.4 仅暴露 `127.0.0.1:3307`，使用项目命名卷。初始化从 `sql/ch02-schema.sql` 与 `sql/ch04-orders-schema.sql` 创建缺失表，检查既有结构并幂等灌入演示数据，不删除已有数据。结构不兼容会拒绝初始化。启动应用前先初始化数据库。不要通过 `docker compose down -v` 清理持久数据。

## 浏览器演示

打开 http://127.0.0.1:8000/ ，分别点击“新对话”后输入：

1. **订单 DEMO-1001 的物流到哪了**：显示固定演示订单卡片；点击“查看订单详情”查看同一记录与完整物流时间线。其他订单号明确返回未找到，不生成替代数据。
2. **退货政策是什么**：显示“查询常见问题”，从知识库召回七天退货政策及例外。
3. **邮费是多少**：显示“查询常见问题”，语义召回“订单实付满99元包邮，未满99元收取8元运费”及偏远/大件例外。

也可以问“商品 P1001 的价格和库存”，或明确要求创建咨询工单。商品价格和库存仍为随机 mock；订单 DEMO-1001 与物流是固定演示记录；工单会实际写入本地 tickets 表。

本页自动保留当前 conversation_id。新对话、刷新和切换功能会开始新的页面会话，已存数据库记录保留；本章没有历史列表。停止生成不撤销已提交的工单。不同 HTTP 请求不自动去重，避免对已经成功的工单重复提问。

## curl 与多轮上下文

```bash
curl -N http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"订单 1001 的物流到哪了"}'
```

从首个 `session` 事件读取 conversation_id，下一轮填入实际 id（以下 1 仅为示例）：

```bash
curl -N http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"我刚才问的是哪个订单号？","conversation_id":1}'
curl -N http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' -d '{"message":"退货政策是什么"}'
curl -N http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' -d '{"message":"邮费是多少"}'
curl http://127.0.0.1:8000/after-sales/extract \
  -H 'Content-Type: application/json' \
  -d '{"description":"演示订单 DEMO1001 的衣服太小了，我想换大一码。"}'
```

非空旧 `history` 返回 422 迁移提示；空数组兼容。服务端加载数据库历史，不相信客户端工具流水。

## 事件与调用边界

```text
event: session
data: {"conversation_id":1}

event: tool_status
data: {"name":"query_logistics","tool_call_id":"call_x","status":"running","attempt":1}

event: tool_status
data: {"name":"query_logistics","tool_call_id":"call_x","status":"succeeded","attempt":1}

event: token
data: {"text":"模拟数据："}

event: done
data: {"finish_reason":"stop"}
```

若模型先输出引导语再发工具申请，会发送 `answer_reset` 清掉临时文本，再显示工具后的最终回答；消费者须重置本轮累计正文。

每次用户提问最多实际执行一个工具。若模型申请多个，第一个执行，其余保存 skipped 配对结果；回灌请求不开放工具，并显式设置 tool_choice=none，不能进入 Agent Loop。参数错误和未知工具不执行；暂时故障可在总 deadline 剩余时间内重试一次，单次超时耗尽预算后不重启完整超时。工单在同一次执行内复用 ticket_no，主键冲突须核对内容；创建工单与会话转人工同事务提交。

## 知识库维护

```bash
# 文档导入、断点续跑和状态
.venv/bin/python scripts/knowledge.py import-dir knowledge/demo
.venv/bin/python scripts/knowledge.py import-dir knowledge/s1pro
.venv/bin/python scripts/knowledge.py resume
.venv/bin/python scripts/knowledge.py status

# 明确批准 FAQ 表可发送到云端嵌入服务后再执行
.venv/bin/python scripts/knowledge.py import-faq

# 历史会话挖掘；会把已结束会话发送给配置的对话模型
.venv/bin/python scripts/knowledge.py mine --run-id manual-001
.venv/bin/python scripts/knowledge.py worker --enabled

# 嵌入模型、维度或拼接协议变化后全量重嵌入
.venv/bin/python scripts/knowledge.py reembed-all
```

建库先写 MySQL `pending`，再 upsert Milvus 并回填 `done`；中断后运行 `resume` 补齐。重复导入按来源主键幂等。删除 Markdown 后重新执行 `import-dir` 会删除该来源的 MySQL chunk，并在 resume 中清理对应向量；不会清空 `qa_extraction_staging`。本机只运行数据库和任务进程，BGE-M3 推理由硅基流动云端完成，本机不加载嵌入模型。

历史会话挖掘会从模型上下文窗口中扣除结构化输出、margin 和 Prompt/Schema 预算，只提交完整轮次；不可拆的超限轮次会记录为 skipped，同会话其他短轮次继续处理。每个模型批次成功后立即事务提交 checkpoint；使用相同 `run-id` 重跑会复用首次扫描快照并跳过已提交批次，运行期间新增的消息留到下一次任务处理。

阈值来自 `evals/ch03/calibration.jsonl`，当前值记录在 `.env.example`；知识内容、嵌入模型或拼接协议变化后必须重新校准。统一验收命令：

```bash
.venv/bin/python scripts/evaluate_ch03.py retrieval --calibrate evals/ch03/calibration.jsonl --holdout evals/ch03/holdout.jsonl
.venv/bin/python scripts/evaluate_ch03.py extraction
.venv/bin/python scripts/evaluate_ch03.py dedup
.venv/bin/python scripts/evaluate_ch03.py recovery
.venv/bin/python scripts/evaluate_ch03.py chat
```

最终正文只在完整生成并保存后发送 done(stop)。length 是截断，正文不存为完整历史；error 后不发 done。已保存的 user/工具流水仍保留，不完整轮次不回放。异常详情只记录脱敏类别。token 指上游实际文本增量块，并不保证一帧恰好一个 tokenizer token。

HTTP 校验 422、预算超限 413、会话缺失 404、同会话繁忙 409、流前数据库不可用 503。售后提取继续通过 with_structured_output 返回 order_id/request_type/expected_solution。

## 测试与评估

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
.venv/bin/python scripts/evaluate_ch02.py
.venv/bin/python scripts/evaluate.py --base-url http://127.0.0.1:8000
```

数据库测试需要运行中的已初始化 MySQL。当前使用唯一 `test-` 用户与随机工单号写入本地演示库，不清表；不应用到生产库。独立测试库授权步骤被环境自动审批拒绝，未更改权限。缺少 DATABASE_URL 时数据库测试跳过，不能把这种运行宣称为 MySQL 验收。

第 2 章标注集在 `evals/ch02-cases.json`，报告为 `evals/ch02-results.json`。评估检查 SSE、模型选择的工具和参数、数据库调用/结果配对、最终正文落库、模拟物流答案与结果一致、工单真实存在及两轮回忆。任一失败退出非零，不自动重试。报告只证明这些合成样例，不保证任意输入下的模型行为。

## 边界

- 仅本机单用户、单 worker 演示，无登录鉴权、跨进程会话锁或真实人工工作台；不可直接公开部署。
- 本章仍没有 Agent Loop、关键词混合召回或 rerank；知识检索只有 BGE-M3 dense 单路，订单/商品/物流仍是 mock。
- UTF-8 字节保守估计预算，覆盖提示、工具定义、参数与返回预留，历史按完整轮次裁剪。不是模型原生 tokenizer 精确计数；长输入可能提前返回 413。
- 模型须支持 OpenAI 兼容工具协议、流式输出和 JSON 模式；不兼容不会自动换模型。
- 表结构没有消息完成状态字段，中断原因在运行日志，数据库不完整轮次通过消息结构识别。
- 过程记录：仓库 `dev-notes/ch03.md`；设计与计划在 `docs/superpowers/`。
