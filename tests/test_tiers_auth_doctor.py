"""Tests for the three features the rail advertises but never covered:
difficulty tiers, account-auth-token credential reuse, and the `doctor`
surface that is supposed to make both visible to a user.

Each test names the failure it exists to prevent.
"""

import argparse

import pytest

from self_orch import auth, cli, install, providers, tiers


# ── tiers ────────────────────────────────────────────────────────────────────

def test_tier_aliases_all_resolve():
    """Prevents: a user writes 'tier 1' or 't1' and it is silently treated as a
    literal model id, then fails at the provider with a confusing error."""
    for alias in ("tier1", "TIER1", "tier:1", "tier 1", "t1"):
        assert tiers.is_tier(alias), alias
        assert tiers.resolve_tier(alias) == tiers.resolve_tier("tier1")


def test_literal_model_passes_through_untouched():
    """Prevents: tier indirection breaking every existing spec that uses a
    literal model id."""
    for literal in ("glm-5.3", "grok-4.6", "claude-opus-4-5", "gpt-5.1"):
        assert not tiers.is_tier(literal)
        assert tiers.resolve_tier(literal) == literal


def test_env_override_wins_over_default(monkeypatch):
    """Prevents: the documented env var being ignored, so a user points a tier
    at their own model and the rail keeps calling the built-in default."""
    monkeypatch.setenv("SELF_ORCH_TIER3_MODEL", "my-own-verifier")
    assert tiers.resolve_tier("tier3") == "my-own-verifier"
    assert tiers.tier_table()["tier3"]["model"] == "my-own-verifier"
    assert tiers.tier_table()["tier3"]["source"] == "env:SELF_ORCH_TIER3_MODEL"


def test_blank_env_falls_back_to_default(monkeypatch):
    """Prevents: an empty env var (VAR= in a .env file) resolving to the empty
    string and being sent to the API as the model id."""
    monkeypatch.setenv("SELF_ORCH_TIER1_MODEL", "   ")
    assert tiers.resolve_tier("tier1").strip() != ""
    assert tiers.tier_table()["tier1"]["source"] == "default"


def test_every_tier_reports_its_env_var_name():
    """Prevents: `doctor` telling a user a tier is on a default without telling
    them which variable to set to change it."""
    table = tiers.tier_table()
    assert set(table) == set(tiers.TIERS)
    for tier, row in table.items():
        assert row["env_var"].startswith("SELF_ORCH_")
        assert row["model"]


# ── account auth tokens ──────────────────────────────────────────────────────

def test_credential_status_covers_all_three_vendors():
    """Prevents: doctor silently omitting a vendor, so a user cannot tell why
    one family of seats fails while the others work."""
    status = auth.credential_status()
    assert set(status) == {"anthropic", "openai", "xai"}
    for vendor, value in status.items():
        assert isinstance(value, str) and value, vendor


def test_oauth_credential_selects_anthropic_messages_shape(monkeypatch):
    """Prevents: an account auth token being sent as an API key, which the
    Anthropic API rejects -- the headline account-login path silently broken."""
    monkeypatch.setattr(
        providers, "resolve_anthropic_credential",
        lambda: auth.Credential("oauth", "tok-abc", "file:~/.claude/.credentials.json"),
    )
    p = providers.resolve_provider("claude-sonnet-4-5")
    assert p.chat_shape == "anthropic"
    assert p.auth_scheme == "bearer"
    assert p.extra_headers.get("anthropic-beta") == "oauth-2025-04-20"
    assert providers.chat_url(p).endswith("/messages")
    assert "credentials.json" in p.credential_source


def test_api_key_credential_uses_x_api_key(monkeypatch):
    """Prevents: regressing the plain API-key path while adding OAuth."""
    monkeypatch.setattr(
        providers, "resolve_anthropic_credential",
        lambda: auth.Credential("api_key", "sk-ant-xxx", "env:ANTHROPIC_API_KEY"),
    )
    p = providers.resolve_provider("claude-opus-4-5")
    assert p.auth_scheme == "x-api-key"
    assert "anthropic-beta" not in (p.extra_headers or {})


def test_missing_credential_raises_actionable_error(monkeypatch):
    """Prevents: a blank failure that does not tell the user which of the two
    ways to authenticate."""
    monkeypatch.setattr(providers, "resolve_anthropic_credential", lambda: None)
    with pytest.raises(providers.ProviderError) as e:
        providers.resolve_provider("claude-sonnet-4-5")
    msg = str(e.value).lower()
    assert "anthropic_api_key" in msg and "claude code" in msg


# ── tier -> provider integration ─────────────────────────────────────────────

def test_provider_resolution_expands_a_tier(monkeypatch):
    """Prevents: tiers resolving for display but never reaching the dispatch
    path, so a seat declared as tier1 is sent to the API literally as 'tier1'."""
    monkeypatch.setenv("SELF_ORCH_TIER1_MODEL", "glm-5.3")
    monkeypatch.setenv("ZAI_API_KEY", "zzz")
    p = providers.resolve_provider("tier1")
    assert p.default_model == "glm-5.3"
    assert p.name == "zai"


def test_model_family_sees_through_a_tier(monkeypatch):
    """Prevents: the verifier/builder diversity check comparing the strings
    'tier2' and 'tier3' instead of the model families behind them, which would
    make the check pass while a model grades its own family's work."""
    monkeypatch.setenv("SELF_ORCH_TIER2_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("SELF_ORCH_TIER3_MODEL", "claude-opus-4-5")
    assert providers.model_family("tier2") == providers.model_family("tier3")
    monkeypatch.setenv("SELF_ORCH_TIER3_MODEL", "gpt-5.1")
    assert providers.model_family("tier2") != providers.model_family("tier3")


# ── doctor surface ───────────────────────────────────────────────────────────

def test_doctor_reports_tiers_and_credentials():
    """Prevents: tier_table() and credential_status() existing but never being
    called -- a user with no way to see what their install will actually run."""
    payload = install.doctor("claude")
    assert "tiers" in payload and set(payload["tiers"]) == set(tiers.TIERS)
    assert "credentials" in payload
    assert set(payload["credentials"]) == {"anthropic", "openai", "xai"}


def test_doctor_accepts_an_account_token_only_install(monkeypatch, capsys):
    """Prevents the exact bug this change fixes: a user authenticated through
    their Claude Code session holds NO api key, and doctor declared the install
    broken -- rejecting the headline setup the project advertises."""
    monkeypatch.setattr(cli, "install_doctor", lambda _a: {
        "cli": "/usr/local/bin/self-orch",
        "keys": {"ZAI_API_KEY": False, "XAI_API_KEY": False},
        "credentials": {"anthropic": "oauth via file:~/.claude/.credentials.json",
                        "openai": "none", "xai": "none"},
        "tiers": tiers.tier_table(),
    })
    rc = cli.cmd_doctor(argparse.Namespace(agent="claude"))
    assert rc == 0, "account-token-only install must be reported as usable"


def test_doctor_still_fails_with_no_credential_at_all(monkeypatch):
    """Prevents: over-correcting into reporting every broken install as fine."""
    monkeypatch.setattr(cli, "install_doctor", lambda _a: {
        "cli": "/usr/local/bin/self-orch",
        "keys": {"ZAI_API_KEY": False},
        "credentials": {"anthropic": "none", "openai": "none", "xai": "none"},
        "tiers": tiers.tier_table(),
    })
    assert cli.cmd_doctor(argparse.Namespace(agent="claude")) == 1
