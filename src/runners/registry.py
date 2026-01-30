"""Runner registry.

This provides a single place to map an engine name to its concrete runner
implementation. Callers should depend on the `Runner` port.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.runners.ports import Runner


def create_runner(
    engine: str,
    *,
    working_dir: str,
    output_dir: Path,
    session_name: str | None = None,
    **kwargs: Any,
) -> Runner:
    engine = (engine or "").strip().lower()

    if engine == "claude":
        from src.runners.claude.runner import ClaudeRunner

        if kwargs:
            # Keep the surface area explicit; pass-through kwargs make it too easy
            # to accidentally couple callers to a specific runner.
            raise TypeError(f"ClaudeRunner does not accept extra args: {sorted(kwargs.keys())}")
        runner = ClaudeRunner(working_dir, output_dir, session_name)
        if not isinstance(runner, Runner):
            raise TypeError("Claude runner does not satisfy Runner port")
        return runner

    if engine == "opencode":
        from src.runners.opencode.runner import OpenCodeRunner

        runner = OpenCodeRunner(working_dir, output_dir, session_name, **kwargs)
        if not isinstance(runner, Runner):
            raise TypeError("OpenCode runner does not satisfy Runner port")
        return runner

    raise ValueError(f"Unknown engine: {engine}")
