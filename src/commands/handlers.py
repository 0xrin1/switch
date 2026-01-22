"""Command handlers for session bot."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

from src.ralph import RalphLoop, parse_ralph_command

if TYPE_CHECKING:
    from src.bots.session import SessionBot


class CommandHandler:
    """Handles slash commands for a session bot.

    Each command is a method prefixed with cmd_ that returns True if handled.
    Commands are registered in the dispatch table on init.
    """

    def __init__(self, bot: "SessionBot"):
        self.bot = bot

        # Command dispatch table: prefix -> (handler, needs_exact_match)
        self._commands: dict[str, tuple[Callable[[str], Awaitable[bool]], bool]] = {
            "/kill": (self.cmd_kill, True),
            "/cancel": (self.cmd_cancel, True),
            "/reset": (self.cmd_reset, True),
            "/ralph-cancel": (self.cmd_ralph_cancel, True),
            "/ralph-stop": (self.cmd_ralph_cancel, True),
            "/ralph-status": (self.cmd_ralph_status, True),
            "/peek": (self.cmd_peek, False),
            "/agent": (self.cmd_agent, False),
            "/thinking": (self.cmd_thinking, False),
            "/model": (self.cmd_model, False),
            "/ralph": (self.cmd_ralph, False),
        }

    async def handle(self, body: str) -> bool:
        """Handle a command. Returns True if command was handled."""
        cmd = body.strip().lower()

        # Try exact matches first
        for prefix, (handler, exact) in self._commands.items():
            if exact and cmd == prefix:
                return await handler(body)
            if not exact and cmd.startswith(prefix):
                return await handler(body)

        return False

    async def cmd_kill(self, _body: str) -> bool:
        """End the session."""
        self.bot.send_reply("Ending session. Goodbye!")
        asyncio.ensure_future(self.bot._self_destruct())
        return True

    async def cmd_cancel(self, _body: str) -> bool:
        """Cancel current operation."""
        if self.bot.ralph_loop:
            self.bot.ralph_loop.cancel()
            if self.bot.runner:
                self.bot.runner.cancel()
            self.bot.send_reply("Cancelling Ralph loop...")
        elif self.bot.runner and self.bot.processing:
            self.bot.runner.cancel()
            self.bot.send_reply("Cancelling current run...")
        else:
            self.bot.send_reply("Nothing running to cancel.")
        return True

    async def cmd_peek(self, body: str) -> bool:
        """Show recent output."""
        parts = body.strip().lower().split()
        num_lines = 30
        if len(parts) > 1:
            try:
                num_lines = int(parts[1])
            except ValueError:
                pass
        await self.bot.peek_output(num_lines)
        return True

    async def cmd_agent(self, body: str) -> bool:
        """Switch active engine."""
        parts = body.strip().lower().split()
        if len(parts) < 2:
            self.bot.send_reply("Usage: /agent oc|cc")
            return True

        engine_map = {
            "oc": "opencode", "opencode": "opencode",
            "cc": "claude", "claude": "claude",
        }
        engine = engine_map.get(parts[1])
        if not engine:
            self.bot.send_reply("Usage: /agent oc|cc")
            return True

        self.bot.sessions.update_engine(self.bot.session_name, engine)
        self.bot.send_reply(f"Active engine set to {engine}.")
        return True

    async def cmd_thinking(self, body: str) -> bool:
        """Set reasoning mode."""
        parts = body.strip().lower().split()
        if len(parts) < 2 or parts[1] not in ("normal", "high"):
            self.bot.send_reply("Usage: /thinking normal|high")
            return True

        session = self.bot.sessions.get(self.bot.session_name)
        if session and session.active_engine != "opencode":
            self.bot.send_reply("/thinking only applies to OpenCode sessions.")
            return True

        self.bot.sessions.update_reasoning_mode(self.bot.session_name, parts[1])
        self.bot.send_reply(f"Reasoning mode set to {parts[1]}.")
        return True

    async def cmd_model(self, body: str) -> bool:
        """Set model ID."""
        parts = body.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            self.bot.send_reply("Usage: /model <model-id>")
            return True

        model_id = parts[1].strip()
        self.bot.sessions.update_model(self.bot.session_name, model_id)
        self.bot.send_reply(f"Model set to {model_id}.")
        return True

    async def cmd_reset(self, _body: str) -> bool:
        """Reset session context."""
        session = self.bot.sessions.get(self.bot.session_name)
        if session and session.active_engine == "claude":
            self.bot.sessions.reset_claude_session(self.bot.session_name)
        else:
            self.bot.sessions.reset_opencode_session(self.bot.session_name)
        self.bot.send_reply("Session reset.")
        return True

    async def cmd_ralph_cancel(self, _body: str) -> bool:
        """Cancel Ralph loop."""
        if self.bot.ralph_loop:
            self.bot.ralph_loop.cancel()
            self.bot.send_reply("Ralph loop will stop after current iteration...")
        else:
            self.bot.send_reply("No Ralph loop running.")
        return True

    async def cmd_ralph_status(self, _body: str) -> bool:
        """Show Ralph loop status."""
        if self.bot.ralph_loop:
            rl = self.bot.ralph_loop
            max_str = str(rl.max_iterations) if rl.max_iterations > 0 else "unlimited"
            self.bot.send_reply(
                f"Ralph RUNNING\n"
                f"Iteration: {rl.current_iteration}/{max_str}\n"
                f"Cost so far: ${rl.total_cost:.3f}\n"
                f"Promise: {rl.completion_promise or 'none'}"
            )
        else:
            loop = self.bot.ralph_loops.get_latest(self.bot.session_name)
            if loop:
                max_str = str(loop.max_iterations) if loop.max_iterations else "unlimited"
                self.bot.send_reply(
                    f"Last Ralph: {loop.status}\n"
                    f"Iterations: {loop.current_iteration}/{max_str}\n"
                    f"Cost: ${loop.total_cost:.3f}"
                )
            else:
                self.bot.send_reply("No Ralph loops in this session.")
        return True

    async def cmd_ralph(self, body: str) -> bool:
        """Start a Ralph loop."""
        ralph_args = parse_ralph_command(body)
        if ralph_args is None:
            self.bot.send_reply(
                "Usage: /ralph <prompt> [--max N] [--done 'promise']\n"
                "  or:  /ralph <N> <prompt>  (shorthand)\n\n"
                "Examples:\n"
                "  /ralph 20 Fix all type errors\n"
                "  /ralph Refactor auth --max 10 --done 'All tests pass'\n\n"
                "Commands:\n"
                "  /ralph-status - check progress\n"
                "  /ralph-cancel - stop loop"
            )
            return True

        if self.bot.processing:
            self.bot.send_reply("Already running. Use /ralph-cancel first.")
            return True

        self.bot.ralph_loop = RalphLoop(
            self.bot,
            ralph_args["prompt"],
            self.bot.working_dir,
            self.bot.output_dir,
            max_iterations=ralph_args["max_iterations"],
            completion_promise=ralph_args["completion_promise"],
            sessions=self.bot.sessions,
            ralph_loops=self.bot.ralph_loops,
        )
        self.bot.processing = True
        asyncio.ensure_future(cast(Awaitable[Any], self.bot._run_ralph()))
        return True
