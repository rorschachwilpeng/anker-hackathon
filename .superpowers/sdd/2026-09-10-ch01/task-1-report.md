# Task 1 report: backend streaming and extraction

## Scope

Implemented only the assigned `ecommerce-support/` backend files: validated settings, request and response schemas, prompt templates, conservative context trimming, LangChain service integration, FastAPI routes, dependency metadata and behavioral tests. The user's `.env` was neither printed nor changed. Existing `dev-notes/ch01.md` changes were left untouched and excluded from this task's commit.

## Context7 and installed API findings

- Checked current LangChain model, streaming and structured-output docs; FastAPI `StreamingResponse` cancellation/lifespan docs; and Pydantic Settings dotenv and validation docs before library implementation.
- Installed versions include FastAPI 0.141.1, langchain-core 1.6.2, langchain-openai 1.6.1, OpenAI 3.11.0, Pydantic 2.13.5 and pydantic-settings 2.15.0. `requirements.lock` is the exact `pip freeze --exclude-editable` result.
- `ChatOpenAI(max_tokens=64, ...)` is accepted by the installed LangChain signature; its OpenAI HTTP serialization uses the current field name `max_completion_tokens`. `stream_usage=False` omits stream usage extras. `with_structured_output(AfterSales, method="json_mode")` serializes `response_format={"type":"json_object"}` and returns a Pydantic-validated model.

## RED evidence

First run before any app module existed:

```text
$ .venv/bin/python -m pytest -q
ERROR tests/test_api.py - ModuleNotFoundError: No module named 'app'
ERROR tests/test_context.py - ModuleNotFoundError: No module named 'app'
ERROR tests/test_service.py - ModuleNotFoundError: No module named 'app'
3 errors during collection
```

Second run against minimal importable stubs, before behavior was implemented:

```text
$ .venv/bin/python -m pytest -q
27 failed, 1 passed, 1 warning in 1.74s
```

The failures reached behavioral assertions: missing routes returned 404, context trimming returned an empty list, invalid budgets were accepted, model serialization/extraction produced no results, and disconnect did not close the upstream iterator.

## GREEN evidence

After minimal implementation and HTTP-boundary corrections:

```text
$ .venv/bin/python -m pytest -q
28 passed in 0.50s
```

The 28 collected cases cover ASCII and Chinese UTF-8 boundaries, whole-pair trimming, immutable input, malformed histories and size limits, pre-stream 413 responses, JSON-safe multiline SSE, token order, `length` finish metadata, error/no-done behavior for failures and incomplete streams, iterator close on disconnect, strict extraction fields, sanitized 502 responses, configuration validation, and real LangChain serialization/Pydantic parsing over an `httpx.MockTransport` boundary.

## Live diagnostic evidence and concerns

- One configured chat attempt returned HTTP 200 but yielded zero service events and was correctly converted to the generic SSE error. The raw SSE body for that failed occurrence was not captured, so the cause remains unresolved: it could be an empty or reasoning-only upstream stream, or fields discarded by the SDK parser. No provider attribution is claimed.
- A later raw LangChain probe showed many parsed empty chunks, then content chunks and `finish_reason=stop`. A full FastAPI probe then returned token events followed by exactly one `done(stop)`. The controller separately observed a successful first chat, a successful second chat with history, and successful structured extraction.
- The installed `langchain-openai` emits a one-time warning on this machine because macOS system proxy configuration is detected while it builds an unused synchronous client with socket keepalive options. All application paths are asynchronous and use the lifespan-owned `httpx.AsyncClient`; live requests completed. If synchronous model methods are added later, the sync client/proxy behavior should be configured explicitly.
- Automatic retries remain disabled as required. An intermittent empty parsed stream therefore becomes a sanitized error rather than a fabricated success or silent fallback.

## Review fixes

Files: `app/service.py`, `app/main.py`, `tests/test_service.py`, `tests/test_api.py`.
Commit: `fix: 关闭模型流并延迟加载配置`.

```text
$ .venv/bin/python -m pytest -q tests/test_service.py::test_closing_service_stream_closes_model_stream tests/test_api.py::test_module_import_and_service_injection_do_not_require_environment
FF
2 failed in 0.95s
```

```text
$ .venv/bin/python -m pytest -q
30 passed in 1.40s
$ .venv/bin/python -m compileall -q app tests
# exit 0
```
