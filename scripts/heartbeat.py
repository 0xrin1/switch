#!/usr/bin/env python3
"""Switch heartbeat SUPERVISOR (watchdog only — the "brain" is a ralph pi
session driven by ~/switch/prompts/heartbeat-cycle.md).

Every HEARTBEAT_INTERVAL seconds:
  1. Find the heartbeat session's ralph loop in sessions.db.
  2. Healthy = loop status 'running' AND (iteration advanced since we last
     saw it, OR session last_active fresh < SESSION_ACTIVE_GRACE seconds).
  3. Stalled (iteration flat for STALE_SECONDS, loop not running, or session
     missing) -> voice alert over the SC205s, rate-limited to one per
     ALERT_COOLDOWN seconds. The voice service lives on dorothy, so the
     alert still works even when helga's inference (which the ralph session
     needs) is down — that is exactly the failure mode we watch.

Robustness contract: stdlib only, every call time-boxed, any failure =
log + silence (or alert if that is the point). Never crashes the loop.

Watched session (re-read every cycle, no restart):
  ~/switch/state/heartbeat-session   written by /heartbeat (preferred)
  HEARTBEAT_SESSION env              fallback if the pointer file is empty

Env (~/switch/heartbeat.env):
  HEARTBEAT_SESSION    fallback ralph session name (e.g. "ralph")
  HEARTBEAT_INTERVAL   seconds between supervisor cycles (default 600)
  STALE_SECONDS        iteration must advance within this (default 1500)
  ALERT_COOLDOWN       min seconds between spoken alerts (default 7200)
  VOICE_URL / VOICE_TOKEN / VOICE_NAME  dorothy voice service
  HEARTBEAT_DRY_RUN=1  log alerts instead of speaking
"""
import fcntl
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime

BASE = os.path.expanduser("~/switch")
DB = os.path.join(BASE, "sessions.db")
STATE_DIR = os.path.join(BASE, "state")
LOG_DIR = os.path.join(BASE, "logs")
LOG = os.path.join(LOG_DIR, "heartbeat.log")
STATE = os.path.join(STATE_DIR, "heartbeat.json")
LOCK = os.path.join(STATE_DIR, "heartbeat.lock")
POINTER = os.path.join(STATE_DIR, "heartbeat-session")

INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "600"))
ENV_SESSION = os.environ.get("HEARTBEAT_SESSION", "").strip()
STALE_SECONDS = int(os.environ.get("STALE_SECONDS", "1500"))
ALERT_COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", "7200"))
SESSION_ACTIVE_GRACE = int(os.environ.get("SESSION_ACTIVE_GRACE", "1800"))
VOICE_URL = os.environ.get("VOICE_URL", "http://100.119.143.40:8931").rstrip("/")
VOICE_TOKEN = os.environ.get("VOICE_TOKEN", "")
VOICE_NAME = os.environ.get("VOICE_NAME", "af_heart")
DRY_RUN = os.environ.get("HEARTBEAT_DRY_RUN") == "1"

ALERT_LINE = (
    "Heads up. Your heartbeat agent looks stalled. Send it slash heartbeat "
    "status in chat, or ask an agent to check the switch heartbeat skill."
)

# --- GlitchTip telemetry (optional, fail-soft) --------------------------------
# Set HEARTBEAT_GLITCHTIP_DSN (in ~/switch/heartbeat.env) to report watchdog
# errors to GlitchTip (project: switch-heartbeat). The watchdog runs on SYSTEM
# python, so sentry-sdk lives in a library venv exposed via PYTHONPATH
# (~/switch/vendor/telemetry — see switch-heartbeat.service). If the import
# fails, telemetry is a no-op: the watchdog's job never depends on it.
TELEMETRY_DSN = os.environ.get("HEARTBEAT_GLITCHTIP_DSN", "").strip()
_telemetry = None


def init_telemetry():
    global _telemetry
    if not TELEMETRY_DSN:
        log("telemetry disabled (HEARTBEAT_GLITCHTIP_DSN not set)")
        return
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=TELEMETRY_DSN,
            environment="prod",
        )
        with sentry_sdk.configure_scope() as scope:
            scope.set_tag("service", "switch-heartbeat")
        _telemetry = sentry_sdk
        log("telemetry enabled (GlitchTip, project switch-heartbeat)")
    except Exception as e:  # noqa: BLE001
        log(f"telemetry unavailable (continuing without): {e}")


def report(exc, **context):
    """Send an exception to GlitchTip if telemetry is active."""
    if _telemetry is None:
        return
    try:
        _telemetry.set_context("watchdog", context)
        _telemetry.capture_exception(exc)
    except Exception:  # noqa: BLE001
        pass


def report_stall(session, why):
    """Record a stall finding (one event per cycle; GlitchTip groups repeats)."""
    if _telemetry is None:
        return
    try:
        _telemetry.set_context("watchdog", {"session": session, "why": why})
        _telemetry.capture_message(
            f"heartbeat stalled: {session} ({'; '.join(why)})", level="error"
        )
    except Exception:  # noqa: BLE001
        pass


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def resolve_session():
    """Pointer file first (live /heartbeat retarget), then env fallback."""
    try:
        with open(POINTER) as f:
            name = f.read().splitlines()[0].strip()
        if name:
            return name
    except OSError:
        pass
    return ENV_SESSION


