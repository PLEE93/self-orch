"""Install the CLI + wire this agent's skill/rules dir.

Stdlib only. The coding agent runs this; the human should not copy files.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/PLEE93/self-orch.git"
DEFAULT_PREFIX = Path.home() / ".self-orch"
MARKER_START = "<!-- self-orch:start -->"
MARKER_END = "<!-- self-orch:end -->"

AGENTS = (
    "claude-code",
    "codex",
    "cursor",
    "grok",
    "opencode",
    "generic",
)

_AGENTS_SNIPPET = """\
<!-- self-orch:start -->
## self-orch

You are the governor. For complex work that benefits from parallel model seats,
read SKILL.md (self-orch skill) and run:

```bash
self-orch dispatch --spec spec.json
```

If `self-orch` is missing from PATH, run `python3 scripts/install.py --agent auto`
from https://github.com/PLEE93/self-orch (or `self-orch install --agent auto`).

Trivial questions: answer directly. Do not dispatch.
<!-- self-orch:end -->
"""


def detect_agent() -> str:
    env = os.environ
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude-code"
    if env.get("CODEX_HOME") or env.get("CODEX_THREAD_ID") or env.get("CODEX_CI"):
        return "codex"
    if env.get("CURSOR_AGENT") or env.get("CURSOR_TRACE_ID") or env.get("CURSOR_SESSION_ID"):
        return "cursor"
    if env.get("GROK_HOME") or env.get("GROK_SESSION_ID") or env.get("GROK_AGENT"):
        return "grok"
    if env.get("OPENCODE") or env.get("OPENCODE_DIR") or env.get("OPENCODE_CONFIG"):
        return "opencode"

    home = Path.home()
    # Running inside a grok/claude/cursor tree is a hint, not proof.
    # Prefer identity the agent already knows; INSTALL.md tells it to pass --agent.
    cwd = Path.cwd()
    if (cwd / ".grok").is_dir() or (home / ".grok" / "bin" / "grok").exists():
        if env.get("TERM_PROGRAM", "").lower() == "grok":
            return "grok"
    return "generic"


def grok_home() -> Path:
    return Path(os.environ.get("GROK_HOME") or (Path.home() / ".grok")).expanduser()


def skill_source() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "SKILL.md",  # src/self_orch/install.py -> repo
        here.parent / "data" / "SKILL.md",
        DEFAULT_PREFIX / "SKILL.md",
        Path.cwd() / "SKILL.md",
    ]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError("SKILL.md not found; clone https://github.com/PLEE93/self-orch first")


def repo_root_from_skill(skill: Path) -> Path:
    return skill.parent


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_skill(dest_dir: Path, source: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "SKILL.md"
    shutil.copyfile(source, dest)
    return dest


def _upsert_marked_section(path: Path, snippet: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = path.read_text(encoding="utf-8") if path.exists() else ""
    if MARKER_START in body and MARKER_END in body:
        pre = body.split(MARKER_START, 1)[0]
        post = body.split(MARKER_END, 1)[1]
        body = pre.rstrip() + "\n\n" + snippet.strip() + "\n" + post.lstrip()
    else:
        body = (body.rstrip() + "\n\n" + snippet.strip() + "\n") if body.strip() else snippet.strip() + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def skill_targets(agent: str, scope: str, project: Path) -> list[Path]:
    home = Path.home()
    user: list[Path] = []
    proj: list[Path] = []
    if agent == "claude-code":
        user = [home / ".claude" / "skills" / "self-orch"]
        proj = [project / ".claude" / "skills" / "self-orch"]
    elif agent == "codex":
        user = [home / ".codex" / "skills" / "self-orch"]
        proj = [project / ".agents" / "skills" / "self-orch"]
    elif agent == "cursor":
        user = [home / ".cursor" / "skills" / "self-orch"]
        proj = [project / ".cursor" / "skills" / "self-orch"]
    elif agent == "grok":
        user = [grok_home() / "skills" / "self-orch"]
        proj = [project / ".grok" / "skills" / "self-orch"]
    elif agent == "opencode":
        user = [home / ".config" / "opencode" / "skills" / "self-orch"]
        proj = [project / ".opencode" / "skills" / "self-orch"]
    else:
        user = [home / ".local" / "share" / "self-orch" / "skill"]
        proj = [project / ".agents" / "skills" / "self-orch"]
    return user if scope == "user" else proj


def extra_rule_files(agent: str, scope: str, project: Path) -> list[Path]:
    home = Path.home()
    if agent == "claude-code" and scope == "user":
        return [home / ".claude" / "CLAUDE.md"]
    if agent == "codex" and scope == "user":
        return [home / ".codex" / "AGENTS.md"]
    if agent == "codex" and scope == "project":
        return [project / "AGENTS.md"]
    if agent == "cursor" and scope == "project":
        return [project / ".cursor" / "rules" / "self-orch.mdc"]
    if agent == "grok" and scope == "user":
        return [grok_home() / "rules" / "self-orch.md"]
    if agent == "opencode" and scope == "project":
        return [project / "AGENTS.md"]
    if agent == "generic" and scope == "project":
        return [project / "AGENTS.md"]
    return []


_CURSOR_MDC = """\
---
description: Parallel self-orchestration rail. Use for multi-model seats, parliament, self-orch, parallel GLM/Grok workers.
alwaysApply: false
---

