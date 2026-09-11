"""Account auth-token discovery.

Most people already have a live, authenticated session for at least one
model vendor, because they run a coding agent (Claude Code, Codex CLI, a
Grok CLI) day to day. This module lets self-orch *reuse* that session --
the same account auth token Claude/Codex/Grok already minted for their own
CLI -- instead of forcing a second, separate API-key setup.

Resolution order per vendor, first hit wins:
  1. An explicit API key env var (unchanged, existing behavior).
  2. A locally-authenticated CLI's own credential file, read-only. Nothing
     here performs an OAuth flow, mints a token, or writes one back -- this
     repo does not want to be a second place your account token is created.
     It reads a token some *other* official CLI already produced, the same
     way that CLI itself would read it.

If neither is present, the caller raises -- never fabricate a credential.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Credential:
    kind: str    # "api_key" | "oauth"
    value: str
    source: str  # human-readable provenance, shown by `self-orch doctor`


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _claude_code_oauth() -> Credential | None:
    """~/.claude/.credentials.json -- written by Claude Code's own login /
    `claude setup-token` flow. Read-only."""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")).expanduser()
    data = _read_json(home / ".credentials.json")
    if not data:
        return None
    token = (
        (data.get("claudeAiOauth") or {}).get("accessToken")
        or data.get("accessToken")
        or data.get("access_token")
    )
    if token:
        return Credential("oauth", str(token), "claude-code:.credentials.json")
    return None


def _codex_oauth() -> Credential | None:
    """~/.codex/auth.json -- written by `codex login`. Read-only."""
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    data = _read_json(home / "auth.json")
    if not data:
        return None
    token = (
        (data.get("tokens") or {}).get("access_token")
        or data.get("access_token")
        or data.get("OPENAI_API_KEY")
    )
    if token:
        kind = "api_key" if str(token).startswith("sk-") else "oauth"
        return Credential(kind, str(token), "codex-cli:auth.json")
    return None


def _grok_oauth() -> Credential | None:
    """Grok CLI credential file. Path/shape is less standardized across
    releases than Claude Code / Codex; check the common candidates.
    Read-only."""
    home = Path(os.environ.get("GROK_HOME") or (Path.home() / ".grok")).expanduser()
    for name in ("auth.json", "credentials.json", ".credentials.json"):
        data = _read_json(home / name)
        if not data:
            continue
        token = data.get("api_key") or data.get("access_token") or data.get("apiKey")
        if token:
            kind = "oauth" if data.get("access_token") else "api_key"
            return Credential(kind, str(token), f"grok-cli:{name}")
    return None


def resolve_anthropic_credential() -> Credential | None:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if key:
        return Credential("api_key", key, "env:ANTHROPIC_API_KEY")
    return _claude_code_oauth()


def resolve_openai_credential() -> Credential | None:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if key:
        return Credential("api_key", key, "env:OPENAI_API_KEY")
    return _codex_oauth()


def resolve_xai_credential() -> Credential | None:
    for env_name in ("XAI_API_KEY", "GROK_API_KEY"):
        key = (os.environ.get(env_name) or "").strip()
        if key:
            return Credential("api_key", key, f"env:{env_name}")
    return _grok_oauth()


def credential_status() -> dict[str, str]:
    """For `self-orch doctor`: which vendors resolved a credential and from where."""
    out: dict[str, str] = {}
    for name, fn in (
        ("anthropic", resolve_anthropic_credential),
        ("openai", resolve_openai_credential),
        ("xai", resolve_xai_credential),
    ):
        cred = fn()
        out[name] = f"{cred.kind} via {cred.source}" if cred else "none"
    return out
