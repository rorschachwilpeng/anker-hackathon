# SDD ledger — plan: docs/superpowers/plans/2026-09-10-ch01.md

Baseline: b91dc1f; feature branch codex/ch01-customer-support. User-specified project directory retained.

| Check | Result |
|---|---|
| Task 1 internal | Tests cover config, budget, routes, service; contract consistent |
| Task 2 internal | SSE parser and evaluator tests match runner requirements |
| Task 1 -> Task 2 | Same endpoints, history pairs, event names, extraction fields |

Skill helper files lack execute permission; extracted briefs using Python without editing skill files.
Task 1: in_progress
Task 1 milestone: Context7 API lookup complete; dependencies installed under Python 3.14.4. FastAPI 0.141.1, langchain-core 1.6.2, langchain-openai 1.6.1, pydantic 2.13.5, pydantic-settings 2.15.0. No dependency blocker reported. Live raw json_mode probe passed; full application acceptance pending.
Task 1 RED evidence (implementer): initial missing-module collection failure; followed by importable minimal stubs, 27 failed / 1 passed on behavior (404 routes, empty context, missing budget validation, empty stream/extraction, disconnect). GREEN pending.
Controller live labeled extraction: six pre-authored cases all passed (exchange/refund/return_refund/logistics/unknown/phone-not-order). Evidence at /tmp/ch01-acceptance/initial-label-results.json. Task 1 reviewer still pending.
Task 1 review: two Important findings, spec FAIL / quality CHANGES REQUIRED. Fix round 1 dispatched to original backend agent: explicit nested upstream aclose + deferred Settings startup for clean-environment DI. Fix base b1abc6d (52ecfca only adds controller notes).
Task 1 fix round 1/5: 2 regressions failed before fixes; commit 5a9021a; fresh 30 passed. Scoped re-review dispatched to original reviewer, awaiting verdict. Restarted real API on 127.0.0.1:8000 using fixed version.
Task 1: complete (commits b91dc1f..5a9021a, review clean after fix round 1; spec PASS / quality APPROVED)
Task 2: in_progress; base 5a9021a.
Fixed-version live two-turn check passed: first 30 chunks, first content 4.542s, last 4.870s, done(stop); follow-up 6 chunks returned exact synthetic order id, done(stop). Evidence /tmp/ch01-acceptance/fixed-chat-results.json. Task 2 agent delivery active.
Task 2 implemented in 0587038. Independent controller suite 43 passed + pip check green. Delivered evaluator live 8/8 passed. Task-2 and whole-branch final review dispatched in one reviewer seat with separate verdicts requested; no final verdict yet.
Final review: two P2 evaluator false positives: missing/extra extraction fields accepted as valid; EOF residual done accepted without terminating SSE blank line. One fix wave sent to original delivery implementer, plus README length explanation. Fix base 0587038. Backend no new blocking findings.
Final re-review: both P2 ADDRESSED; Task 2 spec PASS / quality APPROVED; whole-branch review approved.
Task 2: complete (commits 5a9021a..e43bc6c, review clean after one final fix wave).
Final controller verification: 49 passed; pip check and compileall pass; strict live evaluation 8/8. Reports archived in dev-notes/reviews/ch01; local branch retained, no publish/push.