You are the governor. Follow the self-orch SKILL.md. Dispatch with:

```bash
self-orch dispatch --spec spec.json
```

If the CLI is missing, run `self-orch install --agent cursor` or `python3 scripts/install.py --agent cursor`.
"""

_GROK_RULE = """\
# self-orch

You are the governor. On complex tasks that need parallel model seats, load the
`self-orch` skill and run `self-orch dispatch --spec spec.json`.

Install (once): `self-orch install --agent grok --scope user`
"""


def ensure_checkout(prefix: Path) -> Path:
    skill = prefix / "SKILL.md"
    if skill.is_file():
        return prefix
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if prefix.exists() and not (prefix / ".git").exists():
        raise SystemExit(f"{prefix} exists and is not a self-orch checkout")
    subprocess.check_call(["git", "clone", "--depth", "1", REPO_URL, str(prefix)])
    return prefix


def ensure_venv(prefix: Path) -> Path:
    venv = prefix / ".venv"
    py = venv / "bin" / "python"
    if not py.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
    pip = venv / "bin" / "pip"
    subprocess.check_call([str(pip), "install", "-q", "-e", str(prefix)])
    return venv / "bin" / "self-orch"


def link_cli(cli: Path) -> Path:
    dest_dir = Path.home() / ".local" / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "self-orch"
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    dest.symlink_to(cli)
    return dest


def key_status() -> dict[str, bool]:
    return {
        "ZAI_API_KEY": bool(os.environ.get("ZAI_API_KEY") or os.environ.get("GLM_API_KEY")),
        "XAI_API_KEY": bool(os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")),
        "OPENAI_API_KEY": bool(os.environ.get("OPENAI_API_KEY")),
    }


def install(
    agent: str,
    scope: str,
    prefix: Path,
    project: Path,
    skip_cli: bool = False,
    dry_run: bool = False,
) -> dict:
    if agent == "auto":
        agent = detect_agent()
    if agent not in AGENTS:
        raise SystemExit(f"unknown agent {agent!r}; choose from {', '.join(AGENTS)}")
    written: list[str] = []
    if dry_run:
        return {
            "agent": agent,
            "scope": scope,
            "skill_dirs": [str(p) for p in skill_targets(agent, scope, project)],
            "rules": [str(p) for p in extra_rule_files(agent, scope, project)],
            "cli": None,
            "keys": key_status(),
        }

    try:
        source = skill_source()
        checkout = repo_root_from_skill(source)
    except FileNotFoundError:
        checkout = ensure_checkout(prefix)
        source = checkout / "SKILL.md"
    if not (checkout / "pyproject.toml").is_file():
        checkout = ensure_checkout(prefix)
        source = checkout / "SKILL.md"
    prefix = checkout
    cli_path = None
    if not skip_cli:
        cli_path = ensure_venv(prefix)
        try:
            cli_path = link_cli(cli_path)
        except OSError:
            pass

    skill_text = source.read_text(encoding="utf-8")
    for dest_dir in skill_targets(agent, scope, project):
        dest = dest_dir / "SKILL.md"
        _write(dest, skill_text)
        written.append(str(dest))

    for rule in extra_rule_files(agent, scope, project):
        if rule.suffix == ".mdc":
            _write(rule, _CURSOR_MDC)
        elif rule.name == "self-orch.md":
            _write(rule, _GROK_RULE)
        else:
            _upsert_marked_section(rule, _AGENTS_SNIPPET)
        written.append(str(rule))

    return {
        "agent": agent,
        "scope": scope,
        "prefix": str(prefix),
        "cli": str(cli_path) if cli_path else None,
        "written": written,
        "keys": key_status(),
    }


def doctor(agent: str | None = None) -> dict:
    # `doctor` is the one place that must report BOTH halves of "can this
    # install actually run": which credential each vendor resolved (an API
    # key OR an already-authenticated coding-agent session), and what each
    # difficulty tier currently points at. Both were unreachable before:
    # tier_table() and credential_status() existed and nothing called them.
    from .auth import credential_status
    from .tiers import tier_table

    agent = agent or detect_agent()
    cli = shutil.which("self-orch")
    version = None
    if cli:
        try:
            version = subprocess.check_output([cli, "--version"], text=True).strip()
        except Exception:
            version = "unrunnable"
    files = []
    home = Path.home()
    candidates = [
        home / ".claude" / "skills" / "self-orch" / "SKILL.md",
        home / ".codex" / "skills" / "self-orch" / "SKILL.md",
        home / ".cursor" / "skills" / "self-orch" / "SKILL.md",
        grok_home() / "skills" / "self-orch" / "SKILL.md",
        home / ".config" / "opencode" / "skills" / "self-orch" / "SKILL.md",
    ]
    for p in candidates:
        files.append({"path": str(p), "present": p.is_file()})
    return {
        "detected_agent": agent,
        "cli": cli,
        "version": version,
        "keys": key_status(),
        "credentials": credential_status(),
        "tiers": tier_table(),
        "skills": files,
        "path_has_local_bin": str(Path.home() / ".local" / "bin") in os.environ.get("PATH", ""),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Install self-orch into this coding agent's env")
    p.add_argument("--agent", default="auto", help="auto, or " + ", ".join(AGENTS))
    p.add_argument("--scope", default="user", choices=["user", "project"])
    p.add_argument("--prefix", default=str(DEFAULT_PREFIX))
    p.add_argument("--project", default=".")
    p.add_argument("--skip-cli", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--doctor", action="store_true")
    args = p.parse_args(argv)

    import json

    if args.doctor:
        json.dump(doctor(None if args.agent == "auto" else args.agent), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    result = install(
        agent=args.agent,
        scope=args.scope,
        prefix=Path(args.prefix).expanduser(),
        project=Path(args.project).expanduser().resolve(),
        skip_cli=args.skip_cli,
        dry_run=args.dry_run,
    )
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    keys = result.get("keys") or {}
    missing = [k for k, ok in keys.items() if not ok]
    if missing:
        print(
            "\nMissing env keys (ask the human, do not invent them): " + ", ".join(missing),
            file=sys.stderr,
        )
    if result.get("cli") and not (result.get("keys") or {}).get("ZAI_API_KEY") and not (result.get("keys") or {}).get("XAI_API_KEY"):
        print("CLI is installed but no model key is set. GLM needs ZAI_API_KEY; Grok needs XAI_API_KEY.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
