from dataclasses import dataclass
from typing import Protocol

DIMENSIONS = 1024

@dataclass(frozen=True)
class ChunkDraft:
    category: str
    questions: str
    answer: str
    section_path: str | None = None
    content_type: str | None = None
    is_key_clause: bool = False

@dataclass(frozen=True)
class ChunkSnapshot:
    id: int
    draft: ChunkDraft
    content_hash: str

@dataclass(frozen=True)
class Hit:
    id: int
    score: float
    content_hash: str

class KnowledgeError(RuntimeError):
    def __init__(self, code: str, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(code)

class EmbeddingError(KnowledgeError):
    pass

class VectorError(KnowledgeError):
    pass

class EmbeddingPort(Protocol):
    async def encode(self, texts: list[str]) -> list[list[float]]: ...

class VectorPort(Protocol):
    async def ensure_collection(self) -> None: ...
    async def upsert(self, rows: list[tuple[ChunkSnapshot, list[float]]]) -> list[int]: ...
    async def search(self, vector: list[float], top_k: int) -> list[Hit]: ...
    async def delete(self, ids: list[int]) -> None: ...
