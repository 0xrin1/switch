"""Shared OpenCode data structures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from src.runners.ports import RunnerEvent as Event


@dataclass
class Question:
    """A question from the AI to the user."""

    request_id: str
    questions: list[dict]  # [{header, question, options: [{label, description}]}]


# Type for question callback: receives Question, returns answers array
# Each answer is a list of selected option labels (one per question, positional)
QuestionCallback = Callable[[Question], Awaitable[list[list[str]]]]

