# SDD ledger — plan: docs/superpowers/plans/2026-09-20-ch02.md

User authorized local implementation. Baseline: 49 passed.
Ruling: work in existing local checkout per user request; no additional worktree — preserves running page and .env — concurrent unrelated edits would require isolation.
Pre-flight: repository produces dict messages; chat converts to LangChain messages; tool results use StreamEvent. Tasks 1→2→3 sequential; UI consumes documented SSE.
Task 1: in progress
Task 1: complete (real MySQL schema initialized; repository 5 passing)
Task 2: complete (tool tests passing; upstream echo_status selection and final streaming passed)
Task 3: complete (chat and route tests passed; review fixes verified)
Task 4: complete (browser logistics, FAQ hit and expected miss with badges passed; console clean)
Ruling: SQLAlchemy Core Table + AsyncSession mapping — fixed stack satisfied without ORM entity lifecycle — additional ORM behavior would need explicit implementation.
Ruling: independent test database grant rejected by auto-review — no bypass; unique test rows in local demo database — database retains test records.
Final: fixed repeated close lock race — test_old_turn_close_cannot_release_new_turn_lock RED→GREEN.
Final: fixed long ticket false failure — test_long_ticket_description_returns_compact_success RED→GREEN.
Final: fixed tool response budgeting — test_tool_result_budget_reserved_before_accepting_long_input RED→GREEN.
Final: fixed default/auto increment/on update validation — test_wrong_default_and_missing_on_update_rejected RED→GREEN; live initialization passed.
Task 5: in progress; live model evaluation first run 6/7, ticket protocol failure under investigation.

Ruling: added answer_reset SSE for provisional text before a tool call — preserve streaming and avoid mixed-stream failure — custom consumers must implement reset.
Task 5: complete (72 tests pass; live ch02 7/7; first 6/7 preserved).
Final integration: prior chapter regression 7/8 exposed unexpected final tool call. Explicit tool_choice=none added without tool definitions; regression RED→GREEN, suite 73 passed. Both live evaluations rerunning.

Final verified: pytest 73 passed; live ch02 7/7; ch01 automated7/8 with documented negated-phrase false positive. Browser acceptance passed including 371px no overflow and abort recovery. Keep local branch per user request.
