# Task 1 focused re-review

Reviewed fix commit `5a9021a` only for the two findings in `task-1-review.md` and direct regressions introduced by their fixes. I inspected `task-1-fix-review.diff`, the appended task report, the affected production code, and the two regression tests. Per instruction, I did not rerun the 30-test suite and did not read `.env`.

## Finding 1: nested model stream remains open on disconnect

**Status: ADDRESSED**

`ecommerce-support/app/service.py:81-98` now retains the iterator returned by `model.astream(messages)` and closes it in a `finally` block. Therefore closing `CustomerSupportService.stream()` while it is suspended after yielding a token propagates closure to the nested LangChain/model stream.

`ecommerce-support/tests/test_service.py:79-106` is a meaningful regression test: it advances the production service generator to its first token, closes that generator at the vulnerable suspension point, and verifies that the inner model generator's `finally` ran. Together with the existing ASGI disconnect test, this covers both links in the cancellation chain.

No direct regression was found in normal completion or failure handling. Closing an already exhausted async generator is idempotent, and the `finally` also covers mid-stream exceptions.

## Finding 2: import-time settings loading defeats service injection

**Status: ADDRESSED**

`ecommerce-support/app/main.py:29-42` now constructs default settings only inside the lifespan branch that creates a real `CustomerSupportService`. Module-level `app = create_app()` no longer resolves credentials during import, and an injected service takes the earlier branch before `Settings()` is called.

`ecommerce-support/tests/test_api.py:65-85` meaningfully reproduces a credential-free checkout by copying the application package to a temporary project root without `.env`, removing the three model environment variables, and importing/constructing the injected app in a subprocess. Code inspection confirms the same branch remains credential-free when lifespan starts.

No direct regression was found in the default uvicorn path: without an injected service, settings are still validated during application startup before the real model service is constructed.

## Assessment

**Specification compliance: PASS.** Both previously failing requirements are now satisfied: client disconnect closure reaches the actual nested upstream iterator, and the declared application factory supports credential-free service injection while retaining validated settings for normal startup.

**Code quality: APPROVED.** The fixes are localized, explicit, and backed by regression tests at the correct abstraction boundaries. No new breakage was identified within the requested scope.

The appended report records the two focused tests failing before the fixes and the full suite passing afterward with `30 passed`; this re-review accepted that evidence without rerunning it.
