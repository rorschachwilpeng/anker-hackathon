"""Budget complete source chunks and place strongest evidence at both edges."""
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class Evidence:
    n: int
    chunk_id: int
    section_path: str | None
    question: str
    answer: str


def pack_evidence(rows, byte_budget):
    picked, used = [], 0
    for ranked in rows[:10]:
        row = ranked.row
        item = {'chunk_id':row.id,'section_path':row.draft.section_path,
                'question':row.draft.questions,'answer':row.draft.answer}
        size = len(json.dumps(item,ensure_ascii=False).encode())
        if used+size <= byte_budget:
            picked.append(row)
            used += size
    front = picked[::2]
    back = list(reversed(picked[1::2]))
    return [Evidence(i,row.id,row.draft.section_path,row.draft.questions,row.draft.answer)
            for i,row in enumerate([*front,*back],1)]
