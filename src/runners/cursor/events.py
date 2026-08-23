"""Cursor ACP event helpers."""

from __future__ import annotations

from typing import Any


def extract_session_id(payload: dict[str, Any]) -> str | None:
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    session_id = params.get("sessionId")
    if session_id is None:
        return None
    return str(session_id)


def agent_capabilities(initialize_result: object) -> dict[str, Any]:
    if not isinstance(initialize_result, dict):
        return {}
    caps = initialize_result.get("agentCapabilities")
    return caps if isinstance(caps, dict) else {}


def supports_session_resume(capabilities: dict[str, Any]) -> bool:
    session_caps = capabilities.get("sessionCapabilities")
    return isinstance(session_caps, dict) and "resume" in session_caps
