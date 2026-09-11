"""HTTP providers. Keys come from the environment, or from an already-
authenticated coding agent's own credential file (see auth.py) -- never
invented, never written back."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .auth import resolve_anthropic_credential, resolve_openai_credential, resolve_xai_credential
from .tiers import resolve_tier


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    default_model: str
    chat_shape: str = "openai"          # "openai" | "anthropic"
    auth_scheme: str = "bearer"         # "bearer" | "x-api-key"
    extra_headers: dict = field(default_factory=dict)
    credential_source: str = ""


class ProviderError(RuntimeError):
    pass


def _first_env(*names: str) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def model_family(model: str) -> str:
    """Coarse provider family for a model id, tier aliases expanded first.
    Used to detect a verification seat sharing a family with a builder seat
    in the same dispatch (see rail.py's model-diversity check)."""
    m = (resolve_tier(model) or "").strip().lower()
    if m.startswith("glm"):
        return "glm"
    if m.startswith("grok"):
        return "grok"
    if m.startswith("claude") or m.startswith(("sonnet", "opus", "haiku", "fable")):
        return "anthropic"
    return "openai"


def resolve_provider(model: str) -> Provider:
    """Pick a provider from the model id. Tier aliases ("tier1".."tier4") are
    expanded to a concrete model id first -- see tiers.py.

    glm*                          -> z.ai coding PAAS (API key only)
    grok*                         -> xAI (API key, or a locally-authenticated
                                     Grok CLI credential)
    claude* / sonnet* / opus* /
      haiku* / fable*             -> Anthropic (API key, or a locally-
                                     authenticated Claude Code OAuth session)
    otherwise (gpt*, astra*, ...) -> OpenAI-compatible (API key, or a locally-
                                     authenticated Codex CLI credential),
                                     OPENAI_BASE_URL override
    """
    model = resolve_tier(model)
    m = (model or "").strip().lower()

    if m.startswith("glm"):
        key = _first_env("ZAI_API_KEY", "GLM_API_KEY")
        if not key:
            raise ProviderError("model %r needs ZAI_API_KEY or GLM_API_KEY" % model)
        base = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/coding/paas/v4").rstrip("/")
        return Provider("zai", base, key, model, credential_source="env:ZAI_API_KEY")

    if m.startswith("grok"):
        cred = resolve_xai_credential()
        if not cred:
            raise ProviderError(
                "model %r needs XAI_API_KEY, GROK_API_KEY, or a locally-authenticated Grok CLI"
                % model
            )
        base = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
        return Provider("xai", base, cred.value, model, credential_source=cred.source)

    if m.startswith("claude") or m.startswith(("sonnet", "opus", "haiku", "fable")):
        cred = resolve_anthropic_credential()
        if not cred:
            raise ProviderError(
                "model %r needs ANTHROPIC_API_KEY, or a locally-authenticated Claude Code "
                "session (~/.claude/.credentials.json)" % model
            )
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1").rstrip("/")
        if cred.kind == "oauth":
            return Provider(
                "anthropic", base, cred.value, model,
                chat_shape="anthropic", auth_scheme="bearer",
                extra_headers={"anthropic-beta": "oauth-2025-04-20"},
                credential_source=cred.source,
            )
        return Provider(
            "anthropic", base, cred.value, model,
            chat_shape="anthropic", auth_scheme="x-api-key",
            credential_source=cred.source,
        )

    cred = resolve_openai_credential()
    if not cred:
        raise ProviderError(
            "model %r is not glm*/grok*/claude*; set OPENAI_API_KEY (and optional "
            "OPENAI_BASE_URL), or authenticate the Codex CLI" % model
        )
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    return Provider("openai", base, cred.value, model, credential_source=cred.source)


def chat_url(provider: Provider) -> str:
    if provider.chat_shape == "anthropic":
        return provider.base_url + "/messages"
    return provider.base_url + "/chat/completions"


def auth_headers(provider: Provider) -> dict:
    if provider.auth_scheme == "x-api-key":
        headers = {"x-api-key": provider.api_key, "anthropic-version": "2023-06-01"}
    else:
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        if provider.chat_shape == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
    headers.update(provider.extra_headers)
    return headers
