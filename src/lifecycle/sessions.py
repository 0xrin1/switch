"""Session lifecycle operations.

Goal: keep session create/kill/close semantics in one place so the dispatcher,
session bot commands, and scripts don't drift.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

from src.helpers import delete_xmpp_account, kill_tmux_session
from src.utils import BaseXMPPBot

if TYPE_CHECKING:
    from src.bots.session import SessionBot
    from src.db import SessionRepository

_log = logging.getLogger("lifecycle.sessions")


class _SessionKillManager(Protocol):
    sessions: "SessionRepository"
    session_bots: dict[str, "SessionBot"]
    xmpp_server: str
    xmpp_domain: str
    xmpp_recipient: str
    ejabberd_ctl: str

    def notify_directory_sessions_changed(self, dispatcher_jid: str | None = None) -> None: ...


async def kill_session(
    manager: _SessionKillManager,
    name: str,
    *,
    goodbye: str = "Session closed. Goodbye!",
) -> bool:
    """Archive-with-goodbye session kill.

    Semantics:
    - Send a final goodbye message (best-effort)
    - Stop the in-memory bot and prevent reconnect
    - Unregister the XMPP account
    - Kill tmux
    - Mark session closed in DB
    """

    session = manager.sessions.get(name)
    if not session or session.status == "closed":
        return session is not None

    async def _send_goodbye_best_effort() -> None:
        # Prefer sending from the in-memory bot (so it appears from the session contact).
        bot = manager.session_bots.get(name)
        if bot and bot.is_connected() and not bot.shutting_down:
            try:
                bot.send_reply(goodbye)
                await asyncio.sleep(0.25)
                return
            except Exception:
                pass

        # Fallback: connect as the session account and send a one-shot message.
        class _ClosureBot(BaseXMPPBot):
            def __init__(self, jid: str, password: str, recipient: str, message: str):
                super().__init__(jid, password, recipient=recipient)
                self._message = message
                self.add_event_handler("session_start", self.on_start)

            async def on_start(self, _event):
                self.send_presence()
                self.send_reply(self._message)
                await asyncio.sleep(0.5)
                self.disconnect()

        try:
            closure = _ClosureBot(
                session.xmpp_jid,
                session.xmpp_password,
                manager.xmpp_recipient,
                goodbye,
            )
            closure.connect_to_server(manager.xmpp_server)
            await asyncio.wait_for(closure.disconnected, timeout=5)
        except Exception:
            # Best-effort only.
            return

    await _send_goodbye_best_effort()

    # If the bot is running, cancel in-flight work and prevent reconnects before we delete the account.
    bot = manager.session_bots.get(name)
    if bot:
        try:
            bot.shutting_down = True
            bot.cancel_operations(notify=False)
            bot.disconnect()
        except Exception:
            pass

    username = session.xmpp_jid.split("@")[0]
    delete_xmpp_account(
        username,
        manager.ejabberd_ctl,
        manager.xmpp_domain,
        getattr(bot, "log", None) or _log,
    )
    kill_tmux_session(name)
    manager.sessions.close(name)
    manager.session_bots.pop(name, None)

    manager.notify_directory_sessions_changed()
    return True
