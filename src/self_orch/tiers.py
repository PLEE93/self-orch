"""Difficulty-tier -> model-id registry.

Lets a user configure, per install, which concrete model handles which
difficulty tier instead of hand-typing a literal model id into every seat
spec. This mirrors the model-tier discipline used by the Aurelius self-orch
governor this rail was extracted from: cheap/wide workers on one tier,
seats that need real judgment on another, and the single heaviest
plan/architecture step on its own tier.

Tiers are an indirection layer only -- never required. `resolve_tier("tier2")`
returns whatever model id is configured for tier 2 (env override, else the
built-in default). A literal model id ("glm-5.3", "grok-4.6", "claude-...")
is returned unchanged by `resolve_tier`, so nothing breaks for anyone who
never touches tiers.
"""

from __future__ import annotations

import os

# Built-in defaults. Override any of these with the matching env var below --
# they are starting points, not a claim about which model is "best"; check
# your provider's current model list before shipping.
#   tier1 -> mechanical/worker seats: mass work, cheap, wide, run many in parallel
#   tier2 -> seats that need real understanding: analysis, judgment, synthesis
#   tier3 -> falsification / verification seats -- deliberately a DIFFERENT
#            model family than tier1/tier2 by default, so a verifier is not
#            grading its own family's work
#   tier4 -> the single heaviest step in a mission: plan, architecture, diagnosis
_DEFAULTS = {
    "tier1": "glm-5.3",
    "tier2": "claude-sonnet-4-5",
    "tier3": "gpt-5.1",
    "tier4": "claude-opus-4-5",
}

_ENV_VARS = {
    "tier1": "SELF_ORCH_TIER1_MODEL",
    "tier2": "SELF_ORCH_TIER2_MODEL",
    "tier3": "SELF_ORCH_TIER3_MODEL",
    "tier4": "SELF_ORCH_TIER4_MODEL",
}

_ALIASES = {
    "tier:1": "tier1", "tier 1": "tier1", "t1": "tier1",
    "tier:2": "tier2", "tier 2": "tier2", "t2": "tier2",
    "tier:3": "tier3", "tier 3": "tier3", "t3": "tier3",
    "tier:4": "tier4", "tier 4": "tier4", "t4": "tier4",
}

TIERS = tuple(_DEFAULTS)


def _normalize(model: str) -> str | None:
    m = (model or "").strip().lower()
    if m in _DEFAULTS:
        return m
    if m in _ALIASES:
        return _ALIASES[m]
    return None


def is_tier(model: str) -> bool:
    return _normalize(model) is not None


def resolve_tier(model: str) -> str:
    """Expand a tier alias ("tier1".. "tier4", "tier:1", "t1", ...) into the
    model id configured for it (env override, else the built-in default).
    A literal model id passes through unchanged.
    """
    tier = _normalize(model)
    if tier is None:
        return model
    env_val = os.environ.get(_ENV_VARS[tier], "").strip()
    return env_val or _DEFAULTS[tier]


def tier_table() -> dict[str, dict[str, str]]:
    """Current tier -> resolved-model table plus provenance, for `doctor`/`doctrine`."""
    out: dict[str, dict[str, str]] = {}
    for tier in _DEFAULTS:
        env_name = _ENV_VARS[tier]
        env_val = os.environ.get(env_name, "").strip()
        out[tier] = {
            "model": env_val or _DEFAULTS[tier],
            "source": f"env:{env_name}" if env_val else "default",
            "env_var": env_name,
        }
    return out
