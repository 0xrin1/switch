"""Shared helpers for tool event logging.

Runners differ in their native event formats, but the UI expects consistent tool
markers like:
  [tool:bash <preview>]

This module centralizes:
- redaction of sensitive keys
- formatting of tool input previews
- env-based gating of tool-input logging
"""

from __future__ import annotations

import json
import os


_REDACT_KEYS = ("key", "token", "secret", "password", "auth", "cookie")


def should_log_tool_input() -> bool:
    return os.getenv("SWITCH_LOG_TOOL_INPUT", "").lower() in {"1", "true", "yes"}


def tool_input_max_len() -> int:
    return int(os.getenv("SWITCH_LOG_TOOL_INPUT_MAX", "2000"))


def redact_tool_input(obj: object) -> object:
    if isinstance(obj, dict):
        out: dict[object, object] = {}
        for k, v in obj.items():
            ks = str(k).lower()
            if any(rk in ks for rk in _REDACT_KEYS):
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_tool_input(v)
        return out
    if isinstance(obj, list):
        return [redact_tool_input(x) for x in obj]
    return obj


def format_tool_input_preview(tool: str, raw_input: object) -> str | None:
    """Return a short, human-readable tool input preview (redacted if needed)."""

    if raw_input is None:
        return None

    if tool == "bash" and isinstance(raw_input, dict):
        cmd = raw_input.get("command")
        if isinstance(cmd, str) and cmd.strip():
            return cmd.strip()

    if tool in {"read", "write", "edit"} and isinstance(raw_input, dict):
        fp = raw_input.get("filePath") or raw_input.get("file_path")
        if isinstance(fp, str) and fp:
            return fp

    if tool == "grep" and isinstance(raw_input, dict):
        pat = raw_input.get("pattern")
        inc = raw_input.get("include")
        if isinstance(pat, str) and pat:
            suffix = f" include={inc!r}" if isinstance(inc, str) and inc else ""
            return f"pattern={pat!r}" + suffix

    redacted = redact_tool_input(raw_input)
    return json.dumps(redacted, ensure_ascii=True, sort_keys=True, default=str)
