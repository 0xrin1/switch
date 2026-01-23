"""OpenCode CLI runner with question support via embedded server."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, Awaitable

import aiohttp

from src.runners.base import BaseRunner, RunState

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
class Question:
    """A question from the AI to the user."""

    request_id: str
    questions: list[dict]  # [{header, question, options: [{label, description}]}]


Event = tuple[str, str | OpenCodeResult | Question]

# Type for question callback: receives Question, returns answers dict
QuestionCallback = Callable[[Question], Awaitable[dict[str, list[str]]]]


def _find_free_port() -> int:
    """Find a free port to use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OpenCodeRunner(BaseRunner):
    """Runs OpenCode CLI with embedded server for question support.

    Uses subprocess with JSON output for main communication, plus HTTP API
    for answering questions when the AI asks them.
    """

    def __init__(
        self,
        working_dir: str,
        output_dir: Path,
        session_name: str | None = None,
        model: str | None = None,
        reasoning_mode: str = "normal",
        agent: str = "bridge",
        question_callback: QuestionCallback | None = None,
    ):
        super().__init__(working_dir, output_dir, session_name)
        self.model = model
        self.reasoning_mode = reasoning_mode
        self.agent = agent
        self.question_callback = question_callback
        self.process: asyncio.subprocess.Process | None = None
        self._port: int | None = None
        self._cancelled = False

    def _build_command(self, prompt: str, session_id: str | None, port: int) -> list[str]:
        """Build the opencode command line."""
        cmd = [
            "opencode", "run",
            "--format", "json",
            "--port", str(port),
            "--agent", self.agent,
        ]
        if session_id:
            cmd.extend(["--session", session_id])
        if self.model:
            cmd.extend(["--model", self.model])
        if self.reasoning_mode == "high":
            cmd.extend(["--variant", "high"])
        cmd.extend(["--", prompt])
        return cmd

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
            self._log_response(text)
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
            return ("result", self._make_result(state))
        return None

    def _handle_error(self, event: dict, state: RunState) -> Event:
        """Handle error event - extracts error message."""
        state.saw_error = True
        message = event.get("message")
        error = event.get("error")

        if isinstance(message, dict):
            message = message.get("data", {}).get("message") or message.get("message")

        return ("error", str(message or error or "OpenCode error"))

    def _handle_question(self, event: dict, state: RunState) -> Event | None:
        """Handle question.asked event - creates Question object."""
        # Try different field names for request ID
        request_id = (
            event.get("requestID")
            or event.get("id")
            or event.get("properties", {}).get("requestID")
            or event.get("properties", {}).get("id")
        )

        # Try different field names for questions
        questions = (
            event.get("questions")
            or event.get("properties", {}).get("questions")
            or []
        )

        if not request_id:
            log.warning(f"Question event missing request ID: {event}")
            return None

        question = Question(request_id=request_id, questions=questions)
        self._log_to_file(f"\n[QUESTION] {request_id}: {questions}\n")
        return ("question", question)

    def _make_result(self, state: RunState) -> OpenCodeResult:
        """Create result object from current state."""
        return OpenCodeResult(
            text=state.text,
            session_id=state.session_id,
            cost=state.cost,
            tokens_in=state.tokens_in,
            tokens_out=state.tokens_out,
            tokens_reasoning=state.tokens_reasoning,
            tokens_cache_read=state.tokens_cache_read,
            tokens_cache_write=state.tokens_cache_write,
            duration_s=state.duration_s,
            tool_count=state.tool_count,
        )

    def _parse_event(self, event: dict, state: RunState) -> Event | None:
        """Parse a JSON event and return the appropriate yield value."""
        event_type = event.get("type")
        handlers = {
            "step_start": self._handle_step_start,
            "text": self._handle_text,
            "tool_use": self._handle_tool_use,
            "step_finish": self._handle_step_finish,
            "error": self._handle_error,
            "question.asked": self._handle_question,
            "question": self._handle_question,
        }
        handler = handlers.get(event_type)
        return handler(event, state) if handler else None

    async def _answer_question(self, question: Question, answers: dict[str, list[str]]) -> bool:
        """Send answer to a question via HTTP API."""
        if not self._port:
            log.error("Cannot answer question: no server port")
            return False

        url = f"http://127.0.0.1:{self._port}/question/{question.request_id}/reply"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={"answers": answers}) as resp:
                    success = resp.status == 200
                    if success:
                        log.info(f"Answered question {question.request_id}")
                    else:
                        log.error(f"Failed to answer question: {resp.status}")
                    return success
        except Exception as e:
            log.error(f"Failed to answer question: {e}")
            return False

    async def _reject_question(self, question: Question) -> bool:
        """Reject a question via HTTP API."""
        if not self._port:
            return False

        url = f"http://127.0.0.1:{self._port}/question/{question.request_id}/reject"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url) as resp:
                    return resp.status == 200
        except Exception as e:
            log.error(f"Failed to reject question: {e}")
            return False

    async def _poll_questions(self) -> list[dict]:
        """Poll for pending questions via HTTP API."""
        if not self._port:
            return []

        url = f"http://127.0.0.1:{self._port}/question"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception:
            pass
        return []

    async def run(
        self, prompt: str, session_id: str | None = None
    ) -> AsyncIterator[Event]:
        """Run OpenCode, yielding (event_type, content) tuples.

        Events:
            ("session_id", str) - Session ID for continuity
            ("text", str) - Incremental response text
            ("tool", str) - Tool invocation description
            ("question", Question) - Question from AI needing answer
            ("result", OpenCodeResult) - Final result with stats
            ("error", str) - Error message
        """
        self._port = _find_free_port()
        cmd = self._build_command(prompt, session_id, self._port)
        state = RunState()

        log.info(f"OpenCode: {prompt[:50]}...")
        self._log_prompt(prompt)

        # Start question polling task
        question_poll_task: asyncio.Task | None = None

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.working_dir,
                limit=10 * 1024 * 1024,  # 10MB buffer limit to handle large JSON lines
            )

            if self.process.stdout is None:
                raise RuntimeError("OpenCode process stdout missing")

            # Start polling for questions after a short delay
            async def poll_questions_loop():
                await asyncio.sleep(2)  # Wait for server to start
                while not self._cancelled:
                    try:
                        questions = await self._poll_questions()
                        for q in questions:
                            request_id = q.get("id") or q.get("requestID")
                            if request_id and self.question_callback:
                                question = Question(
                                    request_id=request_id,
                                    questions=q.get("questions", [])
                                )
                                try:
                                    answers = await self.question_callback(question)
                                    await self._answer_question(question, answers)
                                except Exception as e:
                                    log.error(f"Question callback error: {e}")
                                    await self._reject_question(question)
                    except Exception as e:
                        log.debug(f"Question poll error: {e}")
                    await asyncio.sleep(1)

            if self.question_callback:
                question_poll_task = asyncio.create_task(poll_questions_loop())

            async for raw_line in self.process.stdout:
                if self._cancelled:
                    break

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
                        event_type, data = result

                        # Handle questions inline if we see them in stdout
                        if event_type == "question" and isinstance(data, Question):
                            yield result
                            if self.question_callback:
                                try:
                                    answers = await self.question_callback(data)
                                    await self._answer_question(data, answers)
                                except Exception as e:
                                    log.error(f"Question callback error: {e}")
                                    await self._reject_question(data)
                        else:
                            yield result

            await self.process.wait()

            if self.process.returncode and self.process.returncode != 0:
                state.saw_error = True

            if not state.saw_result and not state.saw_error:
                yield self._make_fallback_error(state)

        except Exception as e:
            log.exception("OpenCode runner error")
            yield ("error", str(e))

        finally:
            self._cancelled = True
            if question_poll_task:
                question_poll_task.cancel()
                try:
                    await question_poll_task
                except asyncio.CancelledError:
                    pass

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
        self._cancelled = True
        if self.process:
            self.process.terminate()
