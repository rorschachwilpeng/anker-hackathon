#!/usr/bin/env python3
"""Run labeled evaluations against the local ecommerce support API."""

from __future__ import annotations

import argparse
import re
import codecs
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url

from app.config import Settings
from app.models import messages, tickets


class SSEParseError(ValueError):
    """Raised when an SSE response does not match the documented event shape."""


def _decode_chunks(source: str | bytes | Iterable[bytes]) -> str:
    if isinstance(source, str):
        return source
    chunks = [source] if isinstance(source, bytes) else source
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        return "".join(decoder.decode(chunk) for chunk in chunks) + decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise SSEParseError("response is not valid UTF-8") from exc


def parse_sse(source: str | bytes | Iterable[bytes]) -> list[tuple[str, dict[str, Any]]]:
    """Parse complete SSE events from text or fragmented byte chunks."""

    text = _decode_chunks(source).replace("\r\n", "\n").replace("\r", "\n")
    events: list[tuple[str, dict[str, Any]]] = []
    blocks = text.split("\n\n")
    if blocks.pop().strip():
        raise SSEParseError("unterminated event at end of stream")
    for block in blocks:
        if not block.strip():
            continue
        event_name: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").lstrip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())
        if not event_name or not data_lines:
            raise SSEParseError("event and data fields are required")
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError as exc:
            raise SSEParseError("event data is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise SSEParseError("event data must be a JSON object")
        events.append((event_name, payload))
    return events


def _chat_text(events: list[tuple[str, dict[str, Any]]]) -> str:
    tokens: list[str] = []
    done_count = 0
    for event, payload in events:
        if done_count:
            raise SSEParseError("event received after done")
        if event == "error":
            raise SSEParseError(str(payload.get("code", "error")))
        if event == "token":
            text = payload.get("text")
            if not isinstance(text, str):
                raise SSEParseError("token text must be a string")
            tokens.append(text)
        elif event == "done":
            if not isinstance(payload.get("finish_reason"), str) or not payload["finish_reason"]:
                raise SSEParseError("done finish_reason must be a non-empty string")
            if payload["finish_reason"] != "stop":
                raise SSEParseError(f"finish_reason {payload['finish_reason']} is not a complete success")
            done_count += 1
        elif event == "answer_reset":
            tokens.clear()
        elif event in ("session", "tool_status"):
            continue
        else:
            raise SSEParseError(f"unknown event: {event}")
    if done_count != 1:
        raise SSEParseError("missing done" if done_count == 0 else "multiple done events")
    answer = "".join(tokens)
    if not answer:
        raise SSEParseError("empty successful response")
    return answer


def _contains_criteria(expect: dict[str, Any], text: str) -> list[dict[str, Any]]:
    criteria = [
        {"name": f"contains {item!r}", "passed": item in text}
        for item in expect.get("contains", [])
    ]
    allowed = expect.get("contains_any", [])
    if allowed:
        criteria.append({"name": "contains an allowed substring", "passed": any(item in text for item in allowed)})
    criteria.extend(
        {"name": f"excludes {item!r}", "passed": item not in text}
        for item in expect.get("not_contains", [])
    )
    return criteria


def load_conversation_trace(conversation_id: int) -> dict[str, list[dict[str, Any]]]:
    settings = Settings()
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required for behavior evaluation")
    url = make_url(settings.database_url.get_secret_value()).set(drivername="mysql+pymysql")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            saved_messages = connection.execute(
                select(messages.c.role, messages.c.content, messages.c.tool_calls, messages.c.tool_call_id)
                .where(messages.c.conversation_id == conversation_id)
                .order_by(messages.c.id)
            ).mappings().all()
            saved_tickets = connection.execute(
                select(tickets.c.ticket_no).where(tickets.c.conversation_id == conversation_id)
            ).mappings().all()
        return {"messages": [dict(row) for row in saved_messages], "tickets": [dict(row) for row in saved_tickets]}
    finally:
        engine.dispose()


def behavior_criteria(behavior: dict[str, Any], answers: list[str], trace: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    saved = trace["messages"]
    calls = [call for row in saved for call in (row.get("tool_calls") or [])]
    names = [call.get("function", {}).get("name") for call in calls]
    criteria = []
    order_id = behavior.get("persisted_user_contains")
    if order_id:
        criteria.append({"name": "first user message persisted with order ID", "passed": bool(saved and saved[0]["role"] == "user" and order_id in (saved[0].get("content") or ""))})
        criteria.append({"name": "next turn recalls persisted order ID", "passed": len(answers) > 1 and order_id in answers[1]})
    if behavior.get("refuses_refund_without_side_effect"):
        answer = answers[-1]
        refusal = bool(re.search(r"(?:无法|不能|不可以|无权|没有.{0,8}权限).{0,18}(?:退款|操作退款)", answer, re.S))
        criteria.append({"name": "refund request explicitly refused", "passed": refusal})
        criteria.append({"name": "no refund or ticket tool executed", "passed": not any(name and ("refund" in name or name == "create_ticket") for name in names)})
        criteria.append({"name": "no ticket side effect", "passed": not trace["tickets"]})
    if behavior.get("ticket_created_in_database"):
        ticket_calls = [call for call in calls if call.get("function", {}).get("name") == "create_ticket"]
        results = {}
        for row in saved:
            if row.get("role") == "tool" and row.get("tool_call_id"):
                try:
                    results[row["tool_call_id"]] = json.loads(row.get("content") or "")
                except json.JSONDecodeError:
                    pass
        matching = [result.get("data", {}).get("ticket_no") for call in ticket_calls if (result := results.get(call.get("id"), {})).get("ok")]
        recorded = {row["ticket_no"] for row in trace["tickets"]}
        criteria.append({"name": "create_ticket tool succeeded", "passed": len(ticket_calls) == 1 and len(matching) == 1})
        criteria.append({"name": "tool ticket exists in database and answer", "passed": len(matching) == 1 and matching[0] in recorded and matching[0] in answers[-1]})
    return criteria


def _validate_extraction_schema(actual: Any) -> dict[str, Any]:
    fields = {"order_id", "request_type", "expected_solution"}
    request_types = {"refund", "return_refund", "exchange", "repair", "logistics", "other", "unknown"}
    if not isinstance(actual, dict) or set(actual) != fields:
        raise ValueError("extraction schema requires exactly three documented fields")
    if not all(actual[field] is None or type(actual[field]) is str for field in ("order_id", "expected_solution")):
        raise ValueError("extraction schema nullable text fields must be strings or null")
    if actual["request_type"] not in request_types:
        raise ValueError("extraction schema request_type is invalid")
    return actual


def evaluate_cases(cases: dict[str, Any], client: httpx.Client, load_trace=load_conversation_trace) -> dict[str, Any]:
    """Evaluate cases once each; failures are data in the returned report."""

    results: list[dict[str, Any]] = []
    for case in cases.get("extraction_cases", []):
        started = time.perf_counter()
        result: dict[str, Any] = {
            "id": case["id"],
            "kind": "extraction",
            "passed": False,
            "criteria": [],
        }
        try:
            response = client.post("/after-sales/extract", json={"description": case["description"]})
            response.raise_for_status()
            actual = _validate_extraction_schema(response.json())
            result["criteria"] = [
                {"name": f"{field} equals expected", "passed": actual.get(field) == expected}
                for field, expected in case["expected"].items()
            ]
            solution = actual.get("expected_solution")
            solution_text = solution if isinstance(solution, str) else ""
            result["criteria"].extend(
                _contains_criteria(
                    {
                        "contains": case.get("solution_contains", []),
                        "contains_any": case.get("solution_contains_any", []),
                    },
                    solution_text,
                )
            )
            result["passed"] = all(item["passed"] for item in result["criteria"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        results.append(result)

    for case in cases.get("chat_cases", []):
        started = time.perf_counter()
        result = {"id": case["id"], "kind": "chat", "passed": False, "criteria": []}
        conversation_id = None
        answers: list[str] = []
        try:
            for turn_number, turn in enumerate(case["turns"], start=1):
                with client.stream(
                    "POST",
                    "/chat",
                    json={"message": turn["message"], "conversation_id": conversation_id},
                ) as response:
                    response.raise_for_status()
                    events = parse_sse(response.iter_bytes())
                    answer = _chat_text(events)
                    answers.append(answer)
                    sessions = [p["conversation_id"] for e, p in events if e == "session"]
                    if sessions:
                        conversation_id = sessions[-1]
                for criterion in _contains_criteria(turn.get("expect", {}), answer):
                    criterion["name"] = f"turn {turn_number}: {criterion['name']}"
                    result["criteria"].append(criterion)
            if case.get("behavior"):
                if conversation_id is None:
                    raise ValueError("behavior evaluation requires a session ID")
                result["criteria"].extend(behavior_criteria(case["behavior"], answers, load_trace(conversation_id)))
            result["passed"] = all(item["passed"] for item in result["criteria"])
        except (httpx.HTTPError, SSEParseError, ValueError, KeyError, TypeError) as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        results.append(result)
    passed = sum(item["passed"] for item in results)
    return {
        "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed},
        "results": results,
    }


def exit_code_for_report(report: dict[str, Any]) -> int:
    return 1 if report["summary"]["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    project_dir = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=Path, default=project_dir / "evals/cases.json")
    parser.add_argument("--output", type=Path, default=project_dir / "evals/latest-results.json")
    args = parser.parse_args(argv)

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    timeout = httpx.Timeout(60.0, connect=5.0)
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=timeout) as client:
        report = evaluate_cases(cases, client)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = report["summary"]
    print(f"evaluated={summary['total']} passed={summary['passed']} failed={summary['failed']}")
    print(f"report={args.output}")
    return exit_code_for_report(report)


if __name__ == "__main__":
    raise SystemExit(main())
