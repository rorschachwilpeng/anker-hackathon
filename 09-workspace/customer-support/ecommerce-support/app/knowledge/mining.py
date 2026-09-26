import hashlib
import json

from app.knowledge.extraction import validate_evidence


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode())


def _turns(source):
    turns = []
    current = []
    for message in source["messages"]:
        if message.get("role") == "user" and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns


def _parts(source, byte_budget, skipped):
    if _size(source) <= byte_budget:
        return [source]
    parts = []
    current = []
    for turn in _turns(source):
        single = {**source, "messages": turn}
        if _size(single) > byte_budget:
            if current:
                parts.append({**source, "messages": current})
                current = []
            skipped.append({"source_id": source["id"], "reason": "turn_exceeds_byte_budget"})
            continue
        candidate = {**source, "messages": current + turn}
        if current and _size(candidate) > byte_budget:
            parts.append({**source, "messages": current})
            current = list(turn)
        else:
            current += turn
    if current:
        parts.append({**source, "messages": current})
    return parts


def build_batches(sources, byte_budget=60_000, *, skipped=None):
    skipped = skipped if skipped is not None else []
    batches = []
    current = []
    for source in sources:
        for part in _parts(source, byte_budget, skipped):
            candidate = current + [part]
            if current and (len(candidate) > 20 or _size(candidate) > byte_budget):
                batches.append(current)
                current = [part]
            else:
                current = candidate
    if current:
        batches.append(current)
    return batches


def _batch_no(batch):
    material = json.dumps(batch, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def _json_safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


class MiningService:
    def __init__(self, repository, extractor, *, dedup_service=None, ingestion=None, byte_budget=60_000):
        self.repository = repository
        self.extractor = extractor
        self.dedup_service = dedup_service
        self.ingestion = ingestion
        self.byte_budget = byte_budget

    async def extract_all(self, run_id):
        sources = await self.repository.completed_sources()
        processed = await self.repository.processed_fingerprints()
        changed = [source for source in sources if processed.get(source["id"]) != source["fingerprint"]]
        skipped = []
        batches = build_batches(changed, self.byte_budget, skipped=skipped)
        plan = [
            {
                "batch_no": _batch_no(batch),
                "source_ids": list(dict.fromkeys(part["id"] for part in batch)),
                "items": _json_safe(batch),
            }
            for batch in batches
        ]
        active = await self.repository.prepare_mining_run(run_id, {
            "run_id": run_id, "status": "planned", "batches": plan,
            "sources": _json_safe(changed), "skipped": skipped,
        })
        plan = active["batches"]
        batches = [item["items"] for item in plan]
        changed = active["sources"]
        skipped = active.get("skipped", [])
        completed = await self.repository.completed_mining_batches([item["batch_no"] for item in plan])
        completed_ids = set(completed)
        original = {source["id"]: source["messages"] for source in changed}
        accepted_count = sum(int(payload.get("accepted", 0)) for payload in completed.values()) if isinstance(completed, dict) else 0
        rejected_count = sum(int(payload.get("rejected", 0)) for payload in completed.values()) if isinstance(completed, dict) else 0
        for batch, batch_plan in zip(batches, plan, strict=True):
            if batch_plan["batch_no"] in completed_ids:
                continue
            result = await self.extractor.extract(batch)
            await self.repository.assert_writer_owned()
            accepted = []
            rejected = []
            for item in result.items:
                payload = item.model_dump()
                if validate_evidence(payload, original):
                    accepted.append(payload)
                else:
                    rejected.append(payload)
            await self.repository.commit_mining_batch(
                run_id, batch_plan["batch_no"], accepted, rejected
            )
            accepted_count += len(accepted)
            rejected_count += len(rejected)
        await self.repository.finalize_mining_run(run_id, changed, skipped=skipped)
        return {
            "scanned": len(sources), "staged": accepted_count,
            "rejected": rejected_count, "skipped": len(skipped),
        }

    async def run(self, run_id):
        if self.dedup_service is None or self.ingestion is None:
            raise RuntimeError("mining pipeline is incomplete")
        extraction = await self.extract_all(run_id)
        dedup = await self.dedup_service.run()
        vectorization = await self.ingestion.resume()
        return {"run_id": run_id, "extraction": extraction, "dedup": dedup, "vectorization": vectorization}
