"""Ports (interfaces) for runner implementations.

The rest of the system (bots, lifecycle, commands) should depend on these
contracts rather than concrete runner implementations.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol


RunnerEvent = tuple[str, object]


class Runner(Protocol):
    """A streaming runner (engine adapter)."""

    def run(self, prompt: str, session_id: str | None = None) -> AsyncIterator[RunnerEvent]:
        ...

    def cancel(self) -> None:
        ...
