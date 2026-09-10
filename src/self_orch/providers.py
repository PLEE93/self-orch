"""OpenAI-compatible HTTP providers. Keys come from the environment only."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    default_model: str


class ProviderError(RuntimeError):
    pass


def _first_env(*names: str) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def resolve_provider(model: str) -> Provider:
    """Pick a provider from the model id.

    glm*     -> z.ai coding PAAS
    grok*    -> xAI
    otherwise -> OPENAI_BASE_URL / OPENAI_API_KEY if set
    """
    m = (model or "").strip().lower()
    if m.startswith("glm"):
        key = _first_env("ZAI_API_KEY", "GLM_API_KEY")
        if not key:
            raise ProviderError(
                "model %r needs ZAI_API_KEY or GLM_API_KEY" % model
            )
        base = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/coding/paas/v4").rstrip("/")
        return Provider("zai", base, key, model)
    if m.startswith("grok"):
        key = _first_env("XAI_API_KEY", "GROK_API_KEY")
        if not key:
            raise ProviderError(
                "model %r needs XAI_API_KEY or GROK_API_KEY" % model
            )
        base = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
        return Provider("xai", base, key, model)
    key = _first_env("OPENAI_API_KEY")
    if not key:
        raise ProviderError(
            "model %r is not glm* or grok*; set OPENAI_API_KEY (and optional OPENAI_BASE_URL)"
            % model
        )
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    return Provider("openai", base, key, model)


def chat_url(provider: Provider) -> str:
    return provider.base_url + "/chat/completions"
