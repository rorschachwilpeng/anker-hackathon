import hashlib
from app.knowledge.types import ChunkDraft


def embedding_text(draft: ChunkDraft) -> str:
    return f'category: {draft.category}\nquestions: {draft.questions}\nanswer: {draft.answer}'


def content_hash(draft: ChunkDraft) -> str:
    return hashlib.sha256(embedding_text(draft).encode('utf-8')).hexdigest()
