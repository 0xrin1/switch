"""Ports (interfaces) for runner implementations.

The rest of the system (bots, lifecycle, commands) should depend on these
contracts rather than concrete runner implementations.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


RunnerEvent = tuple[str, object]


@runtime_checkable
class Runner(Protocol):
    """A streaming runner (engine adapter)."""

    async def run(self, prompt: str, session_id: str | None = None, **kwargs) -> AsyncIterator[RunnerEvent]:
        ...

    def cancel(self) -> None:
        ...
