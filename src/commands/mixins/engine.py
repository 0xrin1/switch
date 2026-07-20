"""Engine and model configuration commands."""

from __future__ import annotations

from src.commands.mixins.base import CommandMixinBase
from src.commands.registry import command
from src.engines import normalize_engine, reasoning_engine_names
from src.runners.cursor.config import parse_model_variant, resolve_model_variant


class EngineCommandsMixin(CommandMixinBase):
    @command("/agent", exact=False)
    async def agent(self, body: str) -> bool:
        """Switch active engine."""
        parts = body.strip().lower().split()
        if len(parts) < 2:
            self.bot.send_reply("Usage: /agent oc|cc|pi|cursor")
            return True

        engine = normalize_engine(parts[1])
        if not engine:
            self.bot.send_reply("Usage: /agent oc|cc|pi|cursor")
            return True

        await self.bot.sessions.update_engine(self.bot.session_name, engine)
        self.bot.send_reply(f"Active engine set to {engine}.")
        return True

    @command("/thinking", exact=False)
    async def thinking(self, body: str) -> bool:
        """Show or set the model's reasoning effort."""
        parts = body.strip().lower().split()
        session = self.bot.sessions.get(self.bot.session_name)
        if not session:
            self.bot.send_reply("Session not found.")
            return True

        engine = (session.active_engine or "").strip().lower()
        if engine not in reasoning_engine_names():
            self.bot.send_reply("/thinking only applies to reasoning-capable sessions.")
            return True

        if len(parts) < 2:
            variant = parse_model_variant(session.model_id) if engine == "cursor" else None
            mode = variant.thinking if variant else session.reasoning_mode
            self.bot.send_reply(f"Thinking: {mode}")
            return True

        mode = parts[1]
        allowed = {
            "cursor": ("low", "medium", "high"),
            "pi": ("off", "minimal", "low", "medium", "high", "xhigh"),
            "opencode": ("normal", "high"),
        }.get(engine, ("normal", "high"))
        if mode not in allowed:
            self.bot.send_reply(f"Usage: /thinking {'|'.join(allowed)}")
            return True

        if engine == "cursor":
            model_id = resolve_model_variant(session.model_id, thinking=mode)
            if model_id is None:
                self.bot.send_reply(
                    "This Cursor model does not expose configurable thinking levels."
                )
                return True
            await self.bot.sessions.update_model(self.bot.session_name, model_id)

        await self.bot.sessions.update_reasoning_mode(self.bot.session_name, mode)
        self.bot.send_reply(f"Thinking: {mode}")
        return True

    @command("/speed", exact=False)
    async def speed(self, body: str) -> bool:
        """Show or set the speed variant for models that provide one."""
        parts = body.strip().lower().split()
        session = self.bot.sessions.get(self.bot.session_name)
        if not session:
            self.bot.send_reply("Session not found.")
            return True

        variant = parse_model_variant(session.model_id)
        if (session.active_engine or "").strip().lower() != "cursor" or variant is None:
            self.bot.send_reply("This model does not expose configurable speed variants.")
            return True

        if len(parts) < 2:
            self.bot.send_reply(f"Speed: {'fast' if variant.fast else 'standard'}")
            return True
        if parts[1] not in {"standard", "fast"}:
            self.bot.send_reply("Usage: /speed standard|fast")
            return True

        model_id = resolve_model_variant(session.model_id, fast=parts[1] == "fast")
        assert model_id is not None
        await self.bot.sessions.update_model(self.bot.session_name, model_id)
        self.bot.send_reply(f"Speed: {parts[1]}")
        return True

    @command("/model", exact=False)
    async def model(self, body: str) -> bool:
        """Set model ID."""
        parts = body.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            self.bot.send_reply("Usage: /model <model-id>")
            return True

        model_id = parts[1].strip()
        await self.bot.sessions.update_model(self.bot.session_name, model_id)
        self.bot.send_reply(f"Model set to {model_id}.")
        return True

    @command("/handoff", exact=False)
    async def handoff(self, body: str) -> bool:
        """Hand off to another engine."""
        parts = body.strip().split(maxsplit=2)
        if len(parts) < 2:
            self.bot.send_reply("Usage: /handoff <engine> [prompt]\nEngines: pi, claude, opencode, cursor")
            return True

        engine = normalize_engine(parts[1])
        if not engine:
            self.bot.send_reply("Usage: /handoff <engine> [prompt]\nEngines: pi, claude, opencode, cursor")
            return True

        if self.bot.processing:
            self.bot.send_reply("Already processing. /cancel first.")
            return True

        prompt = parts[2].strip() if len(parts) > 2 else None
        if not prompt:
            msg = self._latest_message("assistant", limit=10)
            if msg:
                prompt = msg.content
            if not prompt:
                self.bot.send_reply("No prompt and no assistant messages to hand off.")
                return True

        self.bot.send_reply(f"Handing off to {engine}...")
        await self.bot.session.run_handoff(engine, prompt)
        return True
