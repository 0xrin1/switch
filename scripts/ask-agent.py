#!/usr/bin/env python3
"""Ask another dispatcher/session and wait for first answer.

This is a delegation helper for agents running on the Switch box.
It sends a prompt to a dispatcher, waits for the spawned session to produce
its first assistant message, and prints that result to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.db import DB_PATH
from src.utils import BaseXMPPBot, get_xmpp_config, load_env


class AskBot(BaseXMPPBot):
    def __init__(self, jid: str, password: str, target_jid: str, message: str):
        super().__init__(jid, password, recipient=target_jid)
        self.message = message
        self.add_event_handler("session_start", self.on_start)
        self.add_event_handler("failed_auth", self.on_failed_auth)

    def on_failed_auth(self, event):
        pass

    async def on_start(self, event):
        self.send_presence()
        self.send_reply(self.message)
        await asyncio.sleep(1.5)
        self.disconnect()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delegate prompt to dispatcher")
    parser.add_argument("prompt", nargs="+", help="prompt for delegated agent")
    parser.add_argument(
        "--dispatcher",
        "-d",
        default=None,
        help="dispatcher name (default: SWITCH_DEFAULT_DISPATCHER or oc-gpt)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="max seconds to wait for a delegated result",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="sqlite polling interval in seconds",
    )
    return parser.parse_args(argv)


def _default_dispatcher_name() -> str:
    import os

    return (os.getenv("SWITCH_DEFAULT_DISPATCHER") or "oc-gpt").strip() or "oc-gpt"


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_latest_message_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM session_messages").fetchone()
    if not row:
        return 0
    value = row["max_id"]
    return int(value) if isinstance(value, (int, float)) else 0


def _find_spawned_session_for_token(
    conn: sqlite3.Connection,
    *,
    dispatcher_jid: str,
    token: str,
    min_message_id: int,
) -> tuple[str, int] | None:
    row = conn.execute(
        """
        SELECT m.session_name, m.id
        FROM session_messages AS m
        JOIN sessions AS s ON s.name = m.session_name
        WHERE m.role = 'user'
          AND m.id > ?
          AND instr(m.content, ?) > 0
          AND s.status = 'active'
          AND COALESCE(s.dispatcher_jid, '') = ?
        ORDER BY m.id DESC
        LIMIT 1
        """,
        (min_message_id, token, dispatcher_jid),
    ).fetchone()
    if not row:
        return None
    return str(row["session_name"]), int(row["id"])


def _find_assistant_reply(
    conn: sqlite3.Connection, *, session_name: str, after_id: int
) -> tuple[str, int] | None:
    row = conn.execute(
        """
        SELECT content, id
        FROM session_messages
        WHERE session_name = ?
          AND role = 'assistant'
          AND id > ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (session_name, after_id),
    ).fetchone()
    if not row:
        return None
    return str(row["content"] or ""), int(row["id"])


async def _send_dispatcher_message(
    *, server: str, dispatcher_jid: str, dispatcher_password: str, body: str
) -> None:
    bot = AskBot(dispatcher_jid, dispatcher_password, dispatcher_jid, body)
    bot.connect_to_server(server)
    await asyncio.wait_for(bot.disconnected, timeout=10)


async def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    load_env()
    cfg = get_xmpp_config()

    dispatcher_name = (args.dispatcher or _default_dispatcher_name()).strip()
    dispatcher = cfg.get("dispatchers", {}).get(dispatcher_name)
    if not dispatcher:
        known = ", ".join(sorted((cfg.get("dispatchers") or {}).keys())) or "none"
        print(f"Error: unknown dispatcher '{dispatcher_name}'. Known: {known}", file=sys.stderr)
        return 2

    dispatcher_jid = str(dispatcher.get("jid") or "").strip()
    dispatcher_password = str(dispatcher.get("password") or "").strip()
    if not dispatcher_password:
        print(
            f"Error: dispatcher '{dispatcher_name}' has no password configured.",
            file=sys.stderr,
        )
        return 1

    prompt_text = " ".join(args.prompt).strip()
    if not prompt_text:
        print("Error: empty prompt", file=sys.stderr)
        return 1

    token = f"switch-delegate-{secrets.token_hex(6)}"
    envelope = (
        f"[delegate_id:{token}]\n"
        "You are being consulted by another Switch session. "
        "Provide your answer directly and concisely.\n\n"
        f"{prompt_text}"
    )

    conn = _open_db()
    try:
        min_message_id = _get_latest_message_id(conn)
        await _send_dispatcher_message(
            server=cfg["server"],
            dispatcher_jid=dispatcher_jid,
            dispatcher_password=dispatcher_password,
            body=envelope,
        )

        deadline = time.monotonic() + max(5.0, float(args.timeout or 0.0))
        session_name: str | None = None
        user_message_id: int | None = None

        while time.monotonic() < deadline:
            conn.commit()
            session_ref = _find_spawned_session_for_token(
                conn,
                dispatcher_jid=dispatcher_jid,
                token=token,
                min_message_id=min_message_id,
            )
            if session_ref:
                session_name, user_message_id = session_ref
                break
            await asyncio.sleep(max(0.1, float(args.poll_interval or 1.0)))

        if not session_name or user_message_id is None:
            print("Error: timed out waiting for delegated session creation", file=sys.stderr)
            return 3

        while time.monotonic() < deadline:
            conn.commit()
            reply = _find_assistant_reply(
                conn, session_name=session_name, after_id=user_message_id
            )
            if reply:
                content, _reply_id = reply
                print(content.strip())
                return 0
            await asyncio.sleep(max(0.1, float(args.poll_interval or 1.0)))

        print(
            f"Error: timed out waiting for delegated answer from {session_name}",
            file=sys.stderr,
        )
        return 4
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
