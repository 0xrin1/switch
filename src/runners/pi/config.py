"""Pi runner configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

PI_THINKING_LEVELS = frozenset(
    {"off", "minimal", "low", "medium", "high", "xhigh"}
)


def resolve_thinking_level(reasoning_mode: str | None) -> str | None:
    """Map session reasoning_mode to Pi --thinking (override Pi settings default)."""
    mode = (reasoning_mode or "").strip().lower()
    if mode in PI_THINKING_LEVELS:
        return mode
    if mode in {"", "normal"}:
        default = (os.getenv("SWITCH_PI_DEFAULT_THINKING") or "medium").strip().lower()
        return default if default in PI_THINKING_LEVELS else "medium"
    return None


@dataclass(frozen=True)
class PiConfig:
    model: str | None = None
    provider: str | None = None
    thinking: str | None = None
    session_dir: str | None = None

    # Optional overrides (otherwise env defaults apply)
    pi_bin: str | None = None

    # System prompt to append via --append-system-prompt.
    # None = use default, "" = skip entirely.
    system_prompt: str | None = None

    # Extra text appended AFTER the resolved system prompt (default or
    # system_prompt override). None/empty = no change. Used for per-dispatcher
    # prompt additions (e.g. model-specific reasoning-strength hints).
    system_prompt_extra: str | None = None

    def resolve_bin(self) -> str:
        return self.pi_bin or os.getenv("PI_BIN", "pi")

    def resolve_provider(self) -> str | None:
        return self.provider or os.getenv("PI_PROVIDER") or None

    def resolve_model(self) -> str | None:
        return self.model or os.getenv("PI_MODEL") or None

    def resolve_session_dir(self) -> str | None:
        return self.session_dir or os.getenv("PI_SESSION_DIR") or None
