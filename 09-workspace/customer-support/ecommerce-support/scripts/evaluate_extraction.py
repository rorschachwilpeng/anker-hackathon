import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_openai import ChatOpenAI

from app.config import Settings
from app.knowledge.extraction import QAExtractor, validate_evidence


async def evaluate(dataset: Path):
    settings = Settings()
    model = ChatOpenAI(
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        api_key=settings.openai_api_key.get_secret_value(),
        max_tokens=settings.knowledge_extraction_max_tokens,
        timeout=settings.request_timeout,
        max_retries=0,
    )
    extractor = QAExtractor(model)
    raw = [json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]
    metadata = raw[0].get("dataset_metadata", {}) if raw else {}
    if metadata != {"synthetic_only": True, "contains_production_data": False}:
        raise ValueError("only an explicitly synthetic evaluation set may be sent upstream")
    rows = [row for row in raw[1:] if "case" in row]
    results = []
    for row in rows:
        output = await extractor.extract(row["conversations"])
        evidence = {
            conversation["id"]: conversation["messages"]
            for conversation in row["conversations"]
        }
        valid = [item.model_dump() for item in output.items if validate_evidence(item, evidence)]
        results.append({
            "case": row["case"],
            "expected": row["expected"],
            "valid_count": len(valid),
            "passed": len(valid) == row["expected"],
            "valid_items": valid,
        })
    positive = [row for row in results if row["expected"]]
    negative = [row for row in results if not row["expected"]]
    return {
        "total": len(results),
        "passed": sum(row["passed"] for row in results),
        "positive_accuracy": sum(row["passed"] for row in positive) / len(positive),
        "safe_negative_accuracy": sum(row["passed"] for row in negative) / len(negative),
        "results": results,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evals/ch03/extraction.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = await evaluate(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("total", "passed", "positive_accuracy", "safe_negative_accuracy")}, ensure_ascii=False))
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
