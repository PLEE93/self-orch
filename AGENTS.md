# self-orch — governor instructions

This file is for Claude Code, Codex, Cursor, OpenCode, Grok, and any other terminal agent.

You are the governor. `self-orch` is the substrate rail. Read `SKILL.md` for the short contract. Print the long form with:

```bash
self-orch doctrine
```

## Install into this agent

- Claude Code: copy `SKILL.md` into `~/.claude/skills/self-orch/SKILL.md` or add this repo as a skill path. Also paste this file into `CLAUDE.md` if you want it always on.
- Codex / OpenCode: keep this `AGENTS.md` in the project root (already the convention).
- Cursor: `@AGENTS.md` or point `.cursor/rules` at `SKILL.md`.
- Grok Build / other: read `SKILL.md` when the user asks for parallel seats or self-orch.

## Environment

```bash
export ZAI_API_KEY=...    # required for glm-* 
export XAI_API_KEY=...    # required for grok-*
```

Never write API keys into the repo, specs, or commit.

## Dispatch

```bash
self-orch dispatch --spec spec.json [--out result.json]
```

Exit 0 only when `dispatch_state=completed`. Parse stdout JSON. Live seat panels print on stderr.

## Do not

- Do not treat this as Aurelius. There is no MCP, no `aurelius_tools`, no chat backend.
- Do not put secrets in `spec.json`.
- Do not skip orient on a complex turn.
- Do not re-dispatch a turn_id whose results you already have.
