from dataclasses import dataclass

@dataclass(frozen=True)
class StreamEvent:
    event: str
    data: dict
