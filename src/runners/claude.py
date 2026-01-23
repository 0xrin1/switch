"""Claude Code CLI runner."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from src.runners.base import BaseRunner, RunState

log = logging.getLogger("claude")


@dataclass
class ClaudeResult:
    """Final result from a Claude run."""

    text: str
    session_id: str | None
    cost: float
    turns: int
    tool_count: int
    total_tokens: int
    context_window: int
    duration_s: float


Event = tuple[str, str | ClaudeResult]


class ClaudeRunner(BaseRunner):
    """Runs Claude Code and streams parsed events."""

    def __init__(
        self,
        working_dir: str,
        output_dir: Path,
        session_name: str | None = None,
    ):
        super().__init__(working_dir, output_dir, session_name)
        self.process: asyncio.subprocess.Process | None = None

    def _build_command(self, prompt: str, session_id: str | None) -> list[str]:
        """Build the claude command line."""
        cmd = [
            "claude", "-p", prompt,
            "--model", "opus",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if session_id:
            cmd.extend(["--resume", session_id])
        return cmd

    def _handle_system_init(self, event: dict, state: RunState) -> Event | None:
        """Handle system init event - extracts session ID."""
        if event.get("subtype") != "init":
            return None
        session_id = event.get("session_id")
        if session_id:
            state.session_id = session_id
            return ("session_id", session_id)
        return None

    def _handle_assistant_text(self, block: dict, state: RunState) -> Event | None:
        """Handle text block from assistant message."""
        text = block.get("text", "").strip()
        if not text:
            return None
        state.text = text
        self._log_response(text)
        return ("text", text)

    def _handle_assistant_tool(self, block: dict, state: RunState) -> Event | None:
        """Handle tool_use block from assistant message."""
        state.tool_count += 1
        name = block.get("name", "?")
        tool_input = block.get("input", {})

        if name == "Bash":
            preview = tool_input.get("command", "")[:40]
            desc = f"[{name}: {preview}]"
        elif name in ("Read", "Write", "Edit"):
            path = tool_input.get("file_path", "")
            desc = f"[{name}: {Path(path).name}]"
        else:
            desc = f"[{name}]"

        self._log_to_file(f"{desc}\n")
        return ("tool", desc)

    def _handle_assistant(self, event: dict, state: RunState) -> list[Event]:
        """Handle assistant event - yields text and tool events."""
        events = []
        content = event.get("message", {}).get("content", [])

        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                result = self._handle_assistant_text(block, state)
                if result:
                    events.append(result)
            elif block_type == "tool_use":
                result = self._handle_assistant_tool(block, state)
                if result:
                    events.append(result)

        return events

    def _handle_result(self, event: dict, state: RunState) -> Event:
        """Handle result event - extracts final stats."""
        state.saw_result = True

        if event.get("is_error"):
            state.saw_error = True
            return ("error", event.get("result", "Unknown error"))

        cost = event.get("total_cost_usd", 0)
        turns = event.get("num_turns", 0)
        duration = event.get("duration_ms", 0) / 1000

        usage = event.get("usage", {})
        total_tokens = (
            usage.get("input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("output_tokens", 0)
        )

        # Find context window from model usage
        context_window = 200000
        for model_name, model_data in event.get("modelUsage", {}).items():
            if "opus" in model_name:
                context_window = model_data.get("contextWindow", 200000)
                break
            context_window = model_data.get("contextWindow", context_window)

        tokens_k = total_tokens / 1000
        context_k = context_window / 1000
        summary = f"[{turns}t {state.tool_count}tools ${cost:.3f} {duration:.1f}s | {tokens_k:.1f}k/{context_k:.0f}k]"

        return ("result", summary)

    def _parse_event(self, event: dict, state: RunState) -> list[Event]:
        """Parse a JSON event and return yield values."""
        event_type = event.get("type")

        if event_type == "system":
            result = self._handle_system_init(event, state)
            return [result] if result else []
        elif event_type == "assistant":
            return self._handle_assistant(event, state)
        elif event_type == "result":
            return [self._handle_result(event, state)]

        return []

    async def run(
        self, prompt: str, session_id: str | None = None
    ) -> AsyncIterator[Event]:
        """Run Claude, yielding (event_type, content) tuples.

        Events:
            ("session_id", str) - Session ID for continuity
            ("text", str) - Response text
            ("tool", str) - Tool invocation description
            ("result", str) - Final result summary
            ("error", str) - Error message
        """
        cmd = self._build_command(prompt, session_id)
        state = RunState()

        log.info(f"Claude: {prompt[:50]}...")
        self._log_prompt(prompt)

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.working_dir,
                limit=10 * 1024 * 1024,
            )

            if self.process.stdout is None:
                raise RuntimeError("Claude process stdout missing")

            async for raw_line in self.process.stdout:
                line = raw_line.decode().strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                for result in self._parse_event(event, state):
                    yield result

            await self.process.wait()

        except Exception as e:
            log.exception("Claude runner error")
            yield ("error", str(e))

    def cancel(self) -> None:
        """Terminate the running process."""
        if self.process:
            self.process.terminate()
