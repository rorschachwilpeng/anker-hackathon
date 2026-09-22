# Task 1 code review

Reviewed `b91dc1f..b1abc6d` against `task-1-brief.md` and the chapter design. I inspected the implementation, tests, lockfile, task report, and supplied review diff. Per the review instruction, I did not rerun the already reported 28-test suite and did not read `.env`.

## Findings

### Important — closing the service stream does not close the nested model stream

**Location:** `ecommerce-support/app/service.py:82` (observable through `ecommerce-support/app/main.py:80-82`)

`CustomerSupportService.stream()` iterates directly over `self.model.astream(messages)` without retaining that inner iterator or closing it in a `finally` block. When the route is disconnected after a token has been emitted, `events()` closes the outer service generator. At that point the service generator is suspended at `yield TokenChunk(text)`. Closing an outer async generator in this state does not synchronously close an async iterator nested behind `async for`, so the real LangChain/OpenAI stream can remain alive and keep its HTTP connection and generation work running.

The disconnect test at `ecommerce-support/tests/test_api.py:185-238` proves only that the route closes a directly supplied fake generator. It does not exercise the production wrapper around `model.astream()`, so it cannot detect this leak.

Retain the model iterator, wrap iteration in `try/finally`, and call its `aclose()` when available. Add a service-level cancellation/close test with a closable fake model stream, ideally exercised through the ASGI disconnect path.

### Important — import-time settings loading defeats the specified dependency-injection interface

**Location:** `ecommerce-support/app/main.py:29` and `ecommerce-support/app/main.py:108`

Importing `app.main` immediately executes `app = create_app()`, and `create_app()` immediately executes `Settings()`. A clean checkout or CI process without a populated `.env` therefore cannot import `create_app` to inject a fake service. Even `create_app(service=fake)` loads settings before the lifespan branch checks that the injected service needs no model configuration. The current tests conceal this because they run in a workspace that already has configuration and also pass both `settings` and `service`.

This violates the task's explicit `create_app(settings=None, service=None)` test-dependency-injection contract. Defer default settings construction to the lifespan branch that creates the real service; an injected service should not require model credentials merely to construct or import the app.

## Assessment

**Specification compliance: FAIL.** The implementation satisfies the requested FastAPI/LangChain stack, explicit `ChatOpenAI` configuration, no-tool/no-Agent/no-fallback scope, pre-SSE validation, token/done/error framing, length propagation, strict structured extraction, sanitized 502 handling, and documented UTF-8 estimate. It does not reliably cancel the actual upstream iterator on disconnect, and the declared app-factory injection interface is not usable without external credentials.

**Code quality: CHANGES REQUIRED.** The separation among schemas, context budgeting, service integration, and HTTP framing is clear, and the existing tests are meaningful for validation, serialization, error framing, and outer-generator closure. The two lifecycle defects above are production-relevant and should be fixed before Task 1 is accepted.

The supplied report records 28 passing tests and live API evidence. Those results do not cover either finding above; no contradictory test evidence was found.
