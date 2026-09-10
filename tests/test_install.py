import json
from pathlib import Path

from self_orch.install import detect_agent, extra_rule_files, install, skill_targets


def test_detect_grok(monkeypatch):
    monkeypatch.setenv("GROK_HOME", "/tmp/fake-grok")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CURSOR_AGENT", raising=False)
    assert detect_agent() == "grok"


def test_detect_claude(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert detect_agent() == "claude-code"


def test_detect_codex(monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("CODEX_HOME", "/tmp/codex")
    assert detect_agent() == "codex"


def test_detect_cursor(monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("CURSOR_TRACE_ID", "abc")
    assert detect_agent() == "cursor"


def test_skill_targets_per_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grokhome"))
    grok = skill_targets("grok", "user", tmp_path / "proj")
    assert grok[0] == tmp_path / "grokhome" / "skills" / "self-orch"
    claude = skill_targets("claude-code", "user", tmp_path / "proj")
    assert ".claude" in str(claude[0])
    cursor = skill_targets("cursor", "project", tmp_path / "proj")
    assert cursor[0] == tmp_path / "proj" / ".cursor" / "skills" / "self-orch"


def test_dry_run_does_not_write(tmp_path):
    result = install(
        agent="grok",
        scope="user",
        prefix=tmp_path / "prefix",
        project=tmp_path / "proj",
        dry_run=True,
    )
    assert result["agent"] == "grok"
    assert not (tmp_path / "prefix").exists()
    assert extra_rule_files("codex", "project", tmp_path / "proj")[0].name == "AGENTS.md"


def test_install_skip_cli_writes_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "home" / ".grok"))
    (tmp_path / "home").mkdir()
    result = install(
        agent="grok",
        scope="user",
        prefix=tmp_path / "unused-prefix",
        project=tmp_path / "proj",
        skip_cli=True,
    )
    written = result["written"]
    assert any(p.endswith("SKILL.md") for p in written)
    skill = Path(written[0])
    assert skill.is_file()
    text = skill.read_text()
    assert "governor" in text.lower()
    assert json.dumps(result)  # serializable
