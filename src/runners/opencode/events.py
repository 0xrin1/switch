"""OpenCode event normalization helpers."""

from __future__ import annotations


def extract_session_id(payload: dict) -> str | None:
    for key in ("sessionID", "sessionId", "session_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    props = payload.get("properties")
    if isinstance(props, dict):
        for key in ("sessionID", "sessionId", "session_id"):
            value = props.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def coerce_event(payload: dict) -> dict | None:
    if "type" in payload and "part" in payload:
        return payload

    event_type = payload.get("type")
    if not isinstance(event_type, str):
        return None

    props = payload.get("properties") if isinstance(payload.get("properties"), dict) else None

    if props:
        if event_type in {"question.asked", "question"}:
            return {"type": "question.asked", **props}
        if event_type in {"permission.requested", "session.permission.requested"}:
            return {"type": "permission.requested", **props}
        if "part" in props and isinstance(props["part"], dict):
            part = props["part"]
            part_type = part.get("type")
            if part_type == "text":
                return {"type": "text", "part": {"text": part.get("text", "")}}
            if part_type in {"tool", "tool_use"}:
                return {"type": "tool_use", "part": part}
            if part_type in {"question", "question.asked"}:
                merged = {"type": "question.asked"}
                merged.update(part)
                return merged
        if event_type in {"error", "session.error"}:
            return {"type": "error", **props}

    if event_type in {"step_start", "step_finish", "text", "tool_use", "error"}:
        return payload

    return None
