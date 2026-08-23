"""Heartbeat commands — ralph loop plus watchdog pointer."""

from __future__ import annotations

from src.commands.mixins.base import CommandMixinBase
from src.commands.registry import command
from src.core.session_runtime.api import RalphConfig
from src.heartbeat import (
    clear_heartbeat_session,
    parse_heartbeat_command,
    read_heartbeat_session,
    write_heartbeat_session,
)


_USAGE = (
    "Usage: /heartbeat [prompt] [--max N] [--done 'promise'] [--wait MINUTES]\n"
    "  no args = cycle prompt + --wait 30\n"
    "  same flags as /ralph; no --swarm (one brain)\n\n"
    "Commands:\n"
    "  /heartbeat-status - loop + which session the watchdog watches\n"
    "  /heartbeat-cancel - stop loop and idle the watchdog"
)


class HeartbeatCommandsMixin(CommandMixinBase):
    @command("/heartbeat-cancel", "/heartbeat-stop")
    async def heartbeat_cancel(self, _body: str) -> bool:
        """Stop the heartbeat ralph loop and idle the watchdog if this is it."""
        watched = read_heartbeat_session()
        cleared = False
        if watched == self.bot.session_name:
            cleared = clear_heartbeat_session()

        if self.bot.session.request_ralph_stop():
            extra = " Watchdog pointer cleared." if cleared else ""
            self.bot.send_reply(
                "Heartbeat loop will stop after current iteration." + extra
            )
            return True

        if cleared:
            self.bot.send_reply("No loop running. Watchdog pointer cleared.")
            return True

        self.bot.send_reply("No heartbeat loop running.")
        return True

    @command("/heartbeat-status")
    async def heartbeat_status(self, _body: str) -> bool:
        """Show ralph status plus which session is the heartbeat brain."""
        watched = read_heartbeat_session()
        mine = self.bot.session_name
        if watched == mine:
            who = f"Heartbeat brain: {mine} (this session)"
        elif watched:
            who = f"Heartbeat brain: {watched} (not this session)"
        else:
            who = "No heartbeat session registered"

        live = self.bot.session.get_ralph_status()
        if live and live.status in {"queued", "running", "stopping"}:
            max_str = (
                str(live.max_iterations) if live.max_iterations > 0 else "unlimited"
            )
            wait_minutes = float(live.wait_seconds or 0.0) / 60.0
            self.bot.send_reply(
                f"{who}\n"
                f"Ralph {live.status.upper()}\n"
                f"Iteration: {live.current_iteration}/{max_str}\n"
                f"Cost so far: ${live.total_cost:.3f}\n"
                f"Wait: {wait_minutes:.2f} min\n"
                f"Promise: {live.completion_promise or 'none'}"
            )
            return True

        loop = self.bot.ralph_loops.get_latest(self.bot.session_name)
        if loop:
            max_str = str(loop.max_iterations) if loop.max_iterations else "unlimited"
            wait_minutes = loop.wait_seconds / 60.0
            self.bot.send_reply(
                f"{who}\n"
                f"Last Ralph: {loop.status}\n"
                f"Iterations: {loop.current_iteration}/{max_str}\n"
                f"Wait: {wait_minutes:.2f} min\n"
                f"Cost: ${loop.total_cost:.3f}"
            )
            return True

        self.bot.send_reply(f"{who}\nNo Ralph loops in this session.")
        return True

    @command("/heartbeat", exact=False)
    async def heartbeat(self, body: str) -> bool:
        """Start a ralph loop and point the watchdog at this session."""
        parsed = parse_heartbeat_command(body)
        if parsed is None:
            self.bot.send_reply(_USAGE)
            return True

        if int(parsed.get("swarm") or 1) > 1:
            self.bot.send_reply("Heartbeat is one session. No --swarm.")
            return True

        live = self.bot.session.get_ralph_status()
        running = bool(live and live.status in {"queued", "running", "stopping"})
        if running:
            self._point_watchdog()
            return True

        if self.bot.processing or self.bot.session.pending_count() > 0:
            self.bot.send_reply(
                "Already running or queued. Use /heartbeat-cancel (or /cancel) first."
            )
            return True

        await self.bot.session.start_ralph(
            RalphConfig(
                prompt=parsed["prompt"],
                max_iterations=int(parsed["max_iterations"] or 0),
                completion_promise=parsed["completion_promise"],
                wait_seconds=float(parsed["wait_minutes"] or 0.0) * 60.0,
                prompt_only=bool(parsed.get("prompt_only")),
            )
        )
        self._point_watchdog()
        return True

    def _point_watchdog(self) -> None:
        name = self.bot.session_name
        try:
            write_heartbeat_session(name)
        except (OSError, ValueError) as err:
            self.bot.send_reply(
                f"Loop is on this session, but watchdog pointer write failed: {err}"
            )
            return
        self.bot.send_reply(
            f"Watchdog now watches {name}. "
            "Next supervisor cycle picks it up — no .env edit, no restart."
        )
