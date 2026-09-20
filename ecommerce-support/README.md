# 电商客服对话 API

这是一个无服务端会话状态的 FastAPI 示例：`/chat` 流式返回中文客服回复，`/after-sales/extract` 从用户描述中提取结构化售后诉求。服务只调用 `.env` 指定的 OpenAI 兼容模型，不查询订单、不执行退款，也不转接人工。

## 安装和启动

需要 Python 3.12 或更高版本。在项目目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
test -e .env || cp .env.example .env
```

最后一条命令只会在 `.env` 不存在时复制示例。首次安装后编辑 `.env`，填写实际的 `OPENAI_BASE_URL`、`OPENAI_MODEL` 和 `OPENAI_API_KEY`；不要提交该文件。

只监听本机地址启动服务：

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 调用示例

### 直接使用聊天网页

启动后用浏览器打开 **http://127.0.0.1:8000/**（或 `/chat`）。页面支持客服对话、流式回复、停止生成、新对话，以及售后提取和 JSON 查看。

浏览器会自动携带当前页面中的完整对话历史。刷新页面、切换功能或点击“新对话”会清空历史；失败、停止和截断的回复不加入后续上下文。历史不会存到浏览器本地存储。

GET `/chat` 返回网页，POST `/chat` 仍然是下面的流式 API。

第一轮对话使用 `curl -N` 立即显示 SSE 增量：

```bash
curl -N -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"我的订单号是 DEMO20260910005，衣服尺码不合适。","history":[]}'
```

服务不保存上一轮。第二轮必须把第一轮真实的 user 和 assistant 内容作为完整历史传回；下面的 assistant 文本只是可替换示例：

```bash
curl -N -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"我刚才说的订单号是什么？","history":[{"role":"user","content":"我的订单号是 DEMO20260910005，衣服尺码不合适。"},{"role":"assistant","content":"收到订单 DEMO20260910005。请问您希望换货还是退货？"}]}'
```

结构化售后提取：

```bash
curl -X POST http://127.0.0.1:8000/after-sales/extract \
  -H 'Content-Type: application/json' \
  -d '{"description":"订单 DEMO20260910001 的衣服太小了，我想换大一码。"}'
```

## 测试和标注评测

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/evaluate.py --base-url http://127.0.0.1:8000
```

评测器读取 `evals/cases.json`，把每项标准、耗时与通过状态写到 `evals/latest-results.json`。任一字段、子串、SSE 或 HTTP 标准失败时退出码为 1；请求只执行一次，不自动重试。样例均为合成数据。该结果只说明当前模型在这组样例上的表现，不代表所有输入均正确。

## 接口语义

`POST /chat` 接受 `message` 和完整的 `history`。历史只能由成对的 `user`、`assistant` 消息组成。成功流包含以下事件：

```text
event: token
data: {"text":"增量内容"}

event: done
data: {"finish_reason":"stop"}
```

`token` 是上游增量文本块，不保证对应一个 tokenizer token。若上游失败，服务发送 `error` 事件：

```text
event: error
data: {"code":"upstream_error","message":"模型服务暂时不可用，请稍后重试。"}
```

`error` 后流立即结束，不再发送 `done`；空流或缺少 `done` 也不算成功。`done` 的 `finish_reason=length` 表示输出因长度限制被截断，评测器会把它记为失败，只有 `stop` 算完整成功。开始流式响应前的请求校验失败返回 422，上下文预算超限返回 413。`POST /after-sales/extract` 成功返回且只返回 `order_id`、`request_type`、`expected_solution`，模型输出或解析失败返回脱敏的 502。

## 限制

- 服务端无数据库和会话存储；客户端必须保存并传回历史，重启服务也不会恢复会话。
- 服务无法核验订单状态或执行售后动作，因此不应把自然语言回复当作业务操作凭据。
- `CONTEXT_WINDOW_TOKENS` 必须按所选模型配置。当前预算使用保守的 UTF-8 字节估算，不是提供商原生 tokenizer 的精确计数。
- 模型端点必须兼容所用 OpenAI 接口；结构化提取默认要求 `json_mode`。提供商不支持流式输出或结构化模式时请求会失败，不会自动更换模型或降级。
- 上游模型和网络可能偶发失败；调用方应只在明确决定后发起新的重试请求。
