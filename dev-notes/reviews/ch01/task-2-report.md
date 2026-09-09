# Task 2 实施报告

## 交付

- `ecommerce-support/evals/cases.json`：6 个售后提取标注样例，覆盖 exchange、refund、return_refund、logistics、缺失信息与手机号非订单号；另含两轮订单号回忆和不得宣称退款完成的聊天约束。
- `ecommerce-support/scripts/evaluate.py`：使用 `httpx.Client` 逐次调用本地 API，解析分片 UTF-8 SSE，检查精确字段、允许子串与禁止子串，记录每例 criteria、latency_ms 和错误；任一失败返回非零且不自动重试。
- `ecommerce-support/tests/test_evaluate.py`：使用 `httpx.MockTransport` 覆盖解析、两轮历史、提取标准、失败报告、CLI 退出码和无重试行为。
- `ecommerce-support/README.md`：中文安装、启动、curl、测试/评测命令，以及接口事件、错误语义和兼容性限制。

## TDD 证据

RED 1：首次运行 `.venv/bin/python -m pytest -q tests/test_evaluate.py`，收集阶段因 `ModuleNotFoundError: No module named 'scripts'` 失败，证明解析器尚未实现。加入最小 SSE 解析器后，4 个解析测试通过。

RED 2：加入错误事件与缺失 done 的评测测试后，收集阶段因 `ImportError: cannot import name 'evaluate_cases'` 失败。加入最小聊天评测路径后，6 个测试通过。

RED 3：加入 CLI 失败报告测试后，`AttributeError: module 'scripts.evaluate' has no attribute 'main'`；加入 CLI、JSON 报告写入和退出码后，9 个测试通过。

RED 4：畸形 done 数据 `{}` 被错误判为成功，期望 failed=1 而实际 failed=0。校验非空 `finish_reason` 后，聚焦测试共 13 个通过。

## 文档核对

实现前通过 Context7 解析官方库 `/encode/httpx`，核对了 `httpx.Client`、`MockTransport`、`stream()/iter_bytes()`、timeout 与客户端资源关闭用法。项目沿用锁文件中的 httpx 0.28.1，没有增加依赖。

## 边界

未修改 `app/`、`.env`、根目录 `dev-notes/`，未停止或调整控制器运行的本地 API。真实评测结果由控制器写入 `evals/latest-results.json`，本任务不覆盖或提交该生成文件。

## 最终审查修复

RED 5：新增 6 个审查回归断言后，聚焦命令得到 6 failed、5 passed。具体复现为：末尾未用空行终止的 done 被解析为完整事件，`finish_reason=length` 被判成功，以及缺字段、多字段、错误 nullable 字段类型、非法 request_type 的结构化响应绕过契约验证。

GREEN 5：SSE 解析器只派发以空行完整终止的事件，非空 EOF 残段报错；聊天评测只把 `finish_reason=stop` 视为完整成功；提取响应先严格验证三个必需字段、禁止额外字段、nullable 文本类型与 request_type 枚举，再比较标注。聚焦审查测试结果为 11 passed、8 deselected。
