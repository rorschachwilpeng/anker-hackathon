import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge.embeddings import valid_vector
from app.knowledge.types import KnowledgeError
from app.knowledge.ingestion import InjectedFailure
from app.knowledge.models import knowledge_ingestion_state
from sqlalchemy import delete
from scripts.knowledge import runtime


def require_vector(vector):
    if not valid_vector(vector):
        raise KnowledgeError("embedding_invalid_response")
    return vector


def judge_case(case, hits, threshold):
    accepted = [hit for hit in hits if hit["score"] >= threshold]
    expected = case.get("expected")
    matching = [hit for hit in accepted if expected and expected in hit["answer"]]
    passed = not accepted if expected is None else bool(matching)
    return {
        "case_id": case["case_id"], "query": case["query"], "expected": expected,
        "actual_source_ids": [hit["id"] for hit in accepted],
        "expected_source_id": matching[0]["id"] if matching else None,
        "evidence": matching[0]["answer"] if matching else None,
        "http_status": 200, "passed": bool(passed),
    }


def choose_threshold(rows):
    scores = sorted({hit["score"] for _, hits in rows for hit in hits})
    candidates = [-1.0] + [min(1.0, score + 1e-6) for score in scores] + [1.0]
    ranked = []
    for threshold in candidates:
        results = [judge_case({"case_id": str(index), "query": "", **case}, hits, threshold) for index, (case, hits) in enumerate(rows)]
        false_positive = sum(case.get("expected") is None and not result["passed"] for (case, _), result in zip(rows, results, strict=True))
        recall = sum(case.get("expected") is not None and result["passed"] for (case, _), result in zip(rows, results, strict=True))
        ranked.append((false_positive, -recall, threshold))
    false_positive, negative_recall, threshold = min(ranked)
    if false_positive or -negative_recall == 0:
        raise KnowledgeError("no_acceptable_threshold")
    return threshold


def validate_recovery_report(report):
    points = report.get("failpoints", {})
    for name in ("after-mysql", "after-milvus"):
        row = points.get(name, {})
        if not row.get("triggered") or row.get("pending_after_resume") != 0 or row.get("unique_ids") is not True:
            return False
    return True


async def recovery_point(name):
    suffix = uuid.uuid4().hex
    namespace = "test_ch03_" + suffix
    collection = "test_ch03_" + suffix
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recovery.md"
        marker = "synthetic-recovery-" + suffix
        path.write_text(f"# 恢复测试\n\n## 唯一条款\n\n{marker} 可在中断后恢复。\n")
        triggered = False
        ids = []
        async with runtime(collection=collection, namespace=namespace, failpoint=name) as service:
            async with service.state.writer_lock():
                await service.repo.ensure_config()
                await service.vectors.ensure_collection()
                try:
                    await service.import_document(path)
                    await service.resume()
                except InjectedFailure:
                    triggered = True
                before = await service.repo.status()
                entries = await service.state.entries("source:")
                ids = [ident for _, payload in entries for ident in payload.get("ids", [])]
        async with runtime(collection=collection, namespace=namespace) as service:
            try:
                async with service.state.writer_lock():
                    await service.repo.ensure_config()
                    await service.vectors.ensure_collection()
                    after = await service.resume()
                    vector = require_vector((await service.embeddings.encode([marker]))[0])
                    hits = await service.vectors.search(vector, 5)
                    matching = [hit for hit in hits if hit.id in ids]
                    await service.repo.replace_source("doc:" + str(path.resolve()), [])
                    await service.vectors.delete(ids)
                    await service.repo.clear_tombstones(ids)
                await service.vectors.client.drop_collection(collection_name=collection, timeout=10)
                async with service.repo.sessions.begin() as session:
                    await session.execute(delete(knowledge_ingestion_state).where(
                        knowledge_ingestion_state.c.state_key.like(namespace + ":%")
                    ))
            finally:
                pass
        return {
            "triggered": triggered,
            "pending_before_resume": before["pending"],
            "pending_after_resume": after["pending"],
            "unique_ids": len(ids) == len(set(ids)) == 1 and len(matching) == 1,
        }


async def fresh_recovery():
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "failpoints": {
            "after-mysql": await recovery_point("after-mysql"),
            "after-milvus": await recovery_point("after-milvus"),
        },
    }
    report["passed"] = validate_recovery_report(report)
    return report


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def raw_results(service, cases):
    output = []
    for case in cases:
        vector = require_vector((await service.embeddings.encode([case["query"]]))[0])
        hits = await service.vectors.search(vector, 5)
        rows = await service.repo.load_done([hit.id for hit in hits])
        output.append((case, [
            {"id": hit.id, "score": hit.score, "answer": rows[hit.id].draft.answer}
            for hit in hits if hit.id in rows and rows[hit.id].content_hash == hit.content_hash
        ]))
    return output


async def retrieval(calibration_path, holdout_path):
    async with runtime() as service:
        await service.vectors.ensure_collection()
        calibration = await raw_results(service, load_jsonl(calibration_path))
        threshold = choose_threshold(calibration)
        holdout = await raw_results(service, load_jsonl(holdout_path))
        results = [judge_case(case, hits, threshold) for case, hits in holdout]
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": service.embeddings.settings.embedding_model,
            "config_fingerprint": service.repo.fingerprint,
            "threshold": threshold, "top_k": 5,
            "passed": all(row["passed"] for row in results), "cases": results,
        }


async def execute(args):
    if args.mode == "retrieval":
        return await retrieval(args.calibrate, args.holdout)
    if args.mode == "extraction":
        from scripts.evaluate_extraction import evaluate
        report = await evaluate(Path("evals/ch03/extraction.jsonl"))
        report["passed"] = report["passed"] == report["total"]
        return report
    if args.mode == "dedup":
        from scripts.evaluate_dedup import evaluate
        report = await evaluate(Path("evals/ch03/dedup.jsonl"))
        report["passed"] = report["passed"] == report["total"] and report["incorrect_merges"] == 0
        return report
    if args.mode == "recovery":
        return await fresh_recovery()
    if args.mode == "chat":
        report = await retrieval(Path("evals/ch03/calibration.jsonl"), Path("evals/ch03/holdout.jsonl"))
        case = next(row for row in report["cases"] if row["case_id"] == "h01")
        return {"timestamp": report["timestamp"], "passed": case["passed"], "case": case}
    raise ValueError(args.mode)


def parser():
    value = argparse.ArgumentParser()
    value.add_argument("mode", choices=["retrieval", "extraction", "dedup", "recovery", "chat"])
    value.add_argument("--calibrate", type=Path, default=Path("evals/ch03/calibration.jsonl"))
    value.add_argument("--holdout", type=Path, default=Path("evals/ch03/holdout.jsonl"))
    value.add_argument("--output", type=Path)
    return value


if __name__ == "__main__":
    args = parser().parse_args()
    report = asyncio.run(execute(args))
    output = args.output or Path("../dev-notes/evals/ch03") / f"{args.mode}-task9.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"mode": args.mode, "passed": report.get("passed"), "output": str(output)}, ensure_ascii=False))
    if report.get("passed") is False:
        raise SystemExit(1)