def db_read(session):
    """(ralph row dict|None, session last_active str|None, other ralph names)"""
    if not session:
        return None, None, []
    try:
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "select session_name, status, current_iteration, max_iterations, "
            "started_at, finished_at from ralph_loops "
            "where session_name = ? order by started_at desc limit 1",
            (session,),
        ).fetchone()
        sess = db.execute(
            "select last_active from sessions where name = ?", (session,)
        ).fetchone()
        others = db.execute(
            "select distinct session_name from ralph_loops "
            "where status in ('running', 'stopping') and session_name != ? "
            "order by session_name",
            (session,),
        ).fetchall()
        db.close()
        ralph = dict(row) if row else None
        last_active = sess["last_active"] if sess else None
        other_names = [r["session_name"] for r in others]
        return ralph, last_active, other_names
    except Exception as e:  # noqa: BLE001
        log(f"db read failed: {e}")
        report(e, op="db_read", session=session)
        return None, None, []


def iso_to_ts(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def speak(line):
    try:
        url = (f"{VOICE_URL}/say?voice={urllib.parse.quote(VOICE_NAME)}"
               f"&text={urllib.parse.quote(line)}")
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {VOICE_TOKEN}"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status == 202
    except Exception as e:  # noqa: BLE001
        log(f"voice call failed: {e}")
        report(e, op="voice_alert")
        return False


def cycle():
    session = resolve_session()
    if not session:
        log("no heartbeat session (pointer + HEARTBEAT_SESSION empty) — supervisor idle")
        return

    ralph, last_active, others = db_read(session)
    now = time.time()
    st = load_state()

    prev = st.get("watched_session")
    just_retargeted = prev != session
    if just_retargeted:
        st["watched_session"] = session
        st.pop("last_seen_iteration", None)
        st.pop("last_iter_change_ts", None)
        save_state(st)
        log(f"now watching {session} (was {prev or 'unset'})")

    if others:
        log(f"other ralphs (not heartbeat): {', '.join(others)}")

    if just_retargeted and not (ralph and ralph.get("status") == "running"):
        log(f"watching {session}; loop not running yet — not stalling")
        return

    # Healthy signals
    loop_running = bool(ralph) and ralph.get("status") == "running"
    session_fresh = bool(last_active) and (
        now - iso_to_ts(last_active) < SESSION_ACTIVE_GRACE
    )
    iteration = ralph.get("current_iteration") if ralph else None
    last_seen_iter = st.get("last_seen_iteration")

    if loop_running:
        if iteration != last_seen_iter:
            st["last_seen_iteration"] = iteration
            st["last_iter_change_ts"] = now
            save_state(st)
            log(
                f"healthy: {session} iter advanced -> {iteration} "
                f"(session active: {session_fresh})"
            )
            return
        # iteration unchanged: is it within the stale window?
        last_change = st.get("last_iter_change_ts", 0)
        if last_change and (now - last_change) < STALE_SECONDS:
            log(
                f"healthy: {session} no new iter yet "
                f"({int((now - last_change) // 60)}m since last)"
            )
            return
        if session_fresh:
            log(f"healthy: {session} iter flat but session still active")
            return

    # Stalled
    why = []
    if not ralph:
        why.append("no ralph loop row")
    elif not loop_running:
        why.append(f"loop status={ralph.get('status')}")
    if not session_fresh:
        why.append("session not active")
    why.append(f"iter={iteration} flat >{STALE_SECONDS // 60}m")
    log(f"STALLED {session}: " + "; ".join(why))
    report_stall(session, why)

    last_alert = st.get("last_alert_ts", 0)
    if now - last_alert < ALERT_COOLDOWN:
        log(f"stall alert suppressed (cooldown {int((ALERT_COOLDOWN - (now - last_alert)) // 60)}m left)")
        return
    if DRY_RUN:
        log(f"DRY_RUN would speak: {ALERT_LINE}")
        st["last_alert_ts"] = now
        save_state(st)
        return
    if speak(ALERT_LINE):
        st["last_alert_ts"] = now
        st["last_alert"] = datetime.now().isoformat()
        save_state(st)
        log("SPOKE alert: heartbeat stalled")
    else:
        log("voice unavailable — alert skipped")


def main():
    log(f"heartbeat supervisor started (watching {resolve_session() or '<unset>'}, "
        f"interval {INTERVAL}s, stale {STALE_SECONDS}s, dry_run {DRY_RUN})")
    init_telemetry()
    while True:
        try:
            lock = open(LOCK, "w")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                log("previous cycle still running — skipping")
            else:
                t0 = time.time()
                cycle()
                log(f"cycle done in {time.time() - t0:.1f}s")
        except Exception as e:  # noqa: BLE001
            log(f"cycle crashed (will retry): {e!r}")
            report(e, op="cycle")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
