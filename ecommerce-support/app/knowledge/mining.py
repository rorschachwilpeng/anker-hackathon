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


def _parts(source, byte_budget):
    if _size(source) <= byte_budget:
        return [source]
    parts = []
    current = []
    for turn in _turns(source):
        candidate = {**source, "messages": current + turn}
        if current and _size(candidate) > byte_budget:
            parts.append({**source, "messages": current})
            current = list(turn)
        else:
            current += turn
    if current:
        parts.append({**source, "messages": current})
    return parts


def build_batches(sources, byte_budget=60_000):
    batches = []
    current = []
    for source in sources:
        for part in _parts(source, byte_budget):
            candidate = current + [part]
            if current and (len(candidate) > 20 or _size(candidate) > byte_budget):
                batches.append(current)
                current = [part]
            else:
                current = candidate
    if current:
        batches.append(current)
    return batches


class MiningService:
    def __init__(self, repository, extractor, *, byte_budget=60_000):
        self.repository = repository
        self.extractor = extractor
        self.byte_budget = byte_budget

    async def extract_all(self, run_id):
        sources = await self.repository.completed_sources()
        processed = await self.repository.processed_fingerprints()
        changed = [source for source in sources if processed.get(source["id"]) != source["fingerprint"]]
        batches = build_batches(changed, self.byte_budget)
        plan = [
            {
                "batch_no": hashlib.sha256(f"{run_id}:{index}".encode()).hexdigest()[:32],
                "source_ids": list(dict.fromkeys(part["id"] for part in batch)),
            }
            for index, batch in enumerate(batches, 1)
        ]
        await self.repository.prepare_mining_run(run_id, plan)
        original = {source["id"]: source["messages"] for source in changed}
        accepted = []
        rejected = []
        for batch in batches:
            result = await self.extractor.extract(batch)
            for item in result.items:
                payload = item.model_dump()
                if validate_evidence(payload, original):
                    accepted.append(payload)
                else:
                    rejected.append(payload)
        await self.repository.commit_mining_run(run_id, accepted, rejected, changed)
        return {"scanned": len(sources), "staged": len(accepted), "rejected": len(rejected)}

