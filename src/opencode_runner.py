#!/usr/bin/env python3
"""OpenCode CLI runner for XMPP bridge."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

log = logging.getLogger("opencode")


@dataclass
class OpenCodeResult:
    """Final result from an OpenCode run."""

    text: str
    session_id: str | None
    cost: float
    tokens_in: int
    tokens_out: int
    tokens_reasoning: int
    tokens_cache_read: int
    tokens_cache_write: int
    duration_s: float
    tool_count: int


@dataclass
class RunState:
    """Accumulates state during an OpenCode run."""

    start_time: datetime = field(default_factory=datetime.now)
    session_id: str | None = None
    text: str = ""
    tool_count: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_reasoning: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    cost: float = 0.0
    saw_result: bool = False
    saw_error: bool = False
    raw_output: list[str] = field(default_factory=list)

    def to_result(self) -> OpenCodeResult:
        return OpenCodeResult(
            text=self.text,
            session_id=self.session_id,
            cost=self.cost,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            tokens_reasoning=self.tokens_reasoning,
            tokens_cache_read=self.tokens_cache_read,
            tokens_cache_write=self.tokens_cache_write,
            duration_s=(datetime.now() - self.start_time).total_seconds(),
            tool_count=self.tool_count,
        )


Event = tuple[str, str | OpenCodeResult]


class OpenCodeRunner:
    """Runs OpenCode CLI and streams parsed events."""

    def __init__(
        self,
        working_dir: str,
        output_dir: Path,
        session_name: str | None = None,
        model: str | None = None,
        reasoning_mode: str = "normal",
        agent: str = "bridge",
    ):
        self.working_dir = working_dir
        self.output_dir = output_dir
        self.session_name = session_name
        self.model = model
        self.reasoning_mode = reasoning_mode
        self.agent = agent
        self.process: asyncio.subprocess.Process | None = None
        self.output_file: Path | None = None

        if session_name:
            output_dir.mkdir(exist_ok=True)
            self.output_file = output_dir / f"{session_name}.log"

    def _build_command(self, prompt: str, session_id: str | None) -> list[str]:
        """Build the opencode command line."""
        cmd = ["opencode", "run", "--format", "json", "--agent", self.agent]
        if session_id:
            cmd.extend(["--session", session_id])
        if self.model:
            cmd.extend(["--model", self.model])
        if self.reasoning_mode == "high":
            cmd.extend(["--variant", "high"])
        cmd.extend(["--", prompt])
        return cmd

    def _log_to_file(self, content: str) -> None:
        """Append content to the output log file."""
        if self.output_file:
            with open(self.output_file, "a") as f:
                f.write(content)

    def _handle_step_start(self, event: dict, state: RunState) -> Event | None:
        """Handle step_start event - extracts session ID."""
        session_id = event.get("sessionID")
        if isinstance(session_id, str) and session_id:
            state.session_id = session_id
            return ("session_id", session_id)
        return None

    def _handle_text(self, event: dict, state: RunState) -> Event | None:
        """Handle text event - accumulates response text."""
        part = event.get("part", {})
        text = part.get("text", "") if isinstance(part, dict) else ""
        if isinstance(text, str) and text:
            state.text += text
            self._log_to_file(f"\n[TEXT]\n{text}\n")
            return ("text", text)
        return None

    def _handle_tool_use(self, event: dict, state: RunState) -> Event | None:
        """Handle tool_use event - tracks tool invocations."""
        part = event.get("part", {})
        if not isinstance(part, dict):
            return None

        tool = part.get("tool")
        if not tool:
            return None

        state.tool_count += 1
        tool_state = part.get("state", {})
        title = tool_state.get("title") if isinstance(tool_state, dict) else None
        desc = f"[tool:{tool} {title}]" if title else f"[tool:{tool}]"
        self._log_to_file(f"{desc}\n")
        return ("tool", desc)

    def _handle_step_finish(self, event: dict, state: RunState) -> Event | None:
        """Handle step_finish event - accumulates tokens/cost, emits result on stop."""
        part = event.get("part", {})
        if not isinstance(part, dict):
            return None

        # Accumulate token counts
        tokens = part.get("tokens", {})
        if isinstance(tokens, dict):
            cache = tokens.get("cache", {})
            state.tokens_in += int(tokens.get("input", 0) or 0)
            state.tokens_out += int(tokens.get("output", 0) or 0)
            state.tokens_reasoning += int(tokens.get("reasoning", 0) or 0)
            if isinstance(cache, dict):
                state.tokens_cache_read += int(cache.get("read", 0) or 0)
                state.tokens_cache_write += int(cache.get("write", 0) or 0)

        state.cost += float(part.get("cost", 0) or 0)

        if part.get("reason") == "stop":
            state.saw_result = True
            return ("result", state.to_result())
        return None

    def _handle_error(self, event: dict, state: RunState) -> Event:
        """Handle error event - extracts error message."""
        state.saw_error = True
        message = event.get("message")
        error = event.get("error")

        # message can be nested
        if isinstance(message, dict):
            message = message.get("data", {}).get("message") or message.get("message")

        return ("error", str(message or error or "OpenCode error"))

    def _parse_event(self, event: dict, state: RunState) -> Event | None:
        """Parse a JSON event and return the appropriate yield value."""
        handlers = {
            "step_start": self._handle_step_start,
            "text": self._handle_text,
            "tool_use": self._handle_tool_use,
            "step_finish": self._handle_step_finish,
            "error": self._handle_error,
        }
        event_type = event.get("type")
        handler = handlers.get(event_type)
        if handler:
            return handler(event, state)
        return None

    async def run(
        self, prompt: str, session_id: str | None = None
    ) -> AsyncIterator[Event]:
        """Run OpenCode, yielding (event_type, content) tuples.

        Events:
            ("session_id", str) - Session ID for continuity
            ("text", str) - Incremental response text
            ("tool", str) - Tool invocation description
            ("result", OpenCodeResult) - Final result with stats
            ("error", str) - Error message
        """
        cmd = self._build_command(prompt, session_id)
        state = RunState()

        log.info(f"OpenCode: {prompt[:50]}...")
        self._log_to_file(f"[{datetime.now().strftime('%H:%M:%S')}] Prompt: {prompt}\n")

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.working_dir,
            )

            if self.process.stdout is None:
                raise RuntimeError("OpenCode process stdout missing")

            async for raw_line in self.process.stdout:
                line = raw_line.decode().strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    if len(state.raw_output) < 5:
                        state.raw_output.append(line)
                    continue

                if isinstance(event, dict):
                    result = self._parse_event(event, state)
                    if result:
                        yield result

            await self.process.wait()

            # Handle cases where we didn't get a proper result
            if self.process.returncode and self.process.returncode != 0:
                state.saw_error = True

            if not state.saw_result and not state.saw_error:
                yield self._make_fallback_error(state)

        except Exception as e:
            log.exception("OpenCode runner error")
            yield ("error", str(e))

    def _make_fallback_error(self, state: RunState) -> Event:
        """Create an error event when OpenCode exits without proper result."""
        if state.raw_output:
            preview = " | ".join(state.raw_output)
            return ("error", f"OpenCode output (non-JSON): {preview}")
        if self.process and self.process.returncode:
            return ("error", f"OpenCode exited with code {self.process.returncode}")
        return ("error", "OpenCode exited without output")

    def cancel(self) -> None:
        """Terminate the running process."""
        if self.process:
            self.process.terminate()
