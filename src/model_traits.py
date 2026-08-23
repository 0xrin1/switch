"""Per-model traits for locally served models (Helga).

Single home for model-name pattern knowledge. Runners and mixins consult
these helpers instead of hardcoding prefixes — when a model is added or
moved between backends, this is the only file to touch.
"""

from __future__ import annotations

# vLLM exposes pause/resume control endpoints (used for hard-cancel).
VLLM_MODEL_PREFIXES: tuple[str, ...] = (
    "glm_vllm/",
    "heretic_local/",
    "gemma4_helga/",
    "qwen35-",  # Heretic models often use qwen35 prefix
    "qwen38_helga/",  # vLLM TP4 (heretic-ara + DFlash2, :8023) since 2026-08-22
)
VLLM_MODEL_SUBSTRINGS: tuple[str, ...] = ("heretic",)

# Served by llama.cpp — no vLLM pause/resume, even when the name says "heretic".
LLAMACPP_MODEL_PREFIXES: tuple[str, ...] = (
    "qwen36_helga/",
    "qwen36_heretic_llamacpp/",
    "muse_glimmer_helga/",
)

# Served by SGLang, whose OpenAI-compatible API also lacks vLLM's
# pause/resume control endpoints.
SGLANG_MODEL_PREFIXES: tuple[str, ...] = ()

# Models known to spin on empty tool feedback without a loop guard.
LOOP_GUARD_MODELS: frozenset[str] = frozenset(
    {
        "qwen36_helga/qwen3.6-27b-heretic-v2",
    }
)


def _norm(model_id: str | None) -> str:
    return (model_id or "").strip().lower()


def is_llamacpp_served(model_id: str | None) -> bool:
    return _norm(model_id).startswith(LLAMACPP_MODEL_PREFIXES)


def is_sglang_served(model_id: str | None) -> bool:
    return _norm(model_id).startswith(SGLANG_MODEL_PREFIXES)


def is_vllm_served(model_id: str | None) -> bool:
    model = _norm(model_id)
    if not model or is_llamacpp_served(model) or is_sglang_served(model):
        return False
    return model.startswith(VLLM_MODEL_PREFIXES) or any(
        s in model for s in VLLM_MODEL_SUBSTRINGS
    )


def needs_loop_guard(model_id: str | None) -> bool:
    return _norm(model_id) in LOOP_GUARD_MODELS
