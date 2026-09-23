from dataclasses import dataclass, field
from typing import Literal


@dataclass
class S1ProState:
    confirmed_model: str | None = None
    symptom: str | None = None
    suggested_steps: list[str] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    feedback: str | None = None
    awaiting: Literal['product', 'feedback', 'ticket_confirmation'] | None = None
    ticket_no: str | None = None

    @classmethod
    def from_dict(cls, value):
        if not value:
            return cls()
        return cls(**{name: value[name] for name in cls.__dataclass_fields__ if name in value})
