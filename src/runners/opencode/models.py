"""Shared OpenCode data structures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class OpenCodeResult:
    """Final result from an OpenCode run."""

    text: str
    session_id: str | None
    cost: float
    tokens_in: int
    tokens_out: int
    tokens_reasoning: int
    tokens_cache_read: int
    tokens_cache_write: int
    duration_s: float
    tool_count: int


@dataclass
class Question:
    """A question from the AI to the user."""

    request_id: str
    questions: list[dict]  # [{header, question, options: [{label, description}]}]


Event = tuple[str, str | OpenCodeResult | Question]

# Type for question callback: receives Question, returns answers dict
QuestionCallback = Callable[[Question], Awaitable[dict[str, list[str]]]]
