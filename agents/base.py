from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.backends import LLMBackend


@dataclass(slots=True)
class AgentNote:
    summary: str
    details: list[str] = field(default_factory=list)


class BaseAgent:
    name = "base"

    def __init__(self, backend: LLMBackend | None = None) -> None:
        self.backend = backend
