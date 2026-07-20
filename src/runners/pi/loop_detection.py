"""Guard against degenerate tool-call loops (Qwen Code-style).

Always-on tier for models in model_traits.LOOP_GUARD_MODELS:
- Period 1: N consecutive identical calls (same tool + args)
- Period 2..K: M repetitions of a repeating A-B-… pattern

See QwenLM/qwen-code#5015, PR #5573, and loopDetectionService.ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.model_traits import needs_loop_guard

CONSECUTIVE_IDENTICAL_TOOL_CALL_THRESHOLD = 5
CYCLING_REPETITIONS = 3
MAX_CYCLING_PERIOD = 3

# Backwards-compatible alias.
ALTERNATING_PATTERN_CYCLES = CYCLING_REPETITIONS


def _canonical_tool_args(args: object) -> str:
    if args is None:
        return ""
    if isinstance(args, dict):
        return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return str(args)


def model_needs_loop_detection(model: str | None) -> bool:
    return needs_loop_guard(model)


def loop_detector_for_model(
    model: str | None,
    *,
    identical_threshold: int = CONSECUTIVE_IDENTICAL_TOOL_CALL_THRESHOLD,
    cycling_repetitions: int = CYCLING_REPETITIONS,
    max_cycling_period: int = MAX_CYCLING_PERIOD,
    alternating_cycles: int | None = None,
) -> PiToolLoopDetector | None:
    if not model_needs_loop_detection(model):
        return None
    if alternating_cycles is not None:
        cycling_repetitions = alternating_cycles
    return PiToolLoopDetector(
        identical_threshold=identical_threshold,
        cycling_repetitions=cycling_repetitions,
        max_cycling_period=max_cycling_period,
    )


@dataclass(frozen=True)
class LoopGuardHit:
    tool_name: str
    period: int  # 1 = identical, 2 = A-B, 3 = A-B-C, …
    repetitions: int  # times the period repeated before firing


def _pattern_description(hit: LoopGuardHit) -> str:
    if hit.period == 1:
        return f"{hit.repetitions} identical {hit.tool_name} tool calls"
    return (
        f"{hit.repetitions} cycles of a repeating {hit.period}-step "
        f"{hit.tool_name} tool pattern"
    )


def _matches_repeating_pattern(keys: list[str], period: int) -> bool:
    if len(keys) % period:
        return False
    pattern = keys[:period]
    if period >= 2 and len(set(pattern)) != period:
        return False
    return all(keys[i] == pattern[i % period] for i in range(len(keys)))


@dataclass
class PiToolLoopDetector:
    """Detect repeating tool-call loops via a single period-based algorithm."""

    identical_threshold: int = CONSECUTIVE_IDENTICAL_TOOL_CALL_THRESHOLD
    cycling_repetitions: int = CYCLING_REPETITIONS
    max_cycling_period: int = MAX_CYCLING_PERIOD
    _recent_keys: list[str] = field(default_factory=list, init=False, repr=False)
    _last_hit: LoopGuardHit | None = field(default=None, init=False, repr=False)

    @property
    def threshold(self) -> int:
        return self.identical_threshold

    @property
    def alternating_cycles(self) -> int:
        return self.cycling_repetitions

    @property
    def consecutive(self) -> int:
        if self._last_hit and self._last_hit.period == 1:
            return self._last_hit.repetitions
        if not self._recent_keys:
            return 0
        streak = 1
        last = self._recent_keys[-1]
        for key in reversed(self._recent_keys[:-1]):
            if key != last:
                break
            streak += 1
        return streak

    @property
    def last_hit(self) -> LoopGuardHit | None:
        return self._last_hit

    def reset(self) -> None:
        self._recent_keys.clear()
        self._last_hit = None

    def _tool_key(self, tool_name: str, args: object) -> str:
        return f"{tool_name}:{_canonical_tool_args(args)}"

    def _repetitions_for_period(self, period: int) -> int:
        return self.identical_threshold if period == 1 else self.cycling_repetitions

    def _max_history(self) -> int:
        return self.max_cycling_period * max(
            self.identical_threshold,
            self.cycling_repetitions,
        )

    def observe(self, tool_name: str, args: object) -> LoopGuardHit | None:
        """Record a tool call; return a hit when a loop threshold is reached."""
        key = self._tool_key(tool_name, args)
        self._recent_keys.append(key)
        max_history = self._max_history()
        if len(self._recent_keys) > max_history:
            self._recent_keys[:] = self._recent_keys[-max_history:]

        for period in range(1, self.max_cycling_period + 1):
            reps = self._repetitions_for_period(period)
            window = period * reps
            if len(self._recent_keys) < window:
                continue
            slice_keys = self._recent_keys[-window:]
            if _matches_repeating_pattern(slice_keys, period):
                hit = LoopGuardHit(
                    tool_name=tool_name,
                    period=period,
                    repetitions=reps,
                )
                self._last_hit = hit
                return hit

        return None

    def loop_notice(self, hit: LoopGuardHit) -> str:
        return (
            f"Loop guard: stopped after {_pattern_description(hit)}. "
            "Asking the agent to summarize…\n\n"
        )

    def recovery_prompt(self, hit: LoopGuardHit) -> str:
        return (
            f"[Switch loop guard] You hit a {_pattern_description(hit)} without "
            "making progress. Do not retry those commands or similar searches. "
            "Reply to the user in plain text only: summarize what you learned so "
            "far, explain any blockers clearly, and ask what they want to do next."
        )


IdenticalToolCallLoopDetector = PiToolLoopDetector
