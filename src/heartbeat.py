"""Heartbeat brain pointer + /heartbeat command parsing.

The watchdog (~/switch/scripts/heartbeat.py) re-reads
~/switch/state/heartbeat-session every cycle. /heartbeat writes that
file so retargeting needs no .env edit and no service restart.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.ralph import parse_ralph_command

DEFAULT_WAIT_MINUTES = 30.0
_DEFAULT_PROMPT_FILE = Path.home() / "switch" / "prompts" / "heartbeat-cycle.md"


def heartbeat_prompt_file() -> Path:
    override = os.environ.get("HEARTBEAT_PROMPT_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PROMPT_FILE


def heartbeat_prompt() -> str:
    return (
        f"Read {heartbeat_prompt_file()} "
        "and follow it exactly, every cycle."
    )


_STATE_DIR = Path.home() / "switch" / "state"
_POINTER_NAME = "heartbeat-session"


def heartbeat_session_path() -> Path:
    override = os.environ.get("HEARTBEAT_SESSION_FILE", "").strip()
    if override:
        return Path(override)
    return _STATE_DIR / _POINTER_NAME


def read_heartbeat_session(path: Path | None = None) -> str:
    """Session name the watchdog should watch. Pointer file, then env."""
    target = path or heartbeat_session_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        raw = ""
    name = (raw.splitlines()[0].strip() if raw else "")
    if name:
        return name
    return os.environ.get("HEARTBEAT_SESSION", "").strip()


def write_heartbeat_session(name: str, path: Path | None = None) -> None:
    """Atomically record which session is the heartbeat brain."""
    session = name.strip()
    if not session:
        raise ValueError("heartbeat session name is empty")
    target = path or heartbeat_session_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(session + "\n", encoding="utf-8")
    os.replace(tmp, target)


def clear_heartbeat_session(path: Path | None = None) -> bool:
    """Drop the pointer so the watchdog goes idle. True if a file was removed."""
    target = path or heartbeat_session_path()
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False


def _has_wait_flag(rest: str) -> bool:
    low = f" {rest.lower()} "
    markers = (" --wait", " —wait", " –wait", " −wait", " -w ", " -w=", " --wait=")
    return any(m in low for m in markers) or rest.lower().strip() in {"-w", "--wait"}


def parse_heartbeat_command(body: str) -> dict | None:
    """Parse /heartbeat the same way as /ralph, with heartbeat defaults.

    No args → cycle prompt + --wait 30.
    Flags only (e.g. `--wait 15`) → cycle prompt + those flags.
    Prompt without --wait → same prompt, --wait 30.
    """
    raw = body.strip()
    if not raw.lower().startswith("/heartbeat"):
        return None
    rest = raw[len("/heartbeat") :].strip()
    if rest.startswith("-") and not rest.startswith("--") and not rest.startswith("-w"):
        # /heartbeat-status and friends are other commands.
        return None

    prompt = heartbeat_prompt()
    if not rest:
        rest = f"{prompt} --wait {DEFAULT_WAIT_MINUTES:g}"
    elif not _has_wait_flag(rest):
        rest = f"{rest} --wait {DEFAULT_WAIT_MINUTES:g}"

    parsed = parse_ralph_command(f"/ralph {rest}")
    if parsed is not None:
        return parsed
    return parse_ralph_command(f"/ralph {prompt} {rest}")
