"""Cursor ACP transport: client lifecycle, session open, prompt task."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.runners.cursor.acp import CursorACPClient
from src.runners.cursor.config import CursorConfig
from src.runners.cursor.events import agent_capabilities, supports_session_resume
from src.runners.timeouts import control_plane_timeout_s

log = logging.getLogger("cursor.transport")


class CursorACPTransport:
    def __init__(self, config: CursorConfig):
        self._config = config
        self._client: CursorACPClient | None = None
        self._cancelled = False
        self._agent_capabilities: dict[str, Any] = {}

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def client(self) -> CursorACPClient | None:
        return self._client

    async def start(self, *, argv: list[str], cwd: str) -> CursorACPClient:
        client = CursorACPClient(argv, cwd=cwd)
        await client.start()
        self._client = client
        await self._initialize(client)
        return client

    async def _initialize(self, client: CursorACPClient) -> None:
        timeout = control_plane_timeout_s(
            override=self._config.control_plane_timeout_s,
        )
        initialize_result = await client.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "switch-cursor-acp", "version": "0.1.0"},
            },
            timeout_s=timeout,
        )
        self._agent_capabilities = agent_capabilities(initialize_result)
        await client.request(
            "authenticate",
            {"methodId": self._config.auth_method},
            timeout_s=timeout,
        )

    async def open_session(
        self,
        client: CursorACPClient,
        *,
        session_id: str | None,
        cwd: str,
    ) -> str:
        timeout = control_plane_timeout_s(
            override=self._config.control_plane_timeout_s,
        )
        params: dict[str, Any] = {"cwd": cwd, "mcpServers": []}
        if session_id and await self._restore_session(
            client, session_id=session_id, params=params, timeout=timeout
        ):
            return session_id

        result = await client.request("session/new", params, timeout_s=timeout)
        if not isinstance(result, dict) or not result.get("sessionId"):
            raise RuntimeError(f"Cursor session/new did not return sessionId: {result!r}")
        return str(result["sessionId"])

    async def _restore_session(
        self,
        client: CursorACPClient,
        *,
        session_id: str,
        params: dict[str, Any],
        timeout: float,
    ) -> bool:
        restore_params = {"sessionId": session_id, **params}
        if supports_session_resume(self._agent_capabilities):
            try:
                await client.request(
                    "session/resume",
                    restore_params,
                    timeout_s=timeout,
                )
                client.drain_events()
                return True
            except Exception:
                log.warning(
                    "Cursor session/resume failed; trying session/load",
                    exc_info=True,
                )

        try:
            await client.request(
                "session/load",
                restore_params,
                timeout_s=timeout,
            )
        except Exception:
            log.warning(
                "Cursor session/load failed; starting a new session",
                exc_info=True,
            )
            return False

        # session/load MUST replay history as session/update before it returns.
        # Those notifications are not the current turn.
        dropped = client.drain_events()
        if dropped:
            log.debug(
                "Dropped %s Cursor ACP session/load replay event(s)",
                dropped,
            )
        return True

    def discard_pre_prompt_events(self) -> int:
        client = self._client
        if client is None:
            return 0
        return client.drain_events()

    def start_prompt(
        self,
        client: CursorACPClient,
        *,
        session_id: str,
        prompt: str,
    ) -> asyncio.Task[Any]:
        # Agent turns stream on a separate notification channel. The prompt RPC
        # must not use a wall-clock cap — completion is driven by the shared
        # queue pipeline (trailing-event idle timeout + finalize).
        return asyncio.create_task(
            client.request(
                "session/prompt",
                {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": prompt}],
                },
                timeout_s=None,
            )
        )

    async def allow_permission(self, msg: dict[str, Any]) -> None:
        client = self._client
        if not client:
            return
        msg_id = msg.get("id")
        if msg_id is None:
            return
        await client.respond(
            int(msg_id),
            {
                "outcome": {
                    "outcome": "selected",
                    "optionId": self._config.permission_option_id,
                }
            },
        )

    def reader_task(self) -> asyncio.Task | None:
        client = self._client
        if client is None:
            return None
        return client.reader_task

    def cancel(self) -> None:
        self._cancelled = True
        if self._client:
            self._client.terminate()

    async def cleanup(self, *, prompt_task: asyncio.Task | None) -> None:
        if prompt_task and not prompt_task.done():
            prompt_task.cancel()
        if self._client:
            await self._client.close()
            self._client = None
