"""HTTP client for the OpenCode server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Callable

import aiohttp

from src.runners.opencode.models import Question

log = logging.getLogger("opencode")


class OpenCodeClient:
    """HTTP + SSE transport for OpenCode."""

    def __init__(self, server_url: str | None = None):
        self.server_url = server_url or self._resolve_server_url()
        self._auth = self._build_auth()

    @property
    def auth(self) -> aiohttp.BasicAuth | None:
        return self._auth

    def _resolve_server_url(self) -> str:
        base_url = os.getenv("OPENCODE_SERVER_URL")
        if base_url:
            return base_url.rstrip("/")

        host = os.getenv("OPENCODE_SERVER_HOST", "127.0.0.1")
        port = os.getenv("OPENCODE_SERVER_PORT", "4096")
        return f"http://{host}:{port}"

    def _build_auth(self) -> aiohttp.BasicAuth | None:
        password = os.getenv("OPENCODE_SERVER_PASSWORD")
        if not password:
            return None
        username = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
        return aiohttp.BasicAuth(username, password)

    def _make_url(self, path: str) -> str:
        return f"{self.server_url}{path}"

    async def request_json(
        self, session: aiohttp.ClientSession, method: str, url: str, **kwargs
    ) -> object | None:
        async with session.request(method, url, **kwargs) as resp:
            if resp.status == 204:
                return None
            text = await resp.text()
            if resp.status >= 400:
                detail = text.strip() or resp.reason
                raise RuntimeError(f"OpenCode HTTP {resp.status}: {detail}")
            if not text:
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text

    async def check_health(self, session: aiohttp.ClientSession) -> None:
        url = self._make_url("/global/health")
        response = await self.request_json(session, "GET", url)
        if isinstance(response, dict) and response.get("healthy") is True:
            return
        raise RuntimeError("OpenCode server unhealthy or unreachable")

    async def create_session(
        self, session: aiohttp.ClientSession, session_name: str | None
    ) -> str:
        payload: dict[str, object] = {}
        if session_name:
            payload["title"] = session_name
        payload["permission"] = [{"permission": "*", "action": "allow", "pattern": "*"}]
        url = self._make_url("/session")
        response = await self.request_json(session, "POST", url, json=payload)
        if isinstance(response, dict):
            session_id = response.get("id") or response.get("sessionID")
            if isinstance(session_id, str) and session_id:
                return session_id
        raise RuntimeError("OpenCode session creation failed")

    async def send_message(
        self,
        session: aiohttp.ClientSession,
        session_id: str,
        prompt: str,
        model_payload: dict | None,
        agent: str,
        reasoning_mode: str,
    ) -> object | None:
        body: dict[str, object] = {
            "parts": [{"type": "text", "text": prompt}],
        }
        if model_payload:
            body["model"] = model_payload
        if agent:
            body["agent"] = agent
        if reasoning_mode == "high" and model_payload:
            body["model"] = {**model_payload, "variant": "high"}
        url = self._make_url(f"/session/{session_id}/message")
        return await self.request_json(session, "POST", url, json=body)

    async def answer_question(
        self,
        session: aiohttp.ClientSession,
        question: Question,
        answers: list[list[str]],
    ) -> bool:
        url = self._make_url(f"/question/{question.request_id}/reply")
        try:
            await self.request_json(session, "POST", url, json={"answers": answers})
            log.info(f"Answered question {question.request_id}")
            return True
        except Exception as e:
            log.error(f"Failed to answer question: {e}")
            return False

    async def reject_question(
        self, session: aiohttp.ClientSession, question: Question
    ) -> bool:
        url = self._make_url(f"/question/{question.request_id}/reject")
        try:
            await self.request_json(session, "POST", url)
            return True
        except Exception as e:
            log.error(f"Failed to reject question: {e}")
            return False

    async def abort_session(
        self, session: aiohttp.ClientSession, session_id: str
    ) -> None:
        url = self._make_url(f"/session/{session_id}/abort")
        try:
            await self.request_json(session, "POST", url)
        except Exception as e:
            log.debug(f"Failed to abort session {session_id}: {e}")

    async def stream_events(
        self,
        session: aiohttp.ClientSession,
        queue: asyncio.Queue[dict],
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        urls = [self._make_url("/event"), self._make_url("/global/event")]
        headers = {"Accept": "text/event-stream"}

        for url in urls:
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 404:
                        continue
                    if resp.status >= 400:
                        raise RuntimeError(f"OpenCode SSE HTTP {resp.status}")
                    await self.read_sse_stream(resp, queue, should_stop=should_stop)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug(f"SSE connect failed for {url}: {e}")
        raise RuntimeError("Failed to connect to OpenCode SSE stream")

    async def read_sse_stream(
        self,
        resp: aiohttp.ClientResponse,
        queue: asyncio.Queue[dict],
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        data_lines: list[str] = []
        async for raw in resp.content:
            if should_stop and should_stop():
                break
            line = raw.decode("utf-8", errors="replace").strip("\r\n")
            if not line:
                if not data_lines:
                    continue
                payload = "\n".join(data_lines)
                data_lines = []
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    await queue.put(event)
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
