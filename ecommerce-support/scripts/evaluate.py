#!/usr/bin/env python3
"""Run labeled evaluations against the local ecommerce support API."""

from __future__ import annotations

import argparse
import codecs
import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx


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


def evaluate_cases(cases: dict[str, Any], client: httpx.Client) -> dict[str, Any]:
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
                    sessions = [p["conversation_id"] for e, p in events if e == "session"]
                    if sessions:
                        conversation_id = sessions[-1]
                for criterion in _contains_criteria(turn.get("expect", {}), answer):
                    criterion["name"] = f"turn {turn_number}: {criterion['name']}"
                    result["criteria"].append(criterion)
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
