"""GlitchTip error reporting via sentry-sdk.

SWITCH_GLITCHTIP_DSN set → ERROR logs become events (logger.exception
includes traceback); WARNING and below become breadcrumbs.
Unset → no-op. No per-call-site instrumentation.
"""

from __future__ import annotations

import logging
import os
import subprocess

log = logging.getLogger("switch.telemetry")

_initialized = False


def _git_sha() -> str | None:
    """Short git SHA of the switch checkout, used as the release tag."""
    repo = os.path.expanduser("~/switch")
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def init_telemetry() -> None:
    """Initialize GlitchTip reporting. No-op without SWITCH_GLITCHTIP_DSN.

    Call once, as early as possible (before DB init) so that even
    infrastructure failures are captured.
    """
    global _initialized
    if _initialized:
        return

    dsn = os.getenv("SWITCH_GLITCHTIP_DSN", "").strip()
    if not dsn:
        log.info("Telemetry disabled (SWITCH_GLITCHTIP_DSN not set)")
        _initialized = True
        return

    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration

    release = _git_sha()
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SWITCH_ENV", "prod").strip() or "prod",
        release=release,
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
    )
    _initialized = True
    log.info("Telemetry enabled (GlitchTip), release=%s", release)
