import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.knowledge.dedup import Deduplicator
from app.knowledge.embeddings import SiliconFlowEmbeddings


async def evaluate(dataset):
    raw = [json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]
    if raw[0].get("dataset_metadata") != {"synthetic_only": True, "contains_production_data": False}:
        raise ValueError("only an explicitly synthetic evaluation set may be sent upstream")
    settings = Settings()
    async with httpx.AsyncClient() as http:
        embeddings = SiliconFlowEmbeddings(http, settings)
        model = ChatOpenAI(
            base_url=settings.openai_base_url, model=settings.openai_model,
            api_key=settings.openai_api_key.get_secret_value(),
            max_tokens=settings.knowledge_extraction_max_tokens,
            timeout=settings.request_timeout, max_retries=0,
        )
        deduplicator = Deduplicator(model, embeddings)
        results = []
        for row in raw[1:]:
            decision = await deduplicator.decide(row["candidate"], row["existing"])
            results.append({
                "case": row["case"], "expected": row["expected"],
                "actual": decision.action, "target_id": decision.target_id,
                "reason": decision.reason, "passed": decision.action == row["expected"],
            })
    review_cases = [row for row in results if row["expected"] == "review"]
    return {
        "total": len(results), "passed": sum(row["passed"] for row in results),
        "incorrect_merges": sum(row["actual"] == "merge" for row in review_cases),
        "results": results,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evals/ch03/dedup.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = await evaluate(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("total", "passed", "incorrect_merges")}, ensure_ascii=False))
    if report["passed"] != report["total"] or report["incorrect_merges"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
